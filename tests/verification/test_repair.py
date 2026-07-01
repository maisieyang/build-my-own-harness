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


class _StubSemanticResult:
    """Minimal GateResult-shaped stub with no ``.steps`` -- mirrors
    ``SemanticGateResult`` (loop-runtime L3') without importing it, to prove
    ``build_repair_prompt`` accepts anything satisfying the Protocol, not
    just ``VerificationResult``."""

    def __init__(self, *, passed: bool, feedback: str) -> None:
        self.passed = passed
        self.feedback = feedback


class TestBuildRepairPromptWithoutStepsDetail:
    """A gate result without ``.steps`` (e.g. the L3' semantic judge) must
    fall back to feedback-only formatting -- the same code path as L3's
    zero-steps case -- instead of crashing on a missing attribute."""

    def test_semantic_result_without_steps_uses_feedback_only(self) -> None:
        result = _StubSemanticResult(passed=False, feedback="the README was not updated")
        prompt = build_repair_prompt("update the README", attempt=2, verification=result)
        assert "update the README" in prompt
        assert "attempt 2" in prompt
        assert "the README was not updated" in prompt

    def test_semantic_result_passed_does_not_claim_failure(self) -> None:
        result = _StubSemanticResult(passed=True, feedback="condition satisfied")
        prompt = build_repair_prompt("goal", attempt=2, verification=result)
        assert "failed" not in prompt.lower()

    def test_semantic_result_failed_does_not_claim_verification_could_not_run(self) -> None:
        """Review finding: a genuinely-failed judge verdict (judge ran fine,
        just scored the condition unmet) was mislabeled 'Verification could
        not run' -- false, since nothing about the judge call itself failed.
        Must be worded as a condition-not-met case instead."""
        result = _StubSemanticResult(passed=False, feedback="README was not touched")
        prompt = build_repair_prompt("update the README", attempt=2, verification=result)
        assert "could not run" not in prompt.lower()
        assert "README was not touched" in prompt
        assert "not yet satisfied" in prompt.lower() or "not met" in prompt.lower()


class _ImposterWithStepsAttribute:
    """Not a ``VerificationResult`` -- but happens to expose a ``.steps``
    attribute of its own. Review finding: ``hasattr``/``getattr(..., default)``
    both swallow ANY AttributeError raised during attribute access, not just
    genuine absence, and (more subtly) either would ALSO duck-type this
    imposter into the hard-gate's per-step formatting path just because it
    happens to have a same-named attribute. The check must be nominal
    (``isinstance(x, VerificationResult)``), not duck-typed, so an unrelated
    type can never be misrouted, and a real ``AttributeError`` from a buggy
    property (if ``VerificationResult`` itself ever had one) would propagate
    normally rather than being caught by ``hasattr``/``getattr``."""

    def __init__(self, *, passed: bool, feedback: str, steps: tuple[object, ...]) -> None:
        self.passed = passed
        self.feedback = feedback
        self.steps = steps


class TestBuildRepairPromptUsesNominalTypeCheck:
    def test_non_verification_result_with_steps_attribute_uses_feedback_only(self) -> None:
        result = _ImposterWithStepsAttribute(
            passed=False, feedback="not a real hard gate", steps=("should not be used",)
        )
        prompt = build_repair_prompt("goal", attempt=2, verification=result)
        assert "not a real hard gate" in prompt
        assert "should not be used" not in prompt
