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

import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from openharness.api.errors import OpenHarnessApiError, PromptTooLongFailure
from openharness.engine.errors import LoopLimitExceeded
from openharness.engine.messages import (
    append_assistant_message,
    append_tool_results,
    drop_oldest_tool_pair,
    extract_tool_uses,
)
from openharness.errors import LoopError, ToolError
from openharness.hooks import (
    OnErrorContext,
    PostApiCallContext,
    PostToolUseContext,
    PreApiCallContext,
    PreToolUseContext,
    execute_hook_chain,
)
from openharness.observability import (
    bind_agent_depth,
    bind_run,
    bind_turn,
    get_logger,
    sanitize_command,
    sanitize_path,
)
from openharness.permissions import Decision, PermissionMode
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ConversationCompleteEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolResultBlock,
)
from openharness.tools.base import ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.engine.context import QueryContext
    from openharness.protocols import (
        ApiStreamEvent,
        ConversationMessage,
        ToolUseBlock,
    )


logger = get_logger("engine")


# P4-T3.3c (D14.5):reactive prompt-too-long truncation is bounded.
# After this many drop-and-retry cycles within one turn, surface the
# underlying ``PromptTooLongFailure`` to the caller — at that point the
# prompt is structurally too large for any one-shot recovery.
_REACTIVE_TRUNCATE_MAX = 3


def _sanitize_tool_input(tool_input: dict[str, Any], cwd: Path) -> dict[str, Any]:
    """Apply field-specific sanitization to ``tool_input`` before logging.

    Per Three-Axis 轴 2 (5b): semantic redaction is the call-site's job.
    ``sanitize_processor`` only knows about key names + value token shapes;
    fields whose value is a filesystem path / shell command need a helper
    that understands the field semantics.

    - ``path`` / ``file_path`` → :func:`sanitize_path` (cwd-relative or redacted)
    - ``command`` → :func:`sanitize_command` (first token + length)

    Other fields pass through; ``sanitize_processor`` still catches
    credentials by key (``api_key`` / ``password`` / ...) and embedded tokens
    (``sk-...`` / JWT / ...) before the renderer.
    """
    out = dict(tool_input)
    for path_key in ("path", "file_path"):
        if path_key in out and isinstance(out[path_key], str):
            out[path_key] = sanitize_path(out[path_key], cwd)
    if "command" in out and isinstance(out["command"], str):
        out["command"] = sanitize_command(out["command"])
    return out


async def run_query(
    initial_messages: list[ConversationMessage],
    context: QueryContext,
) -> AsyncIterator[ApiStreamEvent]:
    """Drive the agent loop, yielding stream events until ``end_turn`` or the
    ``max_turns`` cap is reached.

    Yields events in the order they arrive: API events from
    ``stream_message`` (retry / text-delta / message-complete) interleaved
    with engine events from tool dispatch (started / completed).

    P3-T5.5c: every log call inside the generator body carries ``run_id``
    (auto-minted by :func:`bind_run`) and, inside the per-turn block,
    ``turn_id`` (1-indexed for human reading). The 4 log points landed here:

    - ``turn_start`` (info) — top of each turn
    - ``tool_dispatch`` (info) — before each ``_dispatch_one`` call
    - ``tool_complete`` (info) — after ``_dispatch_one``, with ``duration_ms``
      + ``output_len`` (output content itself is **not** logged per D13.6)
    - ``loop_limit_exceeded`` (warning) — before raising ``LoopLimitExceeded``
    """
    # Defensive copy: the caller's list must not be mutated even though the
    # messages helpers (engine.messages) all return new lists.
    messages = list(initial_messages)

    # P6-T4 (D16.7):``bind_run`` auto-detects nested invocations (sub-agent's
    # ``run_query`` re-enters within parent's bound context) and stashes the
    # parent's run_id as ``parent_run_id`` on the new bound layer. ``bind_agent_depth``
    # surfaces ``QueryContext.agent_depth`` on every event so trace consumers
    # can stitch the parent ↔ sub-agent tree via a self-join on
    # ``run_id ↔ parent_run_id`` and filter by depth.
    with bind_run(), bind_agent_depth(context.agent_depth):
        for _turn in range(context.max_turns):
            with bind_turn(_turn + 1):  # 1-indexed: humans count turns from 1
                logger.info(
                    "turn_start",
                    model=context.model,
                    max_tokens=context.max_tokens,
                )

                request = ApiMessageRequest(
                    model=context.model,
                    max_tokens=context.max_tokens,
                    system=context.system_prompt or None,
                    messages=messages,
                    tools=context.tool_registry.to_api_schema() or None,
                )

                # P3-T4.4g:PreApiCall hook chain — can deny the whole turn or
                # modify the request (e.g., memory injection in Phase 4).
                pre_api_result = await execute_hook_chain(
                    context.hook_registry,
                    "PreApiCall",
                    PreApiCallContext(request=request, turn=_turn),
                )
                if pre_api_result is not None:
                    if pre_api_result.decision == "deny":
                        raise LoopError(
                            f"PreApiCall hook denied turn {_turn}: "
                            f"{pre_api_result.message or 'unspecified'}"
                        )
                    if (
                        pre_api_result.decision == "modify"
                        and pre_api_result.new_request is not None
                        and isinstance(pre_api_result.new_request, ApiMessageRequest)
                    ):
                        request = pre_api_result.new_request

                # P4-T3.3c:reactive truncation loop. On PromptTooLongFailure,
                # drop the oldest tool_use/tool_result pair from ``messages``,
                # rebuild ``request``, retry — bounded by ``_REACTIVE_TRUNCATE_MAX``.
                # Other api errors (auth / 5xx / generic 400 / etc.) propagate
                # immediately via the outer except.
                complete_event: ApiMessageCompleteEvent | None = None
                truncate_attempts = 0
                while True:
                    try:
                        complete_event = None
                        async for event in context.api_client.stream_message(request):
                            yield event
                            if isinstance(event, ApiMessageCompleteEvent):
                                complete_event = event
                        break  # success — exit retry loop
                    except PromptTooLongFailure as ptl_exc:
                        new_messages = drop_oldest_tool_pair(messages)
                        # Two stop conditions:bounded retries OR nothing to drop.
                        if truncate_attempts >= _REACTIVE_TRUNCATE_MAX or len(new_messages) == len(
                            messages
                        ):
                            await execute_hook_chain(
                                context.hook_registry,
                                "OnError",
                                OnErrorContext(exception=ptl_exc, where="api"),
                            )
                            raise
                        truncate_attempts += 1
                        # 10th log event in the observability inventory.
                        # WARNING because compaction firing means we're at
                        # a budget edge — useful default-level signal.
                        logger.warning(
                            "reactive_truncate",
                            turn=_turn + 1,
                            attempt=truncate_attempts,
                            dropped_count=len(messages) - len(new_messages),
                        )
                        messages = new_messages
                        # Rebuild request with the truncated messages list.
                        # Other request fields (system / tools / max_tokens)
                        # don't change between truncation retries.
                        request = ApiMessageRequest(
                            model=context.model,
                            max_tokens=context.max_tokens,
                            system=context.system_prompt or None,
                            messages=messages,
                            tools=context.tool_registry.to_api_schema() or None,
                        )
                    except OpenHarnessApiError as exc:
                        await execute_hook_chain(
                            context.hook_registry,
                            "OnError",
                            OnErrorContext(exception=exc, where="api"),
                        )
                        raise

                # ``stream_message`` always emits exactly one terminal event
                # (api/client.py docstring); if it didn't, we have a contract
                # bug and the assertion surfaces it loudly rather than
                # silently looping.
                assert complete_event is not None, "stream_message yielded no terminal event"

                # P3-T4.4g:PostApiCall hook chain (observe in Phase 3 — modify
                # is P4+).
                await execute_hook_chain(
                    context.hook_registry,
                    "PostApiCall",
                    PostApiCallContext(
                        request=request,
                        response_message=complete_event.message,
                        usage=complete_event.usage,
                        stop_reason=complete_event.stop_reason,
                        turn=_turn,
                    ),
                )

                if complete_event.stop_reason != "tool_use":
                    # P6+-T1 (D24.2): emit the final conversation
                    # state as the LAST event before exit so callers
                    # (oh chat REPL) can carry forward multi-turn
                    # history. The list includes the just-completed
                    # assistant message so the next user turn sees
                    # the full exchange.
                    final_messages = append_assistant_message(
                        messages, list(complete_event.message.content)
                    )
                    yield ConversationCompleteEvent(messages=final_messages)
                    return  # end_turn / max_tokens / stop_sequence -> exit

                # Tool dispatch (D6.3 serial). Per D10.4, four recovery paths
                # all produce ``is_error=True`` results that go back to the
                # LLM: tool-not-found / Pydantic validation / permission
                # denied / tool's own is_error. Programming exceptions
                # propagate (D8.5 / D10.5).
                tool_uses = extract_tool_uses(complete_event.message)
                # P6-T2 (D16.8): pass ``context`` through so ``SpawnAgent``
                # can build sub-context via ``dataclasses.replace``. This is
                # the ONLY engine dispatch change Phase 6 introduces; every
                # other tool ignores ``parent_query`` and behaves identically.
                # P7-T2 (D17.3 / D17.4): pass through ``execution_env`` so
                # ``BashTool`` can delegate shell commands to the configured
                # substrate (HostExecution by default; SandboxExecution in
                # Phase 7b). Every other tool ignores the field.
                exec_context = ToolExecutionContext(
                    cwd=context.cwd,
                    parent_query=context,
                    execution_env=context.execution_env,
                )
                tool_results: list[ToolResultBlock] = []

                for tool_use in tool_uses:
                    yield ToolExecutionStartedEvent(
                        tool_use_id=tool_use.id,
                        tool_name=tool_use.name,
                        tool_input=tool_use.input,
                    )

                    # 5c: log dispatch-start with sanitized input + start clock
                    # for duration_ms on tool_complete.
                    # P5-T5: trust_source from tool.trust_source for the
                    # observability trail (local / trusted-server /
                    # strict-default). Lookup uses try/except since
                    # _dispatch_one will also do a registry.get() — the
                    # cost of one extra lookup is negligible.
                    try:
                        trust_source = context.tool_registry.get(tool_use.name).trust_source
                    except KeyError:
                        trust_source = "unknown"
                    logger.info(
                        "tool_dispatch",
                        tool=tool_use.name,
                        tool_use_id=tool_use.id,
                        input=_sanitize_tool_input(tool_use.input, context.cwd),
                        trust_source=trust_source,
                    )
                    t0 = time.monotonic()

                    # D12.5: DRY_RUN bypasses both permission_checker and
                    # execute(), emitting a synthetic "would call X with Y"
                    # result so the LLM sees the loop progressing but no side
                    # effects occur.
                    if context.permission_mode is PermissionMode.DRY_RUN:
                        output = f"would call {tool_use.name} with {tool_use.input}"
                        is_error = False
                    else:
                        output, is_error = await _dispatch_one(tool_use, context, exec_context)

                    # 5c: log dispatch-complete. Per D13.6, output content
                    # itself is NOT logged (size + PII risk); only its length.
                    duration_ms = round((time.monotonic() - t0) * 1000, 2)
                    logger.info(
                        "tool_complete",
                        tool=tool_use.name,
                        tool_use_id=tool_use.id,
                        is_error=is_error,
                        duration_ms=duration_ms,
                        output_len=len(output),
                    )

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

                # Append the assistant turn (with the tool_use block) and the
                # bundled tool_results -- mirrors the loop pseudocode in
                # messages.py docstring.
                messages = append_assistant_message(messages, list(complete_event.message.content))
                messages = append_tool_results(messages, tool_results)

        # Fell through max_turns without an end_turn:loop budget exhausted.
        logger.warning("loop_limit_exceeded", max_turns=context.max_turns)
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
    - Permission checker returns ``Decision.DENY`` or ``Decision.ASK`` 不通过
    - PreToolUse hook returns deny
    - Tool itself returns ``ToolResult(is_error=True)``

    P3-T4.4f:PreToolUse hooks run between AuthZ and execute;PostToolUse
    after execute and before LLM sees result. Hook crashes wrap as
    ``ToolError`` after firing OnError chain (one-level).
    """
    try:
        tool = context.tool_registry.get(tool_use.name)
    except KeyError:
        return f"tool not found: {tool_use.name}", True

    try:
        args = tool.input_model.model_validate(tool_use.input)
    except ValidationError as exc:
        return f"invalid input for {tool_use.name}: {exc}", True

    permission = context.permission_checker.evaluate(tool_use.name, args, exec_context)
    if permission.decision is Decision.DENY:
        reason = permission.reason or tool_use.name
        return f"permission denied: {reason}", True
    if permission.decision is Decision.ASK:
        # Three-Axis G:ASK + mode 解释决定终局。
        # - AUTO: 用户已经预先信任,treat as ALLOW
        # - DEFAULT (and any future modes): fail-safe to DENY with hint;
        #   Phase 4+ replaces this with an interactive prompt.
        if context.permission_mode is PermissionMode.AUTO:
            pass  # fall through to execute
        else:
            reason = permission.reason or tool_use.name
            return (
                f"permission denied (requires confirmation): {reason}; rerun with --auto to allow",
                True,
            )

    # PreToolUse hook chain — can deny / modify input / observe.
    # Hooks run AFTER AuthZ so they can't bypass framework safety baseline.
    pre_ctx = PreToolUseContext(
        tool_name=tool_use.name,
        tool_use_id=tool_use.id,
        tool_input=dict(tool_use.input),
        exec_context=exec_context,
    )
    pre_result = await execute_hook_chain(context.hook_registry, "PreToolUse", pre_ctx)
    current_input = tool_use.input
    if pre_result is not None:
        if pre_result.decision == "deny":
            return f"hook denied: {pre_result.message or tool_use.name}", True
        if pre_result.decision == "modify" and pre_result.new_input is not None:
            current_input = pre_result.new_input
            # Re-validate the modified input against the tool's schema.
            try:
                args = tool.input_model.model_validate(current_input)
            except ValidationError as exc:
                return f"hook-modified input invalid for {tool_use.name}: {exc}", True

    # Execute the tool. Programming errors fire OnError + wrap as ToolError.
    try:
        result = await tool.execute(args, exec_context)
    except Exception as exc:
        await execute_hook_chain(
            context.hook_registry,
            "OnError",
            OnErrorContext(exception=exc, where="tool", tool_name=tool_use.name),
        )
        raise ToolError(f"tool {tool_use.name} crashed: {exc}") from exc

    # PostToolUse hook chain — can modify output / metadata / is_error.
    post_ctx = PostToolUseContext(
        tool_name=tool_use.name,
        tool_use_id=tool_use.id,
        tool_input=current_input,
        exec_context=exec_context,
        result=result,
    )
    post_result = await execute_hook_chain(context.hook_registry, "PostToolUse", post_ctx)
    if post_result is not None and post_result.decision == "modify":
        result = ToolResult(
            output=post_result.new_output if post_result.new_output is not None else result.output,
            is_error=post_result.new_is_error
            if post_result.new_is_error is not None
            else result.is_error,
            metadata=post_result.new_metadata
            if post_result.new_metadata is not None
            else result.metadata,
        )

    return result.output, result.is_error
