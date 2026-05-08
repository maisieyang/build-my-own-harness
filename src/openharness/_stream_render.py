"""Append-only streaming renderer: ``ApiStreamEvent`` → terminal.

Phase 1 (D5.5) shipped a 3-event renderer; P2-T6.6d extends it with the two
engine-emitted tool events introduced in P2-T4.4a:

- :class:`ApiTextDeltaEvent` → write ``text`` to stdout, no newline, flushed
- :class:`ApiRetryEvent`     → diagnostic line on stderr (does not pollute stdout)
- :class:`ApiMessageCompleteEvent` → trailing newline (only if any text was emitted)
- :class:`ToolExecutionStartedEvent` → ``[ToolName] arg1=v1 arg2=v2\\n`` (D12.6)
- :class:`ToolExecutionCompletedEvent` → ``[ToolName] → output (truncated)\\n``,
  with ``[ToolName error] ...`` prefix on ``is_error=True``

Tool events render on their own line with a ``[Tool]`` prefix; output is
truncated at ``MAX_OUTPUT_PREVIEW`` chars to keep terminal noise bounded.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

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

    from openharness.protocols.stream_events import ApiStreamEvent


# Per D12.6: cap tool output rendered to terminal so a 12k-char Bash dump
# doesn't drown the LLM's actual answer. The full output is still in the
# ToolResultBlock that goes back to the LLM -- this is purely UI hygiene.
MAX_OUTPUT_PREVIEW = 500


async def render_stream(
    events: AsyncIterator[ApiStreamEvent],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> ApiMessageCompleteEvent | None:
    """Drain ``events`` to ``stdout`` / ``stderr``; return the terminal event.

    Returns the *last* :class:`ApiMessageCompleteEvent` seen (multi-turn
    loops emit several -- one per turn). Returns ``None`` if the stream
    ended without a complete event -- which only happens if an exception
    escaped mid-stream and was caught by the caller.
    """
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    saw_text = False
    final: ApiMessageCompleteEvent | None = None

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
                # Reset so the next turn's text deltas decide independently
                # whether to add a trailing newline.
                saw_text = False
        elif isinstance(event, ToolExecutionStartedEvent):
            out.write(_render_tool_started(event))
            out.flush()
        elif isinstance(event, ToolExecutionCompletedEvent):
            out.write(_render_tool_completed(event))
            out.flush()

    return final


def _render_tool_started(event: ToolExecutionStartedEvent) -> str:
    """``[Bash] command="ls /tmp"`` style line."""
    args_repr = " ".join(f"{k}={v!r}" for k, v in event.tool_input.items())
    return f"[{event.tool_name}] {args_repr}\n"


def _render_tool_completed(event: ToolExecutionCompletedEvent) -> str:
    """``[Bash] → output...`` (success) or ``[Bash error] ...`` (recoverable)."""
    label = f"{event.tool_name} error" if event.is_error else event.tool_name
    output = event.output
    if len(output) > MAX_OUTPUT_PREVIEW:
        dropped = len(output) - MAX_OUTPUT_PREVIEW
        output = output[:MAX_OUTPUT_PREVIEW] + f"... [+{dropped} chars]"
    arrow = "✗" if event.is_error else "→"
    return f"[{label}] {arrow} {output}\n"
