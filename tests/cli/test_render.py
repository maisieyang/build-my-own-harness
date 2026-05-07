"""Tests for the append-only streaming renderer.

The renderer is a thin event → bytes mapping; it has no async or network
behavior of its own. Tests therefore feed in synthetic
:class:`ApiStreamEvent` sequences and assert on captured stdout/stderr.

The four behaviors that matter for Phase 1 (D5.5):

1. ``ApiTextDeltaEvent`` text concatenates to stdout in order.
2. A trailing newline is added iff some text was emitted -- so silent
   tool-only responses do not leave a stray blank line in the user's
   shell.
3. ``ApiRetryEvent`` lands on stderr (does **not** pollute stdout, so
   pipes like ``oh ask | tee out.txt`` capture only the model's text).
4. The terminal :class:`ApiMessageCompleteEvent` is returned to the
   caller for inspection (usage, stop_reason) without re-parsing
   terminal output.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from openharness._stream_render import render_stream
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.content import ContentBlock
    from openharness.protocols.stream_events import ApiStreamEvent


async def _async_iter(items: list[ApiStreamEvent]) -> AsyncIterator[ApiStreamEvent]:
    """Wrap a list in an async generator for renderer consumption."""
    for item in items:
        yield item


def _final_event(text: str = "") -> ApiMessageCompleteEvent:
    """Build a minimal :class:`ApiMessageCompleteEvent`. Tests rarely care
    about the assembled message contents, but the type requires them."""
    content: list[ContentBlock] = [TextBlock(text=text)] if text else []
    return ApiMessageCompleteEvent(
        message=ConversationMessage(role="assistant", content=content),
        usage=UsageSnapshot(input_tokens=1, output_tokens=len(text.split())),
        stop_reason="end_turn",
    )


class TestTextDeltas:
    """Text deltas land on stdout, in arrival order, with a trailing newline."""

    @pytest.mark.asyncio
    async def test_concatenates_in_order(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter(
            [
                ApiTextDeltaEvent(text="Hello "),
                ApiTextDeltaEvent(text="world"),
                _final_event("Hello world"),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        assert out.getvalue() == "Hello world\n"
        assert err.getvalue() == ""

    @pytest.mark.asyncio
    async def test_returns_terminal_event(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        terminal = _final_event("Hi")
        events = _async_iter(
            [
                ApiTextDeltaEvent(text="Hi"),
                terminal,
            ]
        )

        result = await render_stream(events, stdout=out, stderr=err)

        assert result is terminal


class TestRetryEvent:
    """Retry diagnostics go to stderr; stdout stays clean for piping."""

    @pytest.mark.asyncio
    async def test_retry_goes_to_stderr_only(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter(
            [
                ApiRetryEvent(attempt=2, delay_seconds=1.5, error="429 rate limited"),
                ApiTextDeltaEvent(text="OK"),
                _final_event("OK"),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        assert out.getvalue() == "OK\n"
        assert "retry attempt 2" in err.getvalue()
        assert "429" in err.getvalue()
        assert "1.5s" in err.getvalue()


class TestEmptyResponse:
    """No text events → no trailing newline, no stray output."""

    @pytest.mark.asyncio
    async def test_no_newline_when_no_text(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter([_final_event()])

        result = await render_stream(events, stdout=out, stderr=err)

        assert out.getvalue() == ""
        assert err.getvalue() == ""
        assert result is not None


class TestStreamWithoutFinalEvent:
    """If the producer somehow ends without a terminal event, return None."""

    @pytest.mark.asyncio
    async def test_returns_none_without_terminal(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter([ApiTextDeltaEvent(text="partial")])

        result = await render_stream(events, stdout=out, stderr=err)

        assert result is None
        # Without a terminal event we did emit text, so no trailing newline
        # is added -- that is the deliberate "incomplete stream" signal.
        assert out.getvalue() == "partial"
