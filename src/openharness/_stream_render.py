"""Append-only streaming renderer: ``ApiStreamEvent`` → terminal.

Phase 1 (D5.5) shipped a 3-event renderer; P2-T6.6d extended it with the
two engine-emitted tool events introduced in P2-T4.4a. Phase 15 (D30.x)
wraps the tool-event pair (Started → Completed) in a ``rich.Live`` region
when stdout is a TTY, falling back byte-identical to the pre-Phase-15
behavior off-TTY (pipes, files, CI, ``oh ask --print``).

Behavior table:

- :class:`ApiTextDeltaEvent`         → write ``text`` to stdout, flushed
- :class:`ApiRetryEvent`             → diagnostic line on stderr
- :class:`ApiMessageCompleteEvent`   → trailing newline (only if any text)
- :class:`ToolExecutionStartedEvent`   (non-TTY) → ``[ToolName] arg=v\\n``
- :class:`ToolExecutionStartedEvent`   (TTY)     → enter Live with spinner
- :class:`ToolExecutionCompletedEvent` (non-TTY) → ``[ToolName] → out\\n``
- :class:`ToolExecutionCompletedEvent` (TTY)     → stop Live, write the
  same final line (cursor was restored by Live's transient cleanup)

The TTY branch and non-TTY branch produce identical final-line content;
only the TTY branch additionally emits cursor-control codes around the
Live region. Tests use ``Console(file=StringIO())`` which auto-detects as
non-TTY, so existing string-equality assertions stay byte-identical
without modification.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table

from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiTextDeltaEvent,
    PermissionParkedEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import TextIO

    from rich.console import ConsoleOptions, RenderResult

    from openharness.protocols.messages import ConversationMessage
    from openharness.protocols.stream_events import ApiStreamEvent


# Per D12.6: cap tool output rendered to terminal so a 12k-char Bash dump
# doesn't drown the LLM's actual answer. The full output is still in the
# ToolResultBlock that goes back to the LLM -- this is purely UI hygiene.
MAX_OUTPUT_PREVIEW = 500


class _ToolSpinnerRenderable:
    """Renderable that yields spinner + tool-name + args + live elapsed time.

    ``rich.Live`` re-evaluates the renderable's ``__rich_console__`` on
    every refresh tick (10 Hz here), so the elapsed counter advances
    without any external task — the spinner's own frame animation rides
    the same refresh loop.
    """

    def __init__(
        self,
        *,
        tool_name: str,
        args_repr: str,
        start_time: float,
    ) -> None:
        self._tool_name = tool_name
        self._args_repr = args_repr
        self._start_time = start_time
        # One Spinner instance, kept across refreshes, so frame animation
        # is continuous (Spinner advances frame based on time since init).
        self._spinner = Spinner("dots")

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        elapsed = time.monotonic() - self._start_time
        line = f"[{self._tool_name}] {self._args_repr} ({elapsed:.1f}s)"
        grid = Table.grid(padding=(0, 1))
        grid.add_row(self._spinner, line)
        yield grid


async def render_stream(
    events: AsyncIterator[ApiStreamEvent],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    console: Console | None = None,
) -> ApiMessageCompleteEvent | None:
    """Drain ``events`` to ``stdout`` / ``stderr``; return the terminal event.

    ``console`` is an optional injection point (D30.4): tests pass a
    ``Console(force_terminal=True)`` to exercise the Live branch
    deterministically. Production callers leave it ``None`` and the
    constructor auto-detects TTY via ``stdout.isatty()``.

    Returns the *last* :class:`ApiMessageCompleteEvent` seen (multi-turn
    loops emit several -- one per turn). Returns ``None`` if the stream
    ended without a complete event.
    """
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    # D30.3: explicit TTY branch. Construct Console once at entry; reuse
    # for the Live region throughout. ``is_terminal`` is True only if the
    # underlying file's ``isatty()`` is True OR ``force_terminal=True``.
    if console is None:
        console = Console(file=out)
    use_live = console.is_terminal and not console.is_dumb_terminal

    saw_text = False
    final: ApiMessageCompleteEvent | None = None
    current_live: Live | None = None

    try:
        async for event in events:
            if isinstance(event, ApiTextDeltaEvent):
                out.write(event.text)
                out.flush()
                saw_text = True
            elif isinstance(event, ApiRetryEvent):
                err.write(
                    f"[retry attempt {event.attempt}: {event.error} "
                    f"— sleeping {event.delay_seconds:.1f}s]\n"
                )
                err.flush()
            elif isinstance(event, ApiMessageCompleteEvent):
                final = event
                if saw_text:
                    out.write("\n")
                    out.flush()
                    # Reset so the next turn's text deltas decide
                    # independently whether to add a trailing newline.
                    saw_text = False
            elif isinstance(event, ToolExecutionStartedEvent):
                if use_live:
                    current_live = _start_live(event, console)
                else:
                    out.write(_render_tool_started(event))
                    out.flush()
            elif isinstance(event, ToolExecutionCompletedEvent):
                if current_live is not None:
                    # transient=True: stop() emits the clear-region escape
                    # so the spinner area is erased; the final-line write
                    # below lands on the now-vacated row.
                    current_live.stop()
                    current_live = None
                out.write(_render_tool_completed(event))
                out.flush()
            elif isinstance(event, PermissionParkedEvent):
                err.write(f"[permission parked {event.request_id[:12]}: {event.reason}]\n")
                err.flush()
    finally:
        # If the stream raised mid-tool-execution, keep the terminal sane
        # by stopping Live (idempotent if already stopped).
        if current_live is not None:
            current_live.stop()

    return final


@dataclass(frozen=True)
class PrintResult:
    """Aggregated pieces a headless ``--output-format json`` result needs
    (loop-runtime L1 T3). Usage is summed across all turns; ``text`` and
    ``stop_reason`` come from the final turn."""

    text: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    num_turns: int


def _print_result(
    final: ApiMessageCompleteEvent | None,
    *,
    input_tokens: int,
    output_tokens: int,
    num_turns: int,
) -> PrintResult:
    """Assemble a :class:`PrintResult` from drained state. Single source for
    the final-turn text extraction + construction, shared by the silent
    (``collect_print_result``) and streaming (``render_stream_json``) drainers."""
    text = (
        "".join(b.text for b in final.message.content if isinstance(b, TextBlock))
        if final is not None
        else ""
    )
    return PrintResult(
        text=text,
        stop_reason=final.stop_reason if final is not None else None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        num_turns=num_turns,
    )


async def collect_print_result(events: AsyncIterator[ApiStreamEvent]) -> PrintResult:
    """Drain ``events`` SILENTLY (still driving tool execution) and aggregate
    the headless json result.

    Unlike :func:`render_stream`, nothing is written to any stream -- the
    caller serializes the returned :class:`PrintResult` as a single JSON
    object. Usage is summed across every ``ApiMessageCompleteEvent`` (one per
    turn); ``text`` / ``stop_reason`` reflect the final turn.
    """
    input_tokens = 0
    output_tokens = 0
    num_turns = 0
    final: ApiMessageCompleteEvent | None = None
    async for event in events:
        if isinstance(event, ApiMessageCompleteEvent):
            final = event
            num_turns += 1
            input_tokens += event.usage.input_tokens
            output_tokens += event.usage.output_tokens
    return _print_result(
        final, input_tokens=input_tokens, output_tokens=output_tokens, num_turns=num_turns
    )


_TRANSCRIPT_TOOL_OUTPUT_PREVIEW = 2000


def _preview_goal_tool_output(output: str) -> str:
    """Bound judge evidence while preserving diagnostics and final verdicts."""
    if len(output) <= _TRANSCRIPT_TOOL_OUTPUT_PREVIEW:
        return output
    head_size = _TRANSCRIPT_TOOL_OUTPUT_PREVIEW // 2
    tail_size = _TRANSCRIPT_TOOL_OUTPUT_PREVIEW - head_size
    omitted = len(output) - _TRANSCRIPT_TOOL_OUTPUT_PREVIEW
    return f"{output[:head_size]}\n... [{omitted} chars omitted] ...\n{output[-tail_size:]}"


def render_history_transcript(messages: list[ConversationMessage]) -> str:
    """Render goal-scoped conversation evidence for the independent judge.

    Tool calls and results matter more than the worker's final prose, so both
    are included and long outputs are preview-capped. Assistant boundaries
    make turn-count stop conditions judgeable.
    """
    lines: list[str] = []
    turn = 0
    for message in messages:
        if message.role == "assistant":
            turn += 1
            lines.append(f"\n[assistant turn {turn}]\n")
            for block in message.content:
                if isinstance(block, TextBlock):
                    lines.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    lines.append(f"\n[tool call: {block.name}({block.input!r})]\n")
        else:
            for block in message.content:
                if isinstance(block, TextBlock):
                    lines.append(f"\n[user]: {block.text}\n")
                elif isinstance(block, ToolResultBlock):
                    status = "error" if block.is_error else "ok"
                    output = _preview_goal_tool_output(block.content)
                    lines.append(f"[tool result ({status}): {output}]\n")
    return "".join(lines)


def build_result_obj(
    result: PrintResult,
    *,
    session_id: str,
) -> dict[str, object]:
    """Single source of truth for the headless json ``result`` object shape
    (loop-runtime L1 T3 + T4). ``cost_usd`` stays null until a pricing layer
    lands (T0: v1 has no cost computation)."""
    return {
        "type": "result",
        "result": result.text,
        "stop_reason": result.stop_reason,
        "usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens": result.input_tokens + result.output_tokens,
        },
        "cost_usd": None,
        "num_turns": result.num_turns,
        "session_id": session_id,
    }


async def render_stream_json(
    events: AsyncIterator[ApiStreamEvent],
    *,
    session_id: str,
    stdout: TextIO | None = None,
) -> str | None:
    """Stream each engine event as ONE newline-delimited JSON object, then a
    final ``result`` object (loop-runtime L1 T4). Returns the terminal
    stop_reason so the caller maps run-level outcome → exit code (T2).

    Event → line mapping: text deltas → ``assistant_delta``; tool dispatch →
    ``tool_started`` / ``tool_completed``; retry-after-retryable-error → ``retry``.
    ``ApiMessageCompleteEvent`` is accumulated (usage / turn count / final
    text) rather than emitted, and surfaces in the terminating ``result``.

    The stream ALWAYS ends with a terminator: a ``result`` on success, or an
    ``error`` object if the run raises mid-stream (the exception then re-raises
    for the CLI exit-code mapping). Note: ``assistant_delta`` lines stream every
    turn's text (including intermediate pre-tool-call text), so concatenated
    deltas != ``result.result`` — which is only the FINAL turn's text (matches
    json mode and Claude Code: deltas are the stream, result is the answer).
    """
    out = sys.stdout if stdout is None else stdout

    def _emit(obj: dict[str, object]) -> None:
        out.write(json.dumps(obj) + "\n")
        out.flush()

    input_tokens = 0
    output_tokens = 0
    num_turns = 0
    final: ApiMessageCompleteEvent | None = None
    try:
        async for event in events:
            if isinstance(event, ApiTextDeltaEvent):
                _emit({"type": "assistant_delta", "text": event.text})
            elif isinstance(event, ToolExecutionStartedEvent):
                _emit(
                    {
                        "type": "tool_started",
                        "tool": event.tool_name,
                        "tool_use_id": event.tool_use_id,
                        "input": event.tool_input,
                    }
                )
            elif isinstance(event, ToolExecutionCompletedEvent):
                _emit(
                    {
                        "type": "tool_completed",
                        "tool": event.tool_name,
                        "tool_use_id": event.tool_use_id,
                        "is_error": event.is_error,
                        "output": event.output,
                    }
                )
            elif isinstance(event, ApiRetryEvent):
                # A retry is "retrying after a retryable error", NOT a failure —
                # type it "retry" so consumers (e.g. L4) don't misread it as the
                # run having errored. ``error`` carries what triggered the retry.
                _emit({"type": "retry", "attempt": event.attempt, "error": event.error})
            elif isinstance(event, ApiMessageCompleteEvent):
                final = event
                num_turns += 1
                input_tokens += event.usage.input_tokens
                output_tokens += event.usage.output_tokens
    except Exception as exc:
        # Guarantee a stream terminator: on a mid-stream raise (LoopLimitExceeded,
        # API failure, ...) emit a terminal ``error`` line before the exception
        # unwinds to the CLI exit-code mapping, so consumers always see a final
        # ``result`` OR ``error``. ``error`` (terminal) is distinct from ``retry``.
        _emit({"type": "error", "error": str(exc)})
        raise

    result = _print_result(
        final, input_tokens=input_tokens, output_tokens=output_tokens, num_turns=num_turns
    )
    _emit(build_result_obj(result, session_id=session_id))
    return result.stop_reason


def _start_live(event: ToolExecutionStartedEvent, console: Console) -> Live:
    """Construct and start a transient Live region for one tool execution."""
    args_repr = " ".join(f"{k}={v!r}" for k, v in event.tool_input.items())
    renderable = _ToolSpinnerRenderable(
        tool_name=event.tool_name,
        args_repr=args_repr,
        start_time=time.monotonic(),
    )
    live = Live(
        renderable,
        console=console,
        refresh_per_second=10,
        transient=True,
    )
    # Render the first frame synchronously. Rich 15 defaults to
    # ``refresh=False`` here, so fast tools may otherwise finish before
    # the 10 Hz refresh thread ever makes the spinner visible.
    live.start(refresh=True)
    return live


def _render_tool_started(event: ToolExecutionStartedEvent) -> str:
    """``[Bash] command="ls /tmp"`` line, used in non-TTY branch."""
    args_repr = " ".join(f"{k}={v!r}" for k, v in event.tool_input.items())
    return f"[{event.tool_name}] {args_repr}\n"


def _render_tool_completed(event: ToolExecutionCompletedEvent) -> str:
    """``[Bash] → output...`` (success) or ``[Bash error] ✗ ...`` (recoverable).

    Identical content in both TTY and non-TTY branches; the TTY branch
    just runs after Live cleared the spinner region.

    Output is ``.strip()``-ed before truncation: tools like ``WebFetch``
    whose markdownify output preserves whitespace left by stripped HTML
    chrome would otherwise render as ``[WebFetch] →`` followed by many
    blank lines before content. Internal whitespace (paragraph breaks)
    is preserved — only the surrounding edges are trimmed. The
    ``[+N chars]`` truncation count reflects the stripped length.
    The LLM-facing ``ToolResultBlock`` is unaffected (renderer is the
    UI display surface, consistent with the Phase 1 D12.6 LLM/UI split).
    """
    label = f"{event.tool_name} error" if event.is_error else event.tool_name
    output = event.output.strip()
    if len(output) > MAX_OUTPUT_PREVIEW:
        dropped = len(output) - MAX_OUTPUT_PREVIEW
        output = output[:MAX_OUTPUT_PREVIEW] + f"... [+{dropped} chars]"
    arrow = "✗" if event.is_error else "→"
    return f"[{label}] {arrow} {output}\n"
