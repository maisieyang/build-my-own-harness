"""skill_trigger scorers — all `=`-grade deterministic (D41.4: this
surface's hardest oracle is exact comparison on (called?, slug)).

Two dims, never collapsed:

- ``trigger_decision`` — LoadSkill called when a slug is expected;
  NOT called when ``expected_slug`` is None. Restraint semantics differ
  from tool_choice TC5: other tool calls are legitimate (the task may
  need Edit/Bash) — only the LoadSkill decision is under test.
- ``slug_selection``   — first LoadSkill call's ``name`` == expected slug,
  exact match (the catalog is case-sensitive; ``parse_credit_report`` for
  ``parse-credit-report`` is a miss the tool would bounce). Vacuous pass
  on restraint cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.eval.protocol import Score

if TYPE_CHECKING:
    from openharness.eval.skill_trigger import SkillTriggerOutput, SkillTriggerSample

_LOAD_SKILL = "LoadSkill"


def _load_skill_calls(output: SkillTriggerOutput) -> list[str]:
    return [str(tu.input.get("name", "")) for tu in output.tool_uses if tu.name == _LOAD_SKILL]


class TriggerDecisionScorer:
    """Called-when-expected / silent-when-not, on the LoadSkill axis only."""

    @property
    def dim(self) -> str:
        return "trigger_decision"

    async def score(self, sample: SkillTriggerSample, output: SkillTriggerOutput) -> Score:
        calls = _load_skill_calls(output)
        if sample.expected_slug is None:
            if calls:
                return Score(
                    dim=self.dim,
                    value=0.0,
                    reason=f"expected NO LoadSkill, got LoadSkill({', '.join(calls)})",
                    case_id=sample.case_id,
                )
            return Score(
                dim=self.dim,
                value=1.0,
                reason="restraint held: no LoadSkill call (other tools allowed)",
                case_id=sample.case_id,
            )
        if not calls:
            others = ", ".join(tu.name for tu in output.tool_uses) or "no tool call"
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"expected LoadSkill, got: {others}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason="LoadSkill called",
            case_id=sample.case_id,
        )


class SlugSelectionScorer:
    """First LoadSkill call's slug must == expected, exact (case/separator)."""

    @property
    def dim(self) -> str:
        return "slug_selection"

    async def score(self, sample: SkillTriggerSample, output: SkillTriggerOutput) -> Score:
        if sample.expected_slug is None:
            return Score(
                dim=self.dim,
                value=1.0,
                reason="vacuous pass: restraint case",
                case_id=sample.case_id,
            )
        calls = _load_skill_calls(output)
        if not calls:
            return Score(
                dim=self.dim,
                value=0.0,
                reason="no LoadSkill call to inspect slug on",
                case_id=sample.case_id,
            )
        first = calls[0]
        if first == sample.expected_slug:
            return Score(
                dim=self.dim,
                value=1.0,
                reason=f"exact slug: {first}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=0.0,
            reason=f"expected {sample.expected_slug!r}, got {first!r}",
            case_id=sample.case_id,
        )
