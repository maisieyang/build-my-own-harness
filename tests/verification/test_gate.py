"""Tests for the L3 verification gate — ``verification/gate.py``.

Command gate only (grader-agent gate is out of scope, see loop-runtime L3
plan). Fail-fast, argv-form (``shell=False``), fail-closed on zero steps.
"""

from __future__ import annotations

import dataclasses as _dc
from typing import TYPE_CHECKING

import pytest

from openharness.verification.gate import (
    StepResult,
    VerificationResult,
    run_verification_steps,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestRunVerificationStepsHappyPath:
    async def test_single_passing_command(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["true"], cwd=tmp_path)
        assert isinstance(result, VerificationResult)
        assert result.passed is True
        assert len(result.steps) == 1
        assert result.steps[0].returncode == 0
        assert result.steps[0].command == "true"
        assert result.steps[0].timed_out is False

    async def test_all_steps_passing(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["true", "true", "true"], cwd=tmp_path)
        assert result.passed is True
        assert len(result.steps) == 3
        assert "3" in result.feedback


class TestRunVerificationStepsFailure:
    async def test_single_failing_command(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["false"], cwd=tmp_path)
        assert result.passed is False
        assert len(result.steps) == 1
        assert result.steps[0].returncode != 0

    async def test_feedback_names_failing_command(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["false"], cwd=tmp_path)
        assert "false" in result.feedback

    async def test_nonexistent_command_fails(self, tmp_path: Path) -> None:
        result = await run_verification_steps(
            ["this-command-does-not-exist-1234567890"], cwd=tmp_path
        )
        assert result.passed is False
        assert result.steps[0].returncode != 0


class TestFailFast:
    async def test_stops_at_first_failure(self, tmp_path: Path) -> None:
        third_marker = tmp_path / "third-ran.txt"
        commands = [
            "true",
            "false",
            f"python3 -c \"open('{third_marker}', 'w').close()\"",
        ]
        result = await run_verification_steps(commands, cwd=tmp_path)
        assert result.passed is False
        assert len(result.steps) == 2
        assert result.steps[0].returncode == 0
        assert result.steps[1].returncode != 0
        assert not third_marker.exists(), "fail-fast must not run steps after a failure"


class TestFailClosedOnEmpty:
    async def test_empty_command_list_does_not_pass(self, tmp_path: Path) -> None:
        result = await run_verification_steps([], cwd=tmp_path)
        assert result.passed is False
        assert result.steps == ()
        assert result.feedback == "no verification steps configured"


class TestTruncation:
    async def test_long_stdout_is_truncated_with_marker(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["python3 -c \"print('x' * 5000)\""], cwd=tmp_path)
        assert result.passed is True
        stdout = result.steps[0].stdout
        assert len(stdout) < 5000
        assert "truncated" in stdout

    async def test_short_stdout_is_not_truncated(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["echo hello"], cwd=tmp_path)
        assert "hello" in result.steps[0].stdout
        assert "truncated" not in result.steps[0].stdout


class TestMalformedCommands:
    """Review finding: shlex.split()/create_subprocess_exec must never raise
    out of run_verification_steps — a malformed or empty --verify string is
    a failed step (fail-closed), not a crash."""

    async def test_unbalanced_quotes_fails_closed_instead_of_raising(self, tmp_path: Path) -> None:
        result = await run_verification_steps(['pytest -k "foo'], cwd=tmp_path)
        assert result.passed is False
        assert len(result.steps) == 1
        assert result.steps[0].returncode != 0

    async def test_blank_command_fails_closed_instead_of_raising(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["   "], cwd=tmp_path)
        assert result.passed is False
        assert len(result.steps) == 1
        assert result.steps[0].returncode != 0

    async def test_malformed_command_stops_fail_fast(self, tmp_path: Path) -> None:
        third_marker = tmp_path / "third-ran.txt"
        commands = [
            "true",
            'pytest -k "foo',
            f"python3 -c \"open('{third_marker}', 'w').close()\"",
        ]
        result = await run_verification_steps(commands, cwd=tmp_path)
        assert result.passed is False
        assert len(result.steps) == 2
        assert not third_marker.exists()


class TestTimeout:
    async def test_slow_command_times_out_and_fails(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["sleep 5"], cwd=tmp_path, timeout=0.1)
        assert result.passed is False
        assert len(result.steps) == 1
        assert result.steps[0].timed_out is True

    async def test_fast_command_does_not_time_out(self, tmp_path: Path) -> None:
        result = await run_verification_steps(["true"], cwd=tmp_path, timeout=5.0)
        assert result.steps[0].timed_out is False


class TestStepResult:
    def test_is_frozen(self) -> None:
        step = StepResult(
            command="true",
            returncode=0,
            stdout="",
            stderr="",
            duration_s=0.01,
        )
        with pytest.raises(_dc.FrozenInstanceError):
            step.returncode = 1  # type: ignore[misc]

    def test_timed_out_defaults_false(self) -> None:
        step = StepResult(command="true", returncode=0, stdout="", stderr="", duration_s=0.01)
        assert step.timed_out is False


class TestVerificationResult:
    def test_is_frozen(self) -> None:
        result = VerificationResult(passed=True, steps=(), feedback="ok")
        with pytest.raises(_dc.FrozenInstanceError):
            result.passed = False  # type: ignore[misc]
