"""L4 repair-prompt builder — turns a failed verification gate result into
the next prompt for the agent's outer repair loop.

Accepts anything satisfying the minimal ``GateResult`` protocol (``passed``,
``feedback``), so both L3's deterministic ``VerificationResult`` (adds
per-step command/stdout/stderr detail) and L3''s ``SemanticGateResult``
(judge reason only, no ``.steps``) drive the same repair loop through this
one function.

Pure function, no CLI/engine dependency — same style as ``gate.py``'s tests.
"""

from __future__ import annotations

from typing import Protocol

from openharness.verification.gate import VerificationResult


class GateResult(Protocol):
    """Minimal shape both ``VerificationResult`` and ``SemanticGateResult``
    satisfy structurally — no explicit inheritance needed.

    Declared as read-only ``@property`` (not plain attributes): both
    concrete types are frozen dataclasses, so their fields are read-only:
    a plain-attribute Protocol member expects a settable variable and
    would reject them under ``mypy --strict``.
    """

    @property
    def passed(self) -> bool: ...

    @property
    def feedback(self) -> str: ...


def build_repair_prompt(goal: str, attempt: int, verification: GateResult) -> str:
    """Build the next repair-loop prompt from the prior failed gate result.

    Surfaces the goal and attempt count, plus — when the gate result carries
    per-step detail (i.e. L3's hard command gate) — the last (fail-fast
    -triggering) step's command/output/returncode. Gate results without
    step-level detail (L3''s soft judge gate, or L3's own zero-steps case)
    fall back to just the gate's ``feedback`` text. Either way the prompt
    ends with an instruction to apply a minimal patch rather than rewriting
    from scratch. Degrades gracefully when verification itself passed but
    the overall attempt still didn't succeed (e.g. a non-``end_turn``
    stop_reason) -- that is not a verification failure, so the prompt must
    not claim one.
    """
    if verification.passed:
        return (
            f"Goal: {goal}\n\n"
            f"This is attempt {attempt} of the repair loop. Verification passed on "
            "the previous attempt, but the run did not finish cleanly (e.g. it was "
            "cut off before completing). Try again and make sure to fully finish "
            "the task this time."
        )

    # Nominal check (isinstance), not duck-typing on attribute presence:
    # ``hasattr``/``getattr(..., default)`` both swallow ANY AttributeError
    # raised during attribute access, not just genuine absence -- a real bug
    # in a future GateResult-satisfying type's ``.steps`` would silently look
    # like "no steps" instead of propagating. isinstance sidesteps this
    # entirely: we only ever touch ``.steps`` once we know the concrete type
    # actually is ``VerificationResult``.
    if isinstance(verification, VerificationResult):
        steps = verification.steps
        if not steps:
            return (
                f"Goal: {goal}\n\n"
                f"This is attempt {attempt} of the repair loop.\n\n"
                f"Verification could not run: {verification.feedback}\n\n"
                "Apply the minimal change needed to make verification runnable, "
                "rather than rewriting things from scratch."
            )

        last_step = steps[-1]

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

    # No ``.steps`` at all -- the L3' semantic judge ran fine and simply
    # scored the condition as unmet. That is NOT "verification could not
    # run" -- the prior wording (before this fix) falsely blamed a broken
    # verification harness for what is really a genuine "not yet satisfied".
    return (
        f"Goal: {goal}\n\n"
        f"This is attempt {attempt} of the repair loop. An independent judge "
        "evaluated the completion condition and found it not yet satisfied.\n\n"
        f"Judge's reason: {verification.feedback}\n\n"
        "Apply the minimal change needed to satisfy the condition, rather than "
        "rewriting things from scratch."
    )
