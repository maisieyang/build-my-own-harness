"""tool_choice scorers — all `=`-grade deterministic (D41.4: hardest oracle
first; this surface's hardest oracle is exact comparison, zero LLM-judge).

Three dims, never collapsed (playbook §四 4.4):

- ``tool_selection``      — first tool call name == expected (None = zero calls)
- ``forbidden_avoidance`` — no call hits the forbidden list (empty = vacuous pass)
- ``input_construction``  — each expected field hits ≥1 of its any-of substrings
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.eval.protocol import Score

if TYPE_CHECKING:
    from openharness.eval.tool_choice import ToolChoiceOutput, ToolChoiceSample


class ExpectedToolScorer:
    """First tool_use name == expected_tool; expected None asserts restraint."""

    @property
    def dim(self) -> str:
        return "tool_selection"

    async def score(self, sample: ToolChoiceSample, output: ToolChoiceOutput) -> Score:
        if sample.expected_tool is None:
            if output.tool_uses:
                names = ", ".join(tu.name for tu in output.tool_uses)
                return Score(
                    dim=self.dim,
                    value=0.0,
                    reason=f"expected NO tool call, got: {names}",
                    case_id=sample.case_id,
                )
            return Score(
                dim=self.dim,
                value=1.0,
                reason="restraint held: no tool call",
                case_id=sample.case_id,
            )

        if not output.tool_uses:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"expected {sample.expected_tool}, got no tool call",
                case_id=sample.case_id,
            )
        first = output.tool_uses[0]
        if first.name == sample.expected_tool:
            return Score(
                dim=self.dim,
                value=1.0,
                reason=f"first call is {first.name}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=0.0,
            reason=f"expected {sample.expected_tool}, first call is {first.name}",
            case_id=sample.case_id,
        )


class ForbiddenToolScorer:
    """No tool_use may hit the forbidden list (vacuous pass when empty)."""

    @property
    def dim(self) -> str:
        return "forbidden_avoidance"

    async def score(self, sample: ToolChoiceSample, output: ToolChoiceOutput) -> Score:
        hits = [tu.name for tu in output.tool_uses if tu.name in sample.forbidden_tools]
        if hits:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"forbidden tool used: {', '.join(hits)}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason="no forbidden tool used"
            if sample.forbidden_tools
            else "vacuous pass: no forbidden list",
            case_id=sample.case_id,
        )


class InputFieldScorer:
    """Each expected field must contain ≥1 of its any-of substrings
    (case-insensitive) in the first tool call matching expected_tool."""

    @property
    def dim(self) -> str:
        return "input_construction"

    async def score(self, sample: ToolChoiceSample, output: ToolChoiceOutput) -> Score:
        if not sample.expected_input_contains:
            return Score(
                dim=self.dim,
                value=1.0,
                reason="vacuous pass: no input expectations",
                case_id=sample.case_id,
            )
        target = next(
            (tu for tu in output.tool_uses if tu.name == sample.expected_tool),
            None,
        )
        if target is None:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"no {sample.expected_tool} call to inspect inputs on",
                case_id=sample.case_id,
            )
        misses: list[str] = []
        for field, any_of in sample.expected_input_contains.items():
            raw = target.input.get(field)
            haystack = str(raw).lower() if raw is not None else ""
            if not any(needle.lower() in haystack for needle in any_of):
                misses.append(f"{field} (want any of {any_of}, got {raw!r})")
        if misses:
            return Score(
                dim=self.dim,
                value=0.0,
                reason="; ".join(misses),
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason=f"all {len(sample.expected_input_contains)} field(s) hit",
            case_id=sample.case_id,
        )
