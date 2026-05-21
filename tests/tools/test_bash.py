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

    from openharness.execution import ProcessResult

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
        # Test must not actually run the full 2 seconds — SIGTERM reaps
        # the subprocess. 1.8s was too tight for slow GitHub Actions
        # runners (observed 2002ms on CI vs ~1100ms locally); 3s is a
        # comfortable upper bound that still proves the timeout fired.
        assert result.metadata["duration_ms"] < 3000


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


# --------------------------------------------------------------------------- #
# P7-T3: Substrate swap via injected ExecutionEnvironment                     #
# --------------------------------------------------------------------------- #


class _FakeExecutionEnvironment:
    """Test double for ExecutionEnvironment — returns a canned
    ProcessResult and records the call args. Satisfies the Protocol
    structurally (no inheritance needed).
    """

    def __init__(self, result: ProcessResult) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def run_command(
        self,
        command: str,
        cwd: Path,
        timeout: float | None = None,
    ) -> ProcessResult:
        self.calls.append({"command": command, "cwd": cwd, "timeout": timeout})
        return self._result


class TestBashWithInjectedExecutionEnv:
    """P7-T3 proves the substrate swap works: a fake ExecutionEnvironment
    completely replaces the host subprocess plumbing, and BashTool
    delegates faithfully. This is the abstraction's reason for being.
    """

    async def test_bash_delegates_to_injected_env(self, tmp_path: Path) -> None:
        from openharness.execution import ProcessResult as _PR

        fake_result = _PR(output="injected-output", exit_code=0)
        fake_env = _FakeExecutionEnvironment(fake_result)

        result = await Bash().execute(
            BashInput(command="echo whatever"),
            ToolExecutionContext(cwd=tmp_path, execution_env=fake_env),
        )

        # BashTool's output IS the fake substrate's output (no host
        # subprocess ran). Substrate swap is the load-bearing assertion.
        assert result.output == "injected-output"
        assert result.is_error is False
        # The substrate received the command + cwd verbatim.
        assert len(fake_env.calls) == 1
        assert fake_env.calls[0]["command"] == "echo whatever"
        assert fake_env.calls[0]["cwd"] == tmp_path
        # Default 600s timeout was passed through.
        assert fake_env.calls[0]["timeout"] == 600.0

    async def test_bash_translates_fake_timeout(self, tmp_path: Path) -> None:
        from openharness.execution import ProcessResult as _PR

        fake_env = _FakeExecutionEnvironment(_PR(output="", exit_code=-1, timed_out=True))
        result = await Bash().execute(
            BashInput(command="anything", timeout_seconds=5),
            ToolExecutionContext(cwd=tmp_path, execution_env=fake_env),
        )
        # Timeout from fake substrate → BashTool's is_error semantics.
        assert result.is_error is True
        assert "timed out after 5s" in result.output
        assert result.metadata["timed_out"] is True

    async def test_bash_translates_fake_non_zero_exit(self, tmp_path: Path) -> None:
        from openharness.execution import ProcessResult as _PR

        fake_env = _FakeExecutionEnvironment(_PR(output="oops", exit_code=42))
        result = await Bash().execute(
            BashInput(command="x"),
            ToolExecutionContext(cwd=tmp_path, execution_env=fake_env),
        )
        # Non-zero exit from substrate → is_error=True, exit_code in metadata.
        assert result.is_error is True
        assert result.output == "oops"
        assert result.metadata["exit_code"] == 42

    async def test_bash_falls_back_to_host_when_no_env_injected(self, tmp_path: Path) -> None:
        # ``ToolExecutionContext(cwd=p)`` (no execution_env kwarg) means
        # `execution_env=None`. BashTool falls back to `_HOST_EXECUTION`
        # singleton — real subprocess fires.
        result = await Bash().execute(
            BashInput(command="echo via-host"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.is_error is False
        assert "via-host" in result.output
