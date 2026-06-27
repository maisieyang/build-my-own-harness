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
import json
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from openharness._stream_render import (
    MAX_OUTPUT_PREVIEW,
    render_stream,
    render_stream_json,
)
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiTextDeltaEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
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


class TestToolEvents:
    """6d: tool events render on their own line with a [Tool] prefix."""

    @pytest.mark.asyncio
    async def test_started_event_renders_with_tool_prefix(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter(
            [
                ToolExecutionStartedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    tool_input={"command": "ls /tmp"},
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        rendered = out.getvalue()
        assert "[Bash]" in rendered
        # repr quoting on the value: command='ls /tmp'
        assert "command='ls /tmp'" in rendered
        # Each tool event is its own line.
        assert rendered.endswith("\n")

    @pytest.mark.asyncio
    async def test_completed_event_renders_success_with_arrow(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter(
            [
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Read",
                    output="hello world\n",
                    is_error=False,
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        rendered = out.getvalue()
        assert "[Read] →" in rendered
        assert "hello world" in rendered

    @pytest.mark.asyncio
    async def test_completed_event_renders_error_with_marker(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter(
            [
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    output="permission denied: Bash",
                    is_error=True,
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        rendered = out.getvalue()
        # is_error=True changes both the label and the arrow marker.
        assert "[Bash error]" in rendered
        assert "✗" in rendered
        assert "permission denied" in rendered

    @pytest.mark.asyncio
    async def test_completed_output_truncated_above_max_preview(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        long_output = "x" * (MAX_OUTPUT_PREVIEW + 200)
        events = _async_iter(
            [
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    output=long_output,
                    is_error=False,
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        rendered = out.getvalue()
        assert "[+200 chars]" in rendered
        # First MAX_OUTPUT_PREVIEW chars survive verbatim.
        assert ("x" * MAX_OUTPUT_PREVIEW) in rendered

    @pytest.mark.asyncio
    async def test_completed_output_stripped_of_surrounding_whitespace(self) -> None:
        """Dogfood follow-up: WebFetch via markdownify left N blank lines
        between ``[Tool] →`` and the real content. ``output.strip()``
        runs at format time so leading / trailing whitespace from
        chrome-stripped HTML doesn't bleed into the terminal."""
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter(
            [
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="WebFetch",
                    output="\n\n\n\n# Page Title\n\nBody content here\n\n\n",
                    is_error=False,
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        rendered = out.getvalue()
        # Content sits flush against "→ " — no leading-blank-line gap.
        assert "[WebFetch] → # Page Title" in rendered
        # Internal paragraph break preserved.
        assert "# Page Title\n\nBody content here" in rendered
        # No trailing whitespace garbage; exactly one \n from format.
        assert rendered.endswith("Body content here\n")

    @pytest.mark.asyncio
    async def test_strip_runs_before_truncation_count(self) -> None:
        """``[+N chars]`` reflects the stripped content's overflow, not
        the raw padded input's. Confirms strip-then-truncate ordering."""
        out = io.StringIO()
        err = io.StringIO()
        body = "x" * (MAX_OUTPUT_PREVIEW + 100)
        padded = f"\n\n\n{body}\n\n\n"  # 6 extra whitespace chars
        events = _async_iter(
            [
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    output=padded,
                    is_error=False,
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        rendered = out.getvalue()
        # Counts 100, not 106 — strip happened before the length check.
        assert "[+100 chars]" in rendered
        # Content starts immediately after "→ " (no leading blanks).
        assert "[Bash] → x" in rendered

    @pytest.mark.asyncio
    async def test_text_deltas_then_tool_events_in_one_turn(self) -> None:
        # Realistic shape: model says something, requests a tool, sees result,
        # says more. saw_text resets between turns so each turn's text is
        # newline-terminated independently.
        out = io.StringIO()
        err = io.StringIO()
        events = _async_iter(
            [
                ApiTextDeltaEvent(text="Let me check."),
                _final_event("Let me check."),
                ToolExecutionStartedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    tool_input={"command": "ls"},
                ),
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    output="file1\nfile2",
                    is_error=False,
                ),
                ApiTextDeltaEvent(text="Found two files."),
                _final_event("Found two files."),
            ]
        )

        await render_stream(events, stdout=out, stderr=err)

        rendered = out.getvalue()
        # Order preserved: text 1 → tool started → tool completed → text 2.
        assert (
            rendered.index("Let me check.")
            < rendered.index("[Bash] command=")
            < rendered.index("[Bash] →")
            < rendered.index("Found two files.")
        )


class TestLiveBranch:
    """Phase 15 (D30.4): TTY branch of the renderer.

    Existing :class:`TestToolEvents` covers the non-TTY (``StringIO →
    is_terminal=False``) fall-back branch. This class exercises the
    ``rich.Live`` branch via ``Console(force_terminal=True)`` and asserts
    on structural properties — spinner glyph present, final line text
    present, error marker present — not on exact byte sequences, because
    Live's refresh thread emits a non-deterministic number of frames.

    The braille assertion is what proves the Live branch was actually
    taken (the non-TTY fall-back never emits braille characters).
    """

    # rich's default ``Spinner("dots")`` cycles through these 10 frames.
    _BRAILLE_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    @staticmethod
    def _tty_console(buf: io.StringIO) -> Console:
        """Construct a Console that reports ``is_terminal=True`` writing to ``buf``."""
        return Console(
            file=buf,
            force_terminal=True,
            color_system=None,
            width=80,
            legacy_windows=False,
        )

    @pytest.mark.asyncio
    async def test_success_completion_shows_spinner_then_final_line(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        console = self._tty_console(out)
        events = _async_iter(
            [
                ToolExecutionStartedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    tool_input={"command": "ls /tmp"},
                ),
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    output="file1\nfile2",
                    is_error=False,
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err, console=console)

        rendered = out.getvalue()
        # Live branch was taken: at least one braille spinner frame was
        # emitted during the Started → Completed window.
        assert any(ch in rendered for ch in self._BRAILLE_FRAMES), (
            f"expected a braille spinner glyph proving the Live branch ran, got: {rendered!r}"
        )
        # Final completion line content is present (ANSI control codes
        # interleaved are fine -- the user-visible content matters here).
        assert "[Bash] →" in rendered
        assert "file1" in rendered

    @pytest.mark.asyncio
    async def test_error_completion_renders_error_label_and_marker(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        console = self._tty_console(out)
        events = _async_iter(
            [
                ToolExecutionStartedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    tool_input={"command": "false"},
                ),
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Bash",
                    output="exit status 1",
                    is_error=True,
                ),
                _final_event(),
            ]
        )

        await render_stream(events, stdout=out, stderr=err, console=console)

        rendered = out.getvalue()
        # is_error=True changes both the label and the arrow marker; both
        # must survive the TTY branch unchanged.
        assert "[Bash error]" in rendered
        assert "✗" in rendered
        assert "exit status 1" in rendered

    @pytest.mark.asyncio
    async def test_text_deltas_unaffected_by_tty_branch(self) -> None:
        """Text / retry / message-complete events bypass Live entirely."""
        out = io.StringIO()
        err = io.StringIO()
        console = self._tty_console(out)
        events = _async_iter(
            [
                ApiTextDeltaEvent(text="Hello "),
                ApiTextDeltaEvent(text="world"),
                _final_event("Hello world"),
            ]
        )

        await render_stream(events, stdout=out, stderr=err, console=console)

        # No tool event means no Live region, so the output is pure text.
        assert out.getvalue() == "Hello world\n"
        assert err.getvalue() == ""

    @pytest.mark.asyncio
    async def test_text_then_tool_then_text_in_tty_branch(self) -> None:
        """Mixed turn: text before + tool + text after, with Live in middle."""
        out = io.StringIO()
        err = io.StringIO()
        console = self._tty_console(out)
        events = _async_iter(
            [
                ApiTextDeltaEvent(text="Checking..."),
                _final_event("Checking..."),
                ToolExecutionStartedEvent(
                    tool_use_id="t1",
                    tool_name="Read",
                    tool_input={"path": "/etc/hosts"},
                ),
                ToolExecutionCompletedEvent(
                    tool_use_id="t1",
                    tool_name="Read",
                    output="127.0.0.1 localhost",
                    is_error=False,
                ),
                ApiTextDeltaEvent(text="Done."),
                _final_event("Done."),
            ]
        )

        await render_stream(events, stdout=out, stderr=err, console=console)

        rendered = out.getvalue()
        # Order is preserved across the Live transition.
        assert rendered.index("Checking...") < rendered.index("[Read] →") < rendered.index("Done.")
        # Live region was active for the Read tool.
        assert any(ch in rendered for ch in self._BRAILLE_FRAMES)
        # Final line landed even though Live was running just before.
        assert "127.0.0.1 localhost" in rendered


class TestRenderStreamJson:
    """loop-runtime L1 T4: ``--output-format stream-json`` — one JSON object
    per engine event, terminated by a single ``result`` object."""

    @pytest.mark.asyncio
    async def test_emits_one_json_object_per_event_then_result(self) -> None:
        events: list[ApiStreamEvent] = [
            ApiTextDeltaEvent(text="hel"),
            ApiTextDeltaEvent(text="lo"),
            ToolExecutionStartedEvent(tool_use_id="t1", tool_name="Read", tool_input={"path": "x"}),
            ToolExecutionCompletedEvent(
                tool_use_id="t1", tool_name="Read", output="contents", is_error=False
            ),
            ApiRetryEvent(attempt=1, delay_seconds=0.5, error="overloaded"),
            ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="hello")]),
                usage=UsageSnapshot(input_tokens=5, output_tokens=2),
                stop_reason="end_turn",
            ),
        ]
        out = io.StringIO()

        stop_reason = await render_stream_json(
            _async_iter(events), session_id="sid-123", stdout=out
        )

        lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        types = [obj["type"] for obj in lines]
        # All five mapped event types appear, result is LAST. A retry is
        # "retrying after a retryable error" — NOT a failure — so it must NOT
        # be typed "error" (debrief: that misleads consumers like L4).
        assert "assistant_delta" in types
        assert "tool_started" in types
        assert "tool_completed" in types
        assert "retry" in types
        assert "error" not in types
        assert lines[-1]["type"] == "result"
        # The result object is the same shape as --output-format json (T3).
        assert lines[-1]["result"] == "hello"
        assert lines[-1]["stop_reason"] == "end_turn"
        assert lines[-1]["usage"] == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
        assert lines[-1]["cost_usd"] is None
        assert lines[-1]["num_turns"] == 1
        assert lines[-1]["session_id"] == "sid-123"
        # The terminal stop_reason is returned for run-level exit-code mapping.
        assert stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_emits_error_terminator_on_mid_stream_raise(self) -> None:
        """A mid-stream raise (LoopLimitExceeded / API failure / ...) still
        emits a terminal ``error`` object — so stream-json ALWAYS ends with a
        ``result`` or an ``error`` — then re-raises for the CLI exit-code map."""

        async def _raising() -> AsyncIterator[ApiStreamEvent]:
            yield ApiTextDeltaEvent(text="partial")
            raise RuntimeError("boom")

        out = io.StringIO()
        with pytest.raises(RuntimeError, match="boom"):
            await render_stream_json(_raising(), session_id="sid", stdout=out)

        lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        assert lines[-1]["type"] == "error"
        assert "boom" in lines[-1]["error"]
