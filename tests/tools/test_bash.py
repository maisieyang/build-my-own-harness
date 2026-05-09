"""Tests for the Bash tool — P2-T3 sub-unit 3d.

Coverage:
- Happy path: exit 0 + stdout
- Non-zero exit → is_error=True, exit_code in metadata
- stderr merged into output (D9.5: single stream back to LLM)
- cwd respected
- Timeout: SIGTERM → SIGKILL, is_error=True with timed_out flag
- Output truncation at MAX_OUTPUT_CHARS with tail marker
- duration_ms populated
- Empty command rejected at Pydantic validation
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from openharness.tools.base import ToolExecutionContext
from openharness.tools.bash import MAX_OUTPUT_CHARS, NO_OUTPUT_SENTINEL, Bash, BashInput

if TYPE_CHECKING:
    from pathlib import Path

# All these tests run real subprocesses. They use only POSIX-portable
# commands or python3 (which is guaranteed by the project's test venv).


@pytest.fixture
def tool() -> Bash:
    return Bash()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=tmp_path)


class TestBashHappyPath:
    async def test_zero_exit_returns_stdout(self, tool: Bash, ctx: ToolExecutionContext) -> None:
        result = await tool.execute(BashInput(command="echo hello"), ctx)
        assert result.is_error is False
        assert result.output == "hello\n"
        assert result.metadata["exit_code"] == 0
        assert result.metadata["duration_ms"] >= 0

    async def test_stderr_is_merged_into_output(
        self, tool: Bash, ctx: ToolExecutionContext
    ) -> None:
        # >&2 redirects to stderr; Bash tool merges stderr into stdout pipe.
        result = await tool.execute(BashInput(command="echo err >&2"), ctx)
        assert result.is_error is False
        assert "err" in result.output

    async def test_cwd_respected(
        self, tool: Bash, ctx: ToolExecutionContext, tmp_path: Path
    ) -> None:
        result = await tool.execute(BashInput(command="pwd"), ctx)
        assert result.is_error is False
        # macOS may resolve /var/folders to /private/var/folders; using
        # tmp_path.name (the unique trailing segment) is robust either way.
        assert tmp_path.name in result.output


class TestBashFailures:
    async def test_non_zero_exit_marks_error(self, tool: Bash, ctx: ToolExecutionContext) -> None:
        result = await tool.execute(BashInput(command="false"), ctx)
        assert result.is_error is True
        assert result.metadata["exit_code"] == 1

    async def test_timeout_terminates_process(self, tool: Bash, ctx: ToolExecutionContext) -> None:
        # 2s sleep, 1s timeout: SIGTERM should reap fast.
        result = await tool.execute(
            BashInput(command="sleep 2", timeout_seconds=1),
            ctx,
        )
        assert result.is_error is True
        assert "timed out" in result.output
        assert result.metadata["timed_out"] is True
        # Test must not actually take 2 seconds.
        assert result.metadata["duration_ms"] < 1800


class TestBashEmptyOutput:
    """Empty stdout returns the ``(no output)`` sentinel — P3-T1.1b.

    Aligns with Read's ``(empty)`` and Grep's ``(no matches)``: a non-empty
    string the LLM can disambiguate from "tool didn't run / returned blank".
    Source: OpenHarness REFERENCE A.3.
    """

    async def test_noop_command_returns_sentinel(
        self, tool: Bash, ctx: ToolExecutionContext
    ) -> None:
        result = await tool.execute(BashInput(command=":"), ctx)
        assert result.is_error is False
        assert result.output == NO_OUTPUT_SENTINEL
        assert result.metadata["exit_code"] == 0
        assert result.metadata["duration_ms"] >= 0

    async def test_true_command_returns_sentinel(
        self, tool: Bash, ctx: ToolExecutionContext
    ) -> None:
        result = await tool.execute(BashInput(command="true"), ctx)
        assert result.is_error is False
        assert result.output == NO_OUTPUT_SENTINEL

    async def test_explicit_empty_echo_returns_sentinel(
        self, tool: Bash, ctx: ToolExecutionContext
    ) -> None:
        # ``printf ""`` writes nothing — different from echo "" which writes
        # a newline. We want exactly the empty-bytes case.
        result = await tool.execute(BashInput(command='printf ""'), ctx)
        assert result.is_error is False
        assert result.output == NO_OUTPUT_SENTINEL

    async def test_failed_command_with_no_output_still_marks_error(
        self, tool: Bash, ctx: ToolExecutionContext
    ) -> None:
        # ``false`` exits 1 with no output. The sentinel applies even though
        # is_error is True — because the empty-output / didn't-run ambiguity
        # is independent of exit code.
        result = await tool.execute(BashInput(command="false"), ctx)
        assert result.is_error is True
        assert result.output == NO_OUTPUT_SENTINEL
        assert result.metadata["exit_code"] == 1

    async def test_whitespace_only_output_is_not_sentinel(
        self, tool: Bash, ctx: ToolExecutionContext
    ) -> None:
        # Strict empty only: ``echo`` produces "\n" — non-empty, so passes
        # through unchanged. The sentinel triggers exclusively on b"".
        result = await tool.execute(BashInput(command="echo"), ctx)
        assert result.is_error is False
        assert result.output == "\n"


class TestBashTruncation:
    async def test_output_truncated_at_max_chars(
        self, tool: Bash, ctx: ToolExecutionContext
    ) -> None:
        # Generate 13,000 'x' chars: > MAX_OUTPUT_CHARS (12,000).
        result = await tool.execute(
            BashInput(command="python3 -c \"import sys; sys.stdout.write('x' * 13000)\""),
            ctx,
        )
        assert result.is_error is False
        # Truncation marker present; total length is the cap plus the marker.
        assert "[truncated" in result.output
        assert result.output.startswith("x" * 100)
        # Capped portion is exactly MAX_OUTPUT_CHARS chars.
        head, _, _ = result.output.partition("\n... [truncated")
        assert len(head) == MAX_OUTPUT_CHARS


class TestBashValidation:
    def test_empty_command_rejected_at_input(self) -> None:
        with pytest.raises(ValidationError):
            BashInput(command="")

    def test_zero_or_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BashInput(command="echo hi", timeout_seconds=0)
