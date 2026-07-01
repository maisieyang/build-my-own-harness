"""Tests for loop-runtime L4's repair-prompt builder — ``verification/repair.py``.

Pure function: (goal, attempt, prior VerificationResult) -> next prompt text.
No CLI, no engine — testable in isolation, same style as ``gate.py``'s tests.
"""

from __future__ import annotations

from openharness.verification.gate import StepResult, VerificationResult
from openharness.verification.repair import build_repair_prompt


class TestBuildRepairPromptFailure:
    def test_includes_goal_text(self) -> None:
        verification = VerificationResult(
            passed=False,
            steps=(
                StepResult(
                    command="pytest -q",
                    returncode=1,
                    stdout="",
                    stderr="1 failed, 2 passed",
                    duration_s=0.5,
                ),
            ),
            feedback="step failed: pytest -q",
        )
        prompt = build_repair_prompt("fix the failing test", attempt=2, verification=verification)
        assert "fix the failing test" in prompt

    def test_includes_attempt_number(self) -> None:
        verification = VerificationResult(
            passed=False,
            steps=(
                StepResult(command="pytest -q", returncode=1, stdout="", stderr="", duration_s=0.1),
            ),
            feedback="step failed: pytest -q",
        )
        prompt = build_repair_prompt("goal", attempt=3, verification=verification)
        assert "attempt 3" in prompt

    def test_includes_failing_command_and_output(self) -> None:
        verification = VerificationResult(
            passed=False,
            steps=(
                StepResult(
                    command="pytest -q",
                    returncode=1,
                    stdout="collected 3 items",
                    stderr="AssertionError: expected 4 got 3",
                    duration_s=0.5,
                ),
            ),
            feedback="step failed: pytest -q",
        )
        prompt = build_repair_prompt("goal", attempt=2, verification=verification)
        assert "pytest -q" in prompt
        assert "AssertionError: expected 4 got 3" in prompt
        assert "collected 3 items" in prompt
        assert "1" in prompt  # returncode surfaced somewhere

    def test_includes_minimal_patch_instruction(self) -> None:
        verification = VerificationResult(
            passed=False,
            steps=(
                StepResult(command="pytest -q", returncode=1, stdout="", stderr="", duration_s=0.1),
            ),
            feedback="step failed: pytest -q",
        )
        prompt = build_repair_prompt("goal", attempt=2, verification=verification)
        assert "minimal" in prompt.lower()

    def test_uses_last_failing_step_not_earlier_passing_ones(self) -> None:
        verification = VerificationResult(
            passed=False,
            steps=(
                StepResult(command="true", returncode=0, stdout="", stderr="", duration_s=0.01),
                StepResult(
                    command="ruff check",
                    returncode=1,
                    stdout="",
                    stderr="E501 line too long",
                    duration_s=0.2,
                ),
            ),
            feedback="step failed: ruff check",
        )
        prompt = build_repair_prompt("goal", attempt=2, verification=verification)
        assert "ruff check" in prompt
        assert "E501 line too long" in prompt


class TestBuildRepairPromptVerificationPassedButNotSucceeded:
    """Review finding: build_repair_prompt is only called when the overall
    attempt did NOT succeed, but that can happen even when verification
    itself passed (e.g. stop_reason != end_turn). The prompt must not claim
    the (passing) command failed."""

    def test_does_not_claim_failure_when_verification_passed(self) -> None:
        verification = VerificationResult(
            passed=True,
            steps=(
                StepResult(command="pytest -q", returncode=0, stdout="", stderr="", duration_s=0.1),
            ),
            feedback="1 step(s) passed",
        )
        prompt = build_repair_prompt("goal", attempt=2, verification=verification)
        assert "goal" in prompt
        assert "attempt 2" in prompt
        assert "failed" not in prompt.lower()
        assert "Return code: 0" not in prompt


class TestBuildRepairPromptZeroStepsEdgeCase:
    def test_no_steps_configured_does_not_crash(self) -> None:
        verification = VerificationResult(
            passed=False, steps=(), feedback="no verification steps configured"
        )
        prompt = build_repair_prompt("goal", attempt=2, verification=verification)
        assert "goal" in prompt
        assert "no verification steps configured" in prompt
