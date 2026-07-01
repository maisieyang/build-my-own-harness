"""L4 repair-prompt builder — turns a failed L3 ``VerificationResult`` into
the next prompt for the agent's outer repair loop.

Pure function, no CLI/engine dependency — same style as ``gate.py``'s tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openharness.verification.gate import VerificationResult


def build_repair_prompt(goal: str, attempt: int, verification: VerificationResult) -> str:
    """Build the next repair-loop prompt from the prior failed verification.

    Surfaces the goal, attempt count, and the last (fail-fast-triggering)
    step's command/output/returncode, plus an instruction to apply a
    minimal patch rather than rewriting from scratch. Degrades gracefully
    when ``verification.steps`` is empty (fail-closed zero-steps case), and
    when verification itself passed but the overall attempt still didn't
    succeed (e.g. a non-``end_turn`` stop_reason) -- that is not a command
    failure, so the prompt must not claim one.
    """
    if verification.passed:
        return (
            f"Goal: {goal}\n\n"
            f"This is attempt {attempt} of the repair loop. Verification passed on "
            "the previous attempt, but the run did not finish cleanly (e.g. it was "
            "cut off before completing). Try again and make sure to fully finish "
            "the task this time."
        )

    if not verification.steps:
        return (
            f"Goal: {goal}\n\n"
            f"This is attempt {attempt} of the repair loop.\n\n"
            f"Verification could not run: {verification.feedback}\n\n"
            "Apply the minimal change needed to make verification runnable, "
            "rather than rewriting things from scratch."
        )

    last_step = verification.steps[-1]

    return (
        f"Goal: {goal}\n\n"
        f"This is attempt {attempt} of the repair loop. The previous attempt "
        "failed verification.\n\n"
        f"Failing command: {last_step.command}\n"
        f"Return code: {last_step.returncode}\n"
        f"stdout:\n{last_step.stdout}\n"
        f"stderr:\n{last_step.stderr}\n\n"
        "Apply the minimal patch needed to fix this failure — do not rewrite "
        "the implementation from scratch."
    )
