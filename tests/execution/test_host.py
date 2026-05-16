"""Tests for ``HostExecution`` — P7-T1.1b.

Behavior parity verification:these tests exercise the same surface
as ``tests/tools/test_bash.py`` does on the Bash tool. The
``HostExecution`` body is extracted byte-identically from
``BashTool.execute``,so anything the current Bash tests cover at
the subprocess level, these tests must cover here too. (The
BashTool tests themselves keep passing after P7-T3 refactor — that's
the actual load-bearing assertion. These tests are the substrate's
own unit coverage.)
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from openharness.execution.base import ProcessResult
from openharness.execution.host import _HOST_EXECUTION, HostExecution

if TYPE_CHECKING:
    from pathlib import Path


class TestHostExecutionHappyPath:
    async def test_echo_returns_stdout(self, tmp_path: Path) -> None:
        env = HostExecution()
        result = await env.run_command("echo hello", cwd=tmp_path)
        assert isinstance(result, ProcessResult)
        assert "hello" in result.output
        assert result.exit_code == 0
        assert result.timed_out is False

    async def test_cwd_is_honored(self, tmp_path: Path) -> None:
        env = HostExecution()
        # ``pwd`` should reflect the cwd we passed.
        result = await env.run_command("pwd", cwd=tmp_path)
        assert result.exit_code == 0
        # tmp_path may be resolved differently (e.g., /private prefix on macOS)
        # — substring match against the directory's basename is robust.
        assert tmp_path.name in result.output

    async def test_non_zero_exit_propagates(self, tmp_path: Path) -> None:
        env = HostExecution()
        result = await env.run_command("false", cwd=tmp_path)
        assert result.exit_code != 0
        assert result.timed_out is False

    async def test_nonexistent_command_returns_non_zero(self, tmp_path: Path) -> None:
        # A command that doesn't exist exits with 127 under /bin/sh.
        env = HostExecution()
        result = await env.run_command(
            "this-command-does-not-exist-1234567890",
            cwd=tmp_path,
        )
        assert result.exit_code != 0


class TestHostExecutionStderrMerged:
    async def test_stderr_appears_in_output(self, tmp_path: Path) -> None:
        # ``>&2 echo X`` writes "X" to stderr; merged pipe should
        # surface it in ``output``.
        env = HostExecution()
        result = await env.run_command(">&2 echo error-text", cwd=tmp_path)
        assert "error-text" in result.output
        # Exit is still 0 because echo succeeded.
        assert result.exit_code == 0

    async def test_chronological_merge_preserved(self, tmp_path: Path) -> None:
        # stdout then stderr then stdout should appear in that order.
        env = HostExecution()
        result = await env.run_command(
            "echo first; >&2 echo second; echo third",
            cwd=tmp_path,
        )
        assert result.exit_code == 0
        first_idx = result.output.find("first")
        second_idx = result.output.find("second")
        third_idx = result.output.find("third")
        assert 0 <= first_idx < second_idx < third_idx


class TestHostExecutionTimeout:
    async def test_slow_command_times_out(self, tmp_path: Path) -> None:
        env = HostExecution()
        # ``sleep 5`` with 0.1s timeout — substrate kills the process.
        result = await env.run_command("sleep 5", cwd=tmp_path, timeout=0.1)
        assert result.timed_out is True
        # Sentinel exit code per HostExecution convention.
        assert result.exit_code == -1
        # Output is empty because the process was killed mid-execution.
        assert result.output == ""

    async def test_fast_command_does_not_time_out(self, tmp_path: Path) -> None:
        env = HostExecution()
        # ``true`` returns immediately; 5s timeout never fires.
        result = await env.run_command("true", cwd=tmp_path, timeout=5.0)
        assert result.timed_out is False
        assert result.exit_code == 0

    async def test_no_timeout_means_no_enforcement(self, tmp_path: Path) -> None:
        env = HostExecution()
        # ``true`` returns immediately; ``timeout=None`` should still work.
        result = await env.run_command("true", cwd=tmp_path, timeout=None)
        assert result.timed_out is False
        assert result.exit_code == 0


class TestHostExecutionSigtermEscalation:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Signal escalation semantics differ on Windows",
    )
    async def test_sigterm_ignoring_process_gets_killed(self, tmp_path: Path) -> None:
        # A process that catches SIGTERM and keeps running must still
        # get killed via SIGKILL after the grace period. We use a Python
        # one-liner to ignore SIGTERM and sleep.
        env = HostExecution()
        # Grace period is 2s; timeout fires at 0.1s; total wall time
        # should be ~2.1-2.2s before substrate gives up.
        result = await env.run_command(
            'python3 -c "import signal,time; '
            "signal.signal(signal.SIGTERM, lambda *a: None); "
            'time.sleep(30)"',
            cwd=tmp_path,
            timeout=0.1,
        )
        assert result.timed_out is True


class TestHostExecutionSingleton:
    def test_module_singleton_is_HostExecution(self) -> None:
        assert isinstance(_HOST_EXECUTION, HostExecution)

    def test_singleton_is_stable(self) -> None:
        # Importing twice returns the same instance.
        from openharness.execution.host import _HOST_EXECUTION as same

        assert same is _HOST_EXECUTION


class TestProcessResult:
    def test_required_fields(self) -> None:
        r = ProcessResult(output="hi", exit_code=0)
        assert r.output == "hi"
        assert r.exit_code == 0
        assert r.timed_out is False  # default

    def test_timed_out_explicit(self) -> None:
        r = ProcessResult(output="", exit_code=-1, timed_out=True)
        assert r.timed_out is True

    def test_is_frozen(self) -> None:
        import dataclasses as _dc

        r = ProcessResult(output="hi", exit_code=0)
        with pytest.raises(_dc.FrozenInstanceError):
            r.output = "bye"  # type: ignore[misc]
