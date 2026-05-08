"""``run_query`` -- the agent loop.

Per the first-principles map (``learnings/openharness-first-principles.md`` §1),
the loop is::

    while True:
        stream = llm.stream(messages)
        parse tool_use blocks
        for each tool_use: check permission, execute, append result
        if stop_reason == "end_turn": break

P2-T4 sub-units land this in stages:

- 4d (this commit): no-tool path. Build request, stream events, exit on
  ``end_turn`` / ``max_tokens`` / ``stop_sequence``. The ``stop_reason ==
  "tool_use"`` branch raises an explicit stub for 4e to clear.
- 4e: 1-tool path with the four recovery flows (validation / not-found /
  denied / tool is_error).
- 4f: multi-turn + ``LoopLimitExceeded`` boundary + programming-error
  propagation.

Per D6.1 the loop exits on ``stop_reason == "end_turn"`` (or any non-tool_use
reason from the API: ``max_tokens`` / ``stop_sequence``); per D6.3 tools execute
serially within a turn (4e onward).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.engine.errors import LoopLimitExceeded
from openharness.protocols import ApiMessageCompleteEvent, ApiMessageRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.engine.context import QueryContext
    from openharness.protocols import ApiStreamEvent, ConversationMessage


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

        # P2-T4.4e fills the tool dispatch path: extract tool_uses, permission
        # check, execute, emit ToolExecution events, append tool results, loop.
        raise NotImplementedError("tool dispatch lands in P2-T4.4e")

    raise LoopLimitExceeded(max_turns=context.max_turns)
