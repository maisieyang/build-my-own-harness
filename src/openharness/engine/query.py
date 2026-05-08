"""``run_query`` -- the agent loop.

Per the first-principles map (``learnings/openharness-first-principles.md`` §1),
the loop is::

    while True:
        stream = llm.stream(messages)
        parse tool_use blocks
        for each tool_use: check permission, execute, append result
        if stop_reason == "end_turn": break

P2-T4 sub-units land this in stages:

- 4d: no-tool path. Build request, stream events, exit on ``end_turn`` /
  ``max_tokens`` / ``stop_sequence``.
- 4e (this commit): 1-tool path with the four recovery flows (validation /
  not-found / denied / tool is_error). Tool dispatch is serial within a turn
  (D6.3); each tool emits Started + Completed events around its execution.
- 4f: multi-turn + ``LoopLimitExceeded`` boundary + programming-error
  propagation.

Per D6.1 the loop exits on ``stop_reason == "end_turn"`` (or any non-tool_use
reason from the API: ``max_tokens`` / ``stop_sequence``); per D6.3 tools execute
serially within a turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from openharness.engine.errors import LoopLimitExceeded
from openharness.engine.messages import (
    append_assistant_message,
    append_tool_results,
    extract_tool_uses,
)
from openharness.permissions import Decision
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolResultBlock,
)
from openharness.tools.base import ToolExecutionContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.engine.context import QueryContext
    from openharness.protocols import (
        ApiStreamEvent,
        ConversationMessage,
        ToolUseBlock,
    )


async def run_query(
    initial_messages: list[ConversationMessage],
    context: QueryContext,
) -> AsyncIterator[ApiStreamEvent]:
    """Drive the agent loop, yielding stream events until ``end_turn`` or the
    ``max_turns`` cap is reached.

    Yields events in the order they arrive: API events from
    ``stream_message`` (retry / text-delta / message-complete) interleaved
    with engine events from tool dispatch (started / completed) once 4e+
    fills the tool path.
    """
    # Defensive copy: the caller's list must not be mutated even though the
    # messages helpers (engine.messages) all return new lists.
    messages = list(initial_messages)

    for _turn in range(context.max_turns):
        request = ApiMessageRequest(
            model=context.model,
            max_tokens=context.max_tokens,
            system=context.system_prompt or None,
            messages=messages,
            tools=context.tool_registry.to_api_schema() or None,
        )

        complete_event: ApiMessageCompleteEvent | None = None
        async for event in context.api_client.stream_message(request):
            yield event
            if isinstance(event, ApiMessageCompleteEvent):
                complete_event = event

        # ``stream_message`` always emits exactly one terminal event
        # (api/client.py docstring); if it didn't, we have a contract bug
        # and the assertion surfaces it loudly rather than silently looping.
        assert complete_event is not None, "stream_message yielded no terminal event"

        if complete_event.stop_reason != "tool_use":
            return  # end_turn / max_tokens / stop_sequence -> clean exit

        # Tool dispatch (D6.3 serial). Per D10.4, four recovery paths all
        # produce ``is_error=True`` results that go back to the LLM:
        # tool-not-found / Pydantic validation / permission denied / tool's
        # own is_error. Programming exceptions propagate (D8.5 / D10.5).
        tool_uses = extract_tool_uses(complete_event.message)
        exec_context = ToolExecutionContext(cwd=context.cwd)
        tool_results: list[ToolResultBlock] = []

        for tool_use in tool_uses:
            yield ToolExecutionStartedEvent(
                tool_use_id=tool_use.id,
                tool_name=tool_use.name,
                tool_input=tool_use.input,
            )
            output, is_error = await _dispatch_one(tool_use, context, exec_context)
            yield ToolExecutionCompletedEvent(
                tool_use_id=tool_use.id,
                tool_name=tool_use.name,
                output=output,
                is_error=is_error,
            )
            tool_results.append(
                ToolResultBlock(
                    tool_use_id=tool_use.id,
                    content=output,
                    is_error=is_error,
                ),
            )

        # Append the assistant turn (with the tool_use block) and the bundled
        # tool_results -- mirrors the loop pseudocode in messages.py docstring.
        messages = append_assistant_message(messages, list(complete_event.message.content))
        messages = append_tool_results(messages, tool_results)

    raise LoopLimitExceeded(max_turns=context.max_turns)


async def _dispatch_one(
    tool_use: ToolUseBlock,
    context: QueryContext,
    exec_context: ToolExecutionContext,
) -> tuple[str, bool]:
    """Run a single tool dispatch. Returns ``(output, is_error)``.

    Recovery paths (all return ``is_error=True``, output names the failure):
    - Tool not in registry
    - Pydantic ValidationError on the tool's input model
    - Permission checker returns ``Decision.DENY``
    - Tool itself returns ``ToolResult(is_error=True)``

    Programming errors (anything ``execute`` raises) propagate -- not caught.
    """
    try:
        tool = context.tool_registry.get(tool_use.name)
    except KeyError:
        return f"tool not found: {tool_use.name}", True

    try:
        args = tool.input_model.model_validate(tool_use.input)
    except ValidationError as exc:
        return f"invalid input for {tool_use.name}: {exc}", True

    decision = context.permission_checker.evaluate(tool_use.name, args, exec_context)
    if decision is Decision.DENY:
        return f"permission denied: {tool_use.name}", True

    result = await tool.execute(args, exec_context)
    return result.output, result.is_error
