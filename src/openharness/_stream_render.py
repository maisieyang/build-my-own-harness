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

import sys
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table

from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiTextDeltaEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import TextIO

    from rich.console import ConsoleOptions, RenderResult

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
    use_live = console.is_terminal

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
    finally:
        # If the stream raised mid-tool-execution, keep the terminal sane
        # by stopping Live (idempotent if already stopped).
        if current_live is not None:
            current_live.stop()

    return final


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
    live.start()
    return live


def _render_tool_started(event: ToolExecutionStartedEvent) -> str:
    """``[Bash] command="ls /tmp"`` line, used in non-TTY branch."""
    args_repr = " ".join(f"{k}={v!r}" for k, v in event.tool_input.items())
    return f"[{event.tool_name}] {args_repr}\n"


def _render_tool_completed(event: ToolExecutionCompletedEvent) -> str:
    """``[Bash] → output...`` (success) or ``[Bash error] ✗ ...`` (recoverable).

    Identical content in both TTY and non-TTY branches; the TTY branch
    just runs after Live cleared the spinner region.
    """
    label = f"{event.tool_name} error" if event.is_error else event.tool_name
    output = event.output
    if len(output) > MAX_OUTPUT_PREVIEW:
        dropped = len(output) - MAX_OUTPUT_PREVIEW
        output = output[:MAX_OUTPUT_PREVIEW] + f"... [+{dropped} chars]"
    arrow = "✗" if event.is_error else "→"
    return f"[{label}] {arrow} {output}\n"
