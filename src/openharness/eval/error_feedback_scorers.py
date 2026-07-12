"""error_feedback scorers — trajectory invariants, deterministic (D41.4:
this surface's hardest oracle is the invariant, not `=` on a single value).

Three dims, never collapsed:

- ``no_verbatim_retry``      — next call ≠ the planted failed call
  (same tool name AND equal input dict = verbatim). A text-only response
  passes: explaining/proposing is a legitimate non-retry.
- ``has_followup``           — the response does SOMETHING: ≥1 tool call or
  non-blank text. Guards against silent abandonment.
- ``no_fabricated_guidance`` — none of the case-authored forbidden
  substrings appear in the text (case-insensitive). Tripwires for the
  F4-style fabrication (e.g. a YAML config format OH doesn't have).
  Vacuous pass on an empty list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.eval.protocol import Score

if TYPE_CHECKING:
    from openharness.eval.error_feedback import ErrorFeedbackOutput, ErrorFeedbackSample


class VerbatimRetryScorer:
    """Next step must not re-issue the exact failed call."""

    @property
    def dim(self) -> str:
        return "no_verbatim_retry"

    async def score(self, sample: ErrorFeedbackSample, output: ErrorFeedbackOutput) -> Score:
        for tu in output.tool_uses:
            if tu.name == sample.planted.name and dict(tu.input) == sample.planted.input:
                return Score(
                    dim=self.dim,
                    value=0.0,
                    reason=f"verbatim retry of failed {tu.name}({tu.input})",
                    case_id=sample.case_id,
                )
        return Score(
            dim=self.dim,
            value=1.0,
            reason="no verbatim retry"
            if output.tool_uses
            else "no tool call at all — vacuous non-retry",
            case_id=sample.case_id,
        )


class FollowupScorer:
    """Some action must follow the error — tool call or substantive text."""

    @property
    def dim(self) -> str:
        return "has_followup"

    async def score(self, sample: ErrorFeedbackSample, output: ErrorFeedbackOutput) -> Score:
        if output.tool_uses:
            return Score(
                dim=self.dim,
                value=1.0,
                reason=f"followed up with {output.tool_uses[0].name}",
                case_id=sample.case_id,
            )
        if output.text.strip():
            return Score(
                dim=self.dim,
                value=1.0,
                reason="followed up with explanatory text",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=0.0,
            reason="empty response — silent abandonment",
            case_id=sample.case_id,
        )


class FabricatedGuidanceScorer:
    """Case-authored forbidden substrings must not appear in the text."""

    @property
    def dim(self) -> str:
        return "no_fabricated_guidance"

    async def score(self, sample: ErrorFeedbackSample, output: ErrorFeedbackOutput) -> Score:
        if not sample.forbidden_text:
            return Score(
                dim=self.dim,
                value=1.0,
                reason="vacuous pass: no forbidden list",
                case_id=sample.case_id,
            )
        lowered = output.text.lower()
        hits = [needle for needle in sample.forbidden_text if needle.lower() in lowered]
        if hits:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"fabrication tripwire hit: {', '.join(hits)}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason="no forbidden guidance in text",
            case_id=sample.case_id,
        )
