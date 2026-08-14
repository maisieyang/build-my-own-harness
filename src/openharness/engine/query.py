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

import itertools
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from openharness.api.errors import OpenHarnessApiError, PromptTooLongFailure
from openharness.engine.errors import AutonomousBoundaryError, LoopLimitExceeded
from openharness.engine.messages import (
    append_assistant_message,
    append_tool_results,
    collect_turn_metadata,
    drop_oldest_tool_pair,
    extract_tool_uses,
)
from openharness.errors import LoopError, ToolError
from openharness.execution import BoundaryViolation, OneShotOverlaySession, SandboxUnavailableError
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
from openharness.permissions import (
    ActionDenyKind,
    DenyResult,
    ExecutionPosture,
    ExternalToolMode,
    LocalBoundaryEvidence,
    ParkedContinuation,
    ParkedControllerState,
    PermissionDelta,
    PermissionDeltaKind,
    PermissionDeltaRequest,
    PermissionFilesystemAccess,
    PermissionResolutionStatus,
)
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    BoundaryViolationEvent,
    ConversationCompleteEvent,
    PermissionParkedEvent,
    TextBlock,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.services.compact import auto_compact_if_needed
from openharness.tools.base import (
    BaseTool,
    ExecutionDomain,
    ExternalEffectKind,
    ToolExecutionContext,
    ToolResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.engine.context import QueryContext
    from openharness.protocols import (
        ApiStreamEvent,
        ConversationMessage,
    )


logger = get_logger("engine")


# P4-T3.3c (D14.5):reactive prompt-too-long truncation is bounded.
# After this many drop-and-retry cycles within one turn, surface the
# underlying ``PromptTooLongFailure`` to the caller — at that point the
# prompt is structurally too large for any one-shot recovery.
_REACTIVE_TRUNCATE_MAX = 3


@dataclass(frozen=True)
class _DispatchOutcome:
    output: str
    is_error: bool
    metadata: dict[str, Any]


def _outcome(value: tuple[str, bool]) -> _DispatchOutcome:
    return _DispatchOutcome(output=value[0], is_error=value[1], metadata={})


@dataclass(frozen=True)
class _DispatchBatchComplete:
    tool_results: tuple[ToolResultBlock, ...]
    parked_index: int | None = None


async def _dispatch_tool_batch(
    tool_uses: tuple[ToolUseBlock, ...],
    context: QueryContext,
    *,
    start_index: int,
    initial_results: tuple[ToolResultBlock, ...],
    authorization_context: tuple[str, ...],
) -> AsyncIterator[ApiStreamEvent | _DispatchBatchComplete]:
    """Dispatch a model-selected tool batch while preserving interruption position."""
    exec_context = ToolExecutionContext(
        cwd=context.cwd,
        parent_query=context,
        execution_env=context.execution_env,
        sandbox_session=context.sandbox_session,
    )
    tool_results = list(initial_results)
    for tool_index in range(start_index, len(tool_uses)):
        tool_use = tool_uses[tool_index]
        yield ToolExecutionStartedEvent(
            tool_use_id=tool_use.id,
            tool_name=tool_use.name,
            tool_input=tool_use.input,
        )
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
        outcome = _DispatchOutcome(output="", is_error=False, metadata={})
        if context.execution_posture is ExecutionPosture.DRY_RUN:
            output = f"would call {tool_use.name} with {tool_use.input}"
            is_error = False
        else:
            outcome = await _dispatch_one(
                tool_use,
                context,
                exec_context,
                authorization_context=authorization_context,
            )
            output, is_error = outcome.output, outcome.is_error
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
        violation = outcome.metadata.get("boundary_violation")
        if isinstance(violation, dict):
            dimension = violation.get("dimension")
            requested = violation.get("requested")
            evidence = violation.get("evidence")
            if (
                isinstance(dimension, str)
                and isinstance(requested, str)
                and isinstance(evidence, str)
            ):
                yield BoundaryViolationEvent(
                    tool_use_id=tool_use.id,
                    tool_name=tool_use.name,
                    dimension=dimension,
                    requested=requested,
                    evidence=evidence,
                )
        if (
            context.permission_runtime is not None
            and context.permission_runtime.parked_request is not None
        ):
            # The visible permission error is control-plane evidence. Do not
            # append it (or synthetic skipped calls) to model conversation.
            yield _DispatchBatchComplete(tuple(tool_results), parked_index=tool_index)
            return
        tool_results.append(
            ToolResultBlock(
                tool_use_id=tool_use.id,
                content=output,
                is_error=is_error,
            )
        )
    yield _DispatchBatchComplete(tuple(tool_results))


def _build_parked_continuation(
    *,
    context: QueryContext,
    messages: list[ConversationMessage],
    assistant_message: ConversationMessage,
    tool_uses: tuple[ToolUseBlock, ...],
    tool_results: tuple[ToolResultBlock, ...],
    parked_index: int,
) -> ParkedContinuation:
    runtime = context.permission_runtime
    if runtime is None or runtime.parked_request is None:
        raise ValueError("cannot build continuation without a parked permission request")
    dispatch_tool_uses = list(tool_uses)
    original = dispatch_tool_uses[parked_index]
    dispatch_tool_uses[parked_index] = ToolUseBlock(
        id=original.id,
        name=original.name,
        input=runtime.parked_request.final_arguments,
    )
    controller_mode = context.controller_mode
    if controller_mode == "default" and context.controller_goal_condition is not None:
        controller_mode = "goal"
    continuation = ParkedContinuation.create(
        request=runtime.parked_request,
        messages=tuple(messages),
        assistant_message=assistant_message,
        tool_uses=tuple(dispatch_tool_uses),
        completed_tool_results=tool_results,
        next_tool_index=parked_index,
        controller=ParkedControllerState(
            mode=controller_mode,
            goal_condition=context.controller_goal_condition,
        ),
    )
    runtime.bind_continuation(continuation)
    return continuation


def _permission_parked_event(
    *,
    context: QueryContext,
    continuation: ParkedContinuation,
) -> PermissionParkedEvent:
    runtime = context.permission_runtime
    if runtime is None or runtime.parked_request is None:
        raise ValueError("permission runtime is not parked")
    parked = runtime.parked_request
    local_evidence = (
        parked.enforcement if isinstance(parked.enforcement, LocalBoundaryEvidence) else None
    )
    return PermissionParkedEvent(
        request_id=parked.request_id,
        tool_use_id=parked.tool_use_id,
        tool_name=parked.tool_name,
        delta_kind=parked.delta.kind.value,
        delta_value=parked.delta.value,
        profile_fingerprint=parked.profile_fingerprint,
        enforcement=parked.enforcement.model_dump(mode="json"),
        boundary_fingerprint=(
            local_evidence.boundary_fingerprint if local_evidence is not None else None
        ),
        backend=local_evidence.backend if local_evidence is not None else None,
        backend_fingerprint=(
            local_evidence.backend_fingerprint if local_evidence is not None else None
        ),
        final_arguments=parked.final_arguments,
        data_sources=parked.data_sources,
        data_destinations=parked.data_destinations,
        boundary_facts=(local_evidence.boundary_facts if local_evidence is not None else None),
        reason=runtime.parked_reason or "human review required",
        review_status=(
            runtime.parked_review_status.value if runtime.parked_review_status else "manual"
        ),
        continuation=continuation,
        messages=list(continuation.messages),
    )


async def _maybe_write_turn_end_metadata(
    context: QueryContext,
    final_messages: list[ConversationMessage],
) -> None:
    """Write the per-turn snapshot with optional LLM-authored focus state.

    When ``llm_focus_state_enabled``
    (Phase 13 D31.7) the engine awaits a secondary LLM call
    before the snapshot write, then injects the inferred focus
    state into ``tool_metadata.task_focus_state`` (replacing the
    None placeholder).

    All failures caught + WARN-logged. Turn still returns success.
    """
    if not context.snapshot_enabled:
        return  # no consumer wired — skip the producer too

    tool_metadata = collect_turn_metadata(final_messages)

    # P13-T3 (D31.7): optional LLM-authored focus state.
    if context.llm_focus_state_enabled:
        from openharness.services.focus_state import infer_focus_state

        focus_model = context.llm_focus_state_model or context.model
        focus_state = await infer_focus_state(
            messages=final_messages,
            api_client=context.api_client,
            model=focus_model,
        )
        # Overwrite the None placeholder with the inferred values.
        tool_metadata["task_focus_state"] = focus_state.to_dict()

    from openharness.services.snapshot import write_session_snapshot

    try:
        write_session_snapshot(
            cwd=context.cwd,
            tool_metadata=tool_metadata,
            messages=final_messages,
            context=context,
            history_max_count=context.snapshot_history_max_count,
            history_max_age_days=context.snapshot_history_max_age_days,
        )
    except OSError as exc:
        logger.warning("snapshot_write_failed", error=str(exc))


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


def extract_authorization_context(
    messages: list[ConversationMessage],
) -> tuple[str, ...]:
    """Extract human-authored or human-derived scope, never agent feedback."""
    context: list[str] = []
    for message in messages:
        if message.role != "user":
            continue
        for block in message.content:
            if not isinstance(block, TextBlock) or not block.text.strip():
                continue
            if block.text.startswith(("[goal set] ", "[goal checker] ", "[permission decision] ")):
                continue
            context.append(block.text)
    return tuple(context)


async def run_query(
    initial_messages: list[ConversationMessage],
    context: QueryContext,
    *,
    continuation: ParkedContinuation | None = None,
) -> AsyncIterator[ApiStreamEvent]:
    """Drive the agent loop until a non-tool response, or until an optional
    caller-selected ``max_turns`` cap is reached.

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
    # This gate precedes compaction and the first API request: autonomous
    # local/delegated execution must never ask a model to act before its
    # runtime boundary has proved complete coverage.
    _require_autonomous_boundary(context)

    # Defensive copy: the caller's list must not be mutated even though the
    # messages helpers (engine.messages) all return new lists.
    messages = list(initial_messages)
    if continuation is not None:
        runtime = context.permission_runtime
        if runtime is None:
            raise ValueError("permission continuation requires a permission runtime")
        continuation.validate_for(runtime)
        if runtime.parked_continuation != continuation:
            raise ValueError("permission continuation is not active")
        if not runtime.decision_ready_for_continuation:
            raise ValueError("permission continuation has no consumed human decision")
        messages = list(continuation.messages)
    authorization_context = (
        context.authorization_context
        if context.authorization_context
        else extract_authorization_context(messages)
    )

    # P6-T4 (D16.7):``bind_run`` auto-detects nested invocations (sub-agent's
    # ``run_query`` re-enters within parent's bound context) and stashes the
    # parent's run_id as ``parent_run_id`` on the new bound layer. ``bind_agent_depth``
    # surfaces ``QueryContext.agent_depth`` on every event so trace consumers
    # can stitch the parent ↔ sub-agent tree via a self-join on
    # ``run_id ↔ parent_run_id`` and filter by depth.
    with bind_run(), bind_agent_depth(context.agent_depth):
        max_turns = context.max_turns
        turn_indexes = itertools.count() if max_turns is None else range(max_turns)
        for _turn in turn_indexes:
            with bind_turn(_turn + 1):  # 1-indexed: humans count turns from 1
                logger.info(
                    "turn_start",
                    model=context.model,
                    max_tokens=context.max_tokens,
                )

                # Resume the exact saved dispatch before any model request.
                # This reconnects the original tool result path without a
                # synthetic user message or a reconstruction model turn.
                if continuation is not None:
                    resumed_batch: _DispatchBatchComplete | None = None
                    async for dispatch_event in _dispatch_tool_batch(
                        continuation.tool_uses,
                        context,
                        start_index=continuation.next_tool_index,
                        initial_results=continuation.completed_tool_results,
                        authorization_context=authorization_context,
                    ):
                        if isinstance(dispatch_event, _DispatchBatchComplete):
                            resumed_batch = dispatch_event
                        else:
                            yield dispatch_event
                    assert resumed_batch is not None
                    if resumed_batch.parked_index is not None:
                        # A restored request that immediately parks again did
                        # not consume the exact decision; fail closed instead
                        # of replacing the preserved continuation.
                        raise ValueError("permission continuation did not consume exact decision")
                    messages = append_assistant_message(
                        messages,
                        list(continuation.assistant_message.content),
                    )
                    messages = append_tool_results(messages, list(resumed_batch.tool_results))
                    runtime = context.permission_runtime
                    assert runtime is not None
                    runtime.consume_continuation(continuation)
                    continuation = None
                    # Continue this loop iteration with the ordinary model
                    # request that consumes the newly attached Tool Results.

                # Proactive semantic compact runs BEFORE PreApiCall hooks so
                # hooks see the condensed Working Set. Older successful Read
                # and Grep results may be cleared in the summarizer's private
                # input; user/assistant text and the original recent tail are
                # never deterministically rewritten. Reactive PTL retry below
                # remains the last-resort safety net.
                if context.compact_enabled:
                    messages, compact_result = await auto_compact_if_needed(
                        messages,
                        model=context.model,
                        api_client=context.api_client,
                        enabled=True,
                        threshold_ratio=context.compact_threshold_ratio,
                        full_compact_max_tokens=context.compact_full_max_tokens,
                        full_compact_timeout_s=context.compact_full_timeout_s,
                    )
                    if compact_result.compact_kind != "none":
                        logger.info(
                            "auto_compact",
                            kind=compact_result.compact_kind,
                            applied_levels=list(compact_result.applied_levels),
                            original_tokens=compact_result.original_tokens,
                            final_tokens=compact_result.final_tokens,
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
                        # P11-T6 (D29.7): re-fire PreApiCall hooks flagged
                        # ``re_run_on_reactive_rebuild=True``. Closes
                        # Phase 4 retro §6 — memory-injection hooks (and
                        # any other PreApiCall hook whose effect must
                        # survive the rebuild) opt in via the flag and
                        # see the freshly-truncated request. Hooks not
                        # flagged are NOT re-run (default behaviour
                        # preserved). PostToolUse / PreToolUse never
                        # re-run here — the only event that gets a
                        # second-chance after PTL is PreApiCall.
                        rerun_hooks = context.hook_registry.get_reactive_rerun("PreApiCall")
                        if rerun_hooks:
                            rerun_result = await execute_hook_chain(
                                context.hook_registry,
                                "PreApiCall",
                                PreApiCallContext(request=request, turn=_turn),
                                hook_subset=rerun_hooks,
                            )
                            if rerun_result is not None:
                                if rerun_result.decision == "deny":
                                    raise LoopError(
                                        f"PreApiCall hook denied rebuilt turn "
                                        f"{_turn}: {rerun_result.message or 'unspecified'}"
                                    ) from ptl_exc
                                if (
                                    rerun_result.decision == "modify"
                                    and rerun_result.new_request is not None
                                    and isinstance(rerun_result.new_request, ApiMessageRequest)
                                ):
                                    request = rerun_result.new_request
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
                    # (oh REPL) can carry forward multi-turn
                    # history. The list includes the just-completed
                    # assistant message so the next user turn sees
                    # the full exchange.
                    final_messages = append_assistant_message(
                        messages, list(complete_event.message.content)
                    )
                    # P12-T1 (D30.6 + D30.9): turn-end snapshot writer.
                    # Errors are caught and logged
                    # — turn still emits ``ConversationCompleteEvent``
                    # so failure isolation matches the extract contract.
                    await _maybe_write_turn_end_metadata(context, final_messages)
                    yield ConversationCompleteEvent(messages=final_messages)
                    return  # end_turn / max_tokens / stop_sequence -> exit

                tool_uses = tuple(extract_tool_uses(complete_event.message))
                batch_complete: _DispatchBatchComplete | None = None
                async for dispatch_event in _dispatch_tool_batch(
                    tool_uses,
                    context,
                    start_index=0,
                    initial_results=(),
                    authorization_context=authorization_context,
                ):
                    if isinstance(dispatch_event, _DispatchBatchComplete):
                        batch_complete = dispatch_event
                    else:
                        yield dispatch_event
                assert batch_complete is not None
                if batch_complete.parked_index is not None:
                    continuation = _build_parked_continuation(
                        context=context,
                        messages=messages,
                        assistant_message=complete_event.message,
                        tool_uses=tool_uses,
                        tool_results=batch_complete.tool_results,
                        parked_index=batch_complete.parked_index,
                    )
                    # Save the typed pre-dispatch history plus continuation
                    # atomically through the existing turn-end writer.
                    await _maybe_write_turn_end_metadata(context, messages)
                    yield _permission_parked_event(context=context, continuation=continuation)
                    return
                messages = append_assistant_message(messages, list(complete_event.message.content))
                messages = append_tool_results(messages, list(batch_complete.tool_results))

        # Only a caller-supplied finite range can fall through. With no cap,
        # the model owns loop termination by returning a clean non-tool turn.
        assert max_turns is not None
        logger.warning("loop_limit_exceeded", max_turns=max_turns)
        # Persist the completed assistant/tool exchanges in the snapshot.
        # Interactive callers pause and return control without treating this
        # forced stop as completion; private bounded callers still receive
        # LoopLimitExceeded as the safety failure it has always been.
        await _maybe_write_turn_end_metadata(context, messages)
        raise LoopLimitExceeded(max_turns=max_turns, messages=messages)


async def _dispatch_one(
    tool_use: ToolUseBlock,
    context: QueryContext,
    exec_context: ToolExecutionContext,
    *,
    authorization_context: tuple[str, ...] = (),
) -> _DispatchOutcome:
    """Run a single tool dispatch. Returns ``(output, is_error)``.

    Recovery paths (all return ``is_error=True``, output names the failure):
    - Tool not in registry
    - Pydantic ValidationError on the tool's input model
    - Canonical profile/boundary cannot authorize the execution domain
    - PreToolUse hook returns deny
    - Tool itself returns ``ToolResult(is_error=True)``

    P3-T4.4f:PreToolUse hooks run between AuthZ and execute;PostToolUse
    after execute and before LLM sees result. Hook crashes wrap as
    ``ToolError`` after firing OnError chain (one-level).
    """
    try:
        dispatch_registry = context.dispatch_tool_registry or context.tool_registry
        tool = dispatch_registry.get(tool_use.name)
    except KeyError:
        return _outcome((f"tool not found: {tool_use.name}", True))

    try:
        args = tool.input_model.model_validate(tool_use.input)
    except ValidationError as exc:
        return _outcome((f"invalid input for {tool_use.name}: {exc}", True))

    action_deny = _evaluate_action_deny_policy(
        context,
        tool_name=tool_use.name,
        args=args,
        exec_context=exec_context,
    )
    verified_dispatch, verified_failure = _verified_dispatch_authorization(tool, context)
    if action_deny is not None:
        return _outcome((f"action denied: {action_deny.reason}", True))
    if verified_failure is not None:
        return _outcome(verified_failure)
    if not verified_dispatch:
        return _outcome((f"authorization denied: no canonical authority for {tool.name}", True))

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
            return _outcome((f"hook denied: {pre_result.message or tool_use.name}", True))
        if pre_result.decision == "modify" and pre_result.new_input is not None:
            current_input = pre_result.new_input
            # Re-validate the modified input against the tool's schema.
            try:
                args = tool.input_model.model_validate(current_input)
            except ValidationError as exc:
                return _outcome((f"hook-modified input invalid for {tool_use.name}: {exc}", True))
            # Hooks are input middleware, not an authorization authority. The
            # first check above protects hooks from seeing calls already denied
            # by the framework; this second check makes the final arguments the
            # ones that are actually authorized immediately before execution.
            action_deny = _evaluate_action_deny_policy(
                context,
                tool_name=tool_use.name,
                args=args,
                exec_context=exec_context,
            )
            verified_dispatch, verified_failure = _verified_dispatch_authorization(tool, context)
            if action_deny is not None:
                return _outcome((f"action denied: {action_deny.reason}", True))
            if verified_failure is not None:
                return _outcome(verified_failure)
            if not verified_dispatch:
                return _outcome(
                    (f"authorization denied: no canonical authority for {tool.name}", True)
                )

    external_failure = await _external_effect_failure(
        tool,
        tool_use,
        args,
        context,
        authorization_context=authorization_context,
    )
    if external_failure is not None:
        return _outcome(external_failure)

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

    result = await _resolve_local_boundary_violation(
        tool=tool,
        tool_use=tool_use,
        args=args,
        context=context,
        exec_context=exec_context,
        result=result,
        authorization_context=authorization_context,
    )

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

    return _DispatchOutcome(
        output=result.output,
        is_error=result.is_error,
        metadata=dict(result.metadata),
    )


def _boundary_violation_metadata(result: ToolResult) -> BoundaryViolation | None:
    raw = result.metadata.get("boundary_violation")
    if not isinstance(raw, dict):
        return None
    dimension = raw.get("dimension")
    requested = raw.get("requested")
    evidence = raw.get("evidence")
    hard_deny = raw.get("hard_deny", False)
    if not (
        isinstance(dimension, str) and isinstance(requested, str) and isinstance(evidence, str)
    ):
        return None
    if not isinstance(hard_deny, bool):
        return None
    return BoundaryViolation(
        dimension=dimension,
        requested=requested,
        evidence=evidence,
        hard_deny=hard_deny,
    )


def _delta_for_violation(violation: BoundaryViolation) -> PermissionDelta | None:
    if violation.dimension.startswith("network."):
        host = violation.requested.rsplit(":", 1)[0].strip("[]")
        return PermissionDelta(
            kind=PermissionDeltaKind.NETWORK_DOMAIN,
            value=host,
            hard_deny=violation.hard_deny,
        )
    access = {
        "filesystem.read": PermissionFilesystemAccess.READ,
        "filesystem.search": PermissionFilesystemAccess.SEARCH,
        "filesystem.write": PermissionFilesystemAccess.WRITE,
    }.get(violation.dimension)
    if access is None:
        return None
    return PermissionDelta.filesystem_path(
        violation.requested,
        access=access,
        hard_deny=violation.hard_deny,
    )


def _dataflow_for_violation(
    violation: BoundaryViolation,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if violation.dimension in {"filesystem.read", "filesystem.search"}:
        return (violation.requested,), ("model context",)
    if violation.dimension.startswith("filesystem."):
        return ("final tool arguments",), (violation.requested,)
    return ("sandbox-visible data",), (violation.requested,)


async def _resolve_local_boundary_violation(
    *,
    tool: BaseTool[Any],
    tool_use: ToolUseBlock,
    args: BaseModel,
    context: QueryContext,
    exec_context: ToolExecutionContext,
    result: ToolResult,
    authorization_context: tuple[str, ...] = (),
) -> ToolResult:
    violation = _boundary_violation_metadata(result)
    runtime = context.permission_runtime
    if violation is None or runtime is None:
        return result
    delta = _delta_for_violation(violation)
    if delta is None:
        return ToolResult(
            output=(
                "permission denied: boundary violation cannot be represented as "
                f"a minimal permission delta ({violation.dimension})"
            ),
            is_error=True,
            metadata=result.metadata,
        )
    data_sources, data_destinations = _dataflow_for_violation(violation)
    request = PermissionDeltaRequest.create(
        tool_use_id=tool_use.id,
        tool_name=tool.name,
        final_arguments=args.model_dump(mode="json"),
        profile=runtime.profile,
        boundary=runtime.require_local_boundary(),
        delta=delta,
        crossing=violation,
        data_sources=data_sources,
        data_destinations=data_destinations,
        authorization_context=authorization_context,
    )
    approved = runtime.consume_grant(request)
    resolution_reason = "human approved exact request"
    if not approved:
        resolution = await runtime.resolve_boundary_result(
            violation,
            request_factory=lambda: request,
        )
        resolution_reason = resolution.reason
        if resolution.status is PermissionResolutionStatus.RETRY_ONCE:
            approved = runtime.consume_grant(request)
        elif resolution.status is PermissionResolutionStatus.PARKED:
            return ToolResult(
                output=f"permission parked: {resolution.reason}",
                is_error=True,
                metadata=result.metadata,
            )
        else:
            return ToolResult(
                output=f"permission denied: {resolution.reason}",
                is_error=True,
                metadata=result.metadata,
            )
    session = context.sandbox_session
    if not approved or not isinstance(session, OneShotOverlaySession):
        runtime.park(request, reason="approved delta has no verified overlay executor")
        return ToolResult(
            output="permission parked: approved delta has no verified overlay executor",
            is_error=True,
            metadata=result.metadata,
        )
    try:
        session.arm(request)
        retried = await tool.execute(args, exec_context)
    except (SandboxUnavailableError, RuntimeError, ValueError) as exc:
        runtime.park(request, reason=f"approved overlay could not be installed: {exc}")
        return ToolResult(
            output=f"permission parked: approved overlay could not be installed: {exc}",
            is_error=True,
            metadata=result.metadata,
        )
    if _boundary_violation_metadata(retried) is not None:
        runtime.park(
            request,
            reason="one-shot overlay did not satisfy the exact boundary request",
        )
        return ToolResult(
            output="permission parked: one-shot overlay did not satisfy the exact boundary request",
            is_error=True,
            metadata=retried.metadata,
        )
    logger.info(
        "permission_overlay_consumed",
        request_id=request.request_id,
        reason=resolution_reason,
    )
    return ToolResult(
        output=retried.output,
        is_error=retried.is_error,
        metadata={
            **retried.metadata,
            "boundary_violation": result.metadata["boundary_violation"],
            "permission_overlay": "consumed",
        },
    )


async def _external_effect_failure(
    tool: BaseTool[Any],
    tool_use: ToolUseBlock,
    args: BaseModel,
    context: QueryContext,
    *,
    authorization_context: tuple[str, ...] = (),
) -> tuple[str, bool] | None:
    if tool.execution_domain is not ExecutionDomain.EXTERNAL_EFFECT:
        return None
    surface = tool.external_effect_surface
    kind = tool.external_effect_kind
    if surface is None or kind is None:
        return "external effect denied: tool has incomplete external policy metadata", True
    external_policy = context.runtime_permission_profile.external_tools
    mode = getattr(external_policy, surface.value)
    if mode is ExternalToolMode.DENY:
        return f"external effect denied by {surface.value} policy: {tool.name}", True
    requires_call_approval = (
        mode is ExternalToolMode.ASK
        or not tool.external_effect_trusted
        or kind
        in {
            ExternalEffectKind.MUTATING,
            ExternalEffectKind.DESTRUCTIVE,
            ExternalEffectKind.UNKNOWN,
        }
    )
    if requires_call_approval:
        runtime = context.permission_runtime
        if runtime is None:
            return (
                f"external approval required for {surface.value} "
                f"({kind.value}, trust={tool.trust_source}): {tool.name}",
                True,
            )
        request = PermissionDeltaRequest.create_external(
            tool_use_id=tool_use.id,
            tool_name=tool.name,
            final_arguments=args.model_dump(mode="json"),
            profile=runtime.profile,
            policy=external_policy,
            surface=surface.value,
            effect_kind=kind.value,
            trust_source=tool.trust_source,
            tool_identity=tool.name,
            server_identity=tool.external_server_identity,
            delta=PermissionDelta.external_tool(surface.value),
            crossing=BoundaryViolation(
                dimension=f"external.{surface.value}",
                requested=tool.name,
                evidence=(
                    f"{kind.value} external effect is outside the local sandbox; "
                    f"trust={tool.trust_source}"
                ),
            ),
            data_sources=("final tool arguments",),
            data_destinations=(surface.value,),
            authorization_context=authorization_context,
        )
        if runtime.consume_grant(request):
            return None
        resolution = await runtime.resolve_external(request)
        if resolution.status is PermissionResolutionStatus.RETRY_ONCE:
            if not runtime.consume_grant(request):
                return "external approval was not bound to the exact request", True
            return None
        if resolution.status is PermissionResolutionStatus.PARKED:
            return f"permission parked: {resolution.reason}", True
        return f"external effect denied: {resolution.reason}", True
    return None


def _evaluate_action_deny_policy(
    context: QueryContext,
    *,
    tool_name: str,
    args: BaseModel,
    exec_context: ToolExecutionContext,
) -> DenyResult | None:
    """Evaluate the authoritative deny-only policy, failing closed."""
    policy = context.action_deny_policy
    if policy is None:
        return None
    try:
        return policy.evaluate(tool_name, args, exec_context)
    except Exception as exc:
        # Do not log the exception message: a third-party implementation may
        # embed arguments. Policy failure cannot safely authorize execution.
        logger.warning(
            "action_deny_policy_failed",
            tool=tool_name,
            error_type=type(exc).__name__,
        )
        return DenyResult(
            kind=ActionDenyKind.POLICY_FAILURE,
            reason="action policy evaluation failed",
        )


def _verified_dispatch_authorization(
    tool: BaseTool[Any],
    context: QueryContext,
) -> tuple[bool, tuple[str, bool] | None]:
    """Require verified local dispatch for every local or delegated tool.

    Once a sandbox session is present, local execution must be justified by
    its reported verified boundary. Any drift or incomplete coverage denies;
    it never falls back to unverified host execution.
    """
    if tool.execution_domain in {
        ExecutionDomain.EXTERNAL_EFFECT,
        ExecutionDomain.TRUSTED_CONTROL,
    }:
        # These domains have their own canonical authority: external policy or
        # explicitly trusted in-process control. Neither is a local data-plane
        # containment question.
        return True, None
    if tool.execution_domain not in {
        ExecutionDomain.LOCAL_DATA,
        ExecutionDomain.DELEGATED_RUNTIME,
    }:
        return False, None
    session = context.sandbox_session
    if session is None:
        return True, (
            "verified dispatch denied: local execution requires a verified sandbox boundary",
            True,
        )

    boundary = session.boundary
    if not boundary.is_verified:
        return True, ("verified dispatch denied: sandbox boundary is unverified", True)
    profile = context.runtime_permission_profile
    if profile is not None and boundary.profile_fingerprint != profile.fingerprint:
        return True, ("verified dispatch denied: runtime profile drift", True)
    enforced = context.enforced_boundary
    if enforced is not None and boundary.fingerprint != enforced.fingerprint:
        return True, ("verified dispatch denied: sandbox boundary drift", True)
    if tool.execution_domain is ExecutionDomain.DELEGATED_RUNTIME:
        registry = context.dispatch_tool_registry or context.tool_registry
        for delegated_tool in registry.list_tools():
            if delegated_tool.execution_domain is not ExecutionDomain.LOCAL_DATA:
                continue
            delegated_effect = delegated_tool.required_execution_effect
            if delegated_effect is None:
                return True, (
                    f"verified dispatch denied: delegated tool {delegated_tool.name} "
                    "has no declared sandbox effect",
                    True,
                )
            if not boundary.covers(delegated_effect):
                return True, (
                    f"verified dispatch denied: delegated tool {delegated_tool.name} "
                    f"lacks {delegated_effect.value} coverage",
                    True,
                )
        return True, None

    effect = tool.required_execution_effect
    if effect is None:
        return True, ("verified dispatch denied: tool has no declared sandbox effect", True)
    if not boundary.covers(effect):
        return True, (f"verified dispatch denied: boundary does not cover {effect.value}", True)
    return True, None


def _require_autonomous_boundary(context: QueryContext) -> None:
    if not context.autonomous or context.execution_posture is ExecutionPosture.DRY_RUN:
        return
    for tool in context.tool_registry.list_tools():
        if tool.execution_domain not in {
            ExecutionDomain.LOCAL_DATA,
            ExecutionDomain.DELEGATED_RUNTIME,
        }:
            continue
        verified, failure = _verified_dispatch_authorization(tool, context)
        if not verified:
            raise AutonomousBoundaryError(
                f"autonomous tool {tool.name} requires a verified sandbox boundary"
            )
        if failure is not None:
            raise AutonomousBoundaryError(f"autonomous tool {tool.name}: {failure[0]}")
