"""Append-only streaming renderer: ``ApiStreamEvent`` → terminal.

Phase 1 ships the simplest possible renderer (D5.5):

- :class:`ApiTextDeltaEvent` → write ``text`` to stdout, no newline, flushed
- :class:`ApiRetryEvent`     → diagnostic line on stderr (does not pollute stdout)
- :class:`ApiMessageCompleteEvent` → trailing newline (only if any text was emitted)

No Markdown re-render, no Rich live region, no JSON output mode -- those
arrive in Tier 1 alongside proper Print mode. "First signal" deliberately
mirrors ``cat`` / ``curl --no-buffer``: an append-only byte stream that
composes with pipes (``oh ask "..." | tee out.txt``).

The renderer accepts injectable ``stdout`` / ``stderr`` so tests verify
output without touching real file descriptors.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiTextDeltaEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import TextIO

    from openharness.protocols.stream_events import ApiStreamEvent


async def render_stream(
    events: AsyncIterator[ApiStreamEvent],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> ApiMessageCompleteEvent | None:
    """Drain ``events`` to ``stdout`` / ``stderr``; return the terminal event.

    Returning :class:`ApiMessageCompleteEvent` lets the caller inspect the
    final assembled message (e.g., for testing or for future "show usage"
    flags) without re-parsing terminal output. Returns ``None`` if the
    stream ended without a complete event -- which only happens if an
    exception escaped mid-stream and was caught by the caller.
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

    return final
