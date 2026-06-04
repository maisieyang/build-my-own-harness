"""Scorers — Stage 1 (programmatic) + Stage 3 (LLM-judge).

Scorer category:
- :class:`ParseOkScorer`              — structural (象限 I): goal not None
- :class:`GoalKeywordMatchScorer`     — structural (象限 I): substring match
- :class:`CapabilityAssertionsScorer` — structural (象限 I) per-capability
  substring rules; has known semantic ceiling on cross-model runs (Day 1
  §四点九 + Stage 2 N=3 findings) — LLM-judge below addresses where it breaks
- :class:`CapabilityLLMJudgeScorer`   — LLM-judge (象限 IV): per-capability
  semantic evaluation. Selective: only judges T4/T5/T6/T7 (D32.1); other
  capabilities return ``"NA"``. Coexists with substring scorer for
  disagreement-as-calibration-signal (D32.6).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openharness.eval.cassette import (
    CassetteMissingError,
    CassetteMode,
    CassetteStore,
    cassetted_judge_call,
)
from openharness.eval.protocol import Sample, Score
from openharness.eval.rubrics import CAPABILITY_RUBRICS
from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock

if TYPE_CHECKING:
    from openharness.api import SupportsStreamingMessages
    from openharness.protocols.messages import ConversationMessage
    from openharness.services.focus_state import FocusState


class ParseOkScorer:
    """1.0 iff ``output.goal is not None`` (LLM returned parseable JSON)."""

    @property
    def dim(self) -> str:
        return "parse_ok"

    async def score(self, sample: Sample, output: FocusState) -> Score:
        if output.goal is not None:
            return Score(
                dim="parse_ok",
                value=1.0,
                reason="goal parsed",
                case_id=sample.case_id,
            )
        return Score(
            dim="parse_ok",
            value=0.0,
            reason="goal is None — malformed JSON or empty",
            case_id=sample.case_id,
        )


class GoalKeywordMatchScorer:
    """1.0 iff any expected keyword (case-insensitive) appears in goal.

    Step 1 keyword baseline — kept as parallel signal so disagreements with
    capability scorer surface false-positives in real time.
    """

    @property
    def dim(self) -> str:
        return "goal_keyword_match"

    async def score(self, sample: Sample, output: FocusState) -> Score:
        if output.goal is None:
            return Score(
                dim="goal_keyword_match",
                value=0.0,
                reason="goal is None",
                case_id=sample.case_id,
            )
        goal_lower = output.goal.lower()
        matched = [kw for kw in sample.expected_goal_keywords if kw.lower() in goal_lower]
        if matched:
            return Score(
                dim="goal_keyword_match",
                value=1.0,
                reason=f"matched: {', '.join(matched)}",
                case_id=sample.case_id,
            )
        return Score(
            dim="goal_keyword_match",
            value=0.0,
            reason=(f"none of {sample.expected_goal_keywords} in goal={output.goal!r}"),
            case_id=sample.case_id,
        )


class CapabilityAssertionsScorer:
    """1.0 iff ALL capability_assertions pass; 0.0 on first failure.

    The returned ``Score.dim`` is ``f"capability_{sample.capability}"`` —
    per-sample dim variation so the summary table can roll up
    capability_T1 / capability_T2 / ... separately. The scorer's own
    ``.dim`` attribute is the static category name ``"capability"``.

    Day 1 §四点九 finding: ``goal_must_NOT_contain`` substring rules can
    false-positive when LLM includes a forbidden symbol as contextual
    reference rather than as task subject (e.g., correct goal "fix the
    test by repairing _parse_focus_state_response" fails the assertion
    that forbids '_parse_focus_state_response'). LLM-judge scorer (Stage
    3) is the proper resolution.
    """

    @property
    def dim(self) -> str:
        return "capability"

    async def score(self, sample: Sample, output: FocusState) -> Score:
        dim = f"capability_{sample.capability}"
        case_id = sample.case_id
        assertions = sample.capability_assertions

        if output.goal is None:
            return Score(
                dim=dim,
                value=0.0,
                reason="goal is None — capability not evaluable",
                case_id=case_id,
            )

        goal_lower = output.goal.lower()
        next_step_lower = (output.next_step or "").lower()

        must_contain = assertions.get("goal_must_contain", [])
        if must_contain:
            hit = [s for s in must_contain if s.lower() in goal_lower]
            if not hit:
                return Score(
                    dim=dim,
                    value=0.0,
                    reason=(
                        f"FAIL goal_must_contain: none of {must_contain} in goal={output.goal!r}"
                    ),
                    case_id=case_id,
                )

        must_not = assertions.get("goal_must_NOT_contain", [])
        if must_not:
            violated = [s for s in must_not if s.lower() in goal_lower]
            if violated:
                return Score(
                    dim=dim,
                    value=0.0,
                    reason=(
                        f"FAIL goal_must_NOT_contain: found {violated} in goal={output.goal!r}"
                    ),
                    case_id=case_id,
                )

        nxt_must = assertions.get("next_step_must_contain_any_of", [])
        if nxt_must:
            if output.next_step is None:
                return Score(
                    dim=dim,
                    value=0.0,
                    reason=("FAIL next_step_must_contain_any_of: next_step is None"),
                    case_id=case_id,
                )
            hit = [s for s in nxt_must if s.lower() in next_step_lower]
            if not hit:
                return Score(
                    dim=dim,
                    value=0.0,
                    reason=(
                        f"FAIL next_step_must_contain_any_of: none of "
                        f"{nxt_must} in next_step={output.next_step!r}"
                    ),
                    case_id=case_id,
                )

        nxt_not = assertions.get("next_step_must_NOT_contain", [])
        if nxt_not and output.next_step is not None:
            violated = [s for s in nxt_not if s.lower() in next_step_lower]
            if violated:
                return Score(
                    dim=dim,
                    value=0.0,
                    reason=(
                        f"FAIL next_step_must_NOT_contain: found {violated} "
                        f"in next_step={output.next_step!r}"
                    ),
                    case_id=case_id,
                )

        return Score(
            dim=dim,
            value=1.0,
            reason="ALL capability assertions passed",
            case_id=case_id,
        )


# ---------------------------------------------------------------------------
# Stage 3 — LLM-judge scorer
# ---------------------------------------------------------------------------


def _render_messages_for_judge(messages: list[ConversationMessage]) -> str:
    """Render the case conversation for the judge LLM's input payload.

    One line per message: ``[role] {snippet}``. Each block type rendered
    differently. Snippets capped per block to keep judge prompt < 2000 chars
    so total judge call stays well under max_tokens budget.
    """
    parts: list[str] = []
    for msg in messages:
        if not msg.content:
            continue
        block = msg.content[0]
        snippet: str
        if isinstance(block, TextBlock):
            snippet = block.text[:280]
        elif isinstance(block, ToolUseBlock):
            snippet = f"[tool_use] {block.name}({block.input})"[:280]
        elif isinstance(block, ToolResultBlock):
            snippet = f"[tool_result] {block.content[:200]}"
        else:
            snippet = "(unknown block)"
        parts.append(f"[{msg.role}] {snippet}")
    return "\n".join(parts)


class CapabilityLLMJudgeScorer:
    """LLM-judge per-capability semantic quality of (goal, next_step).

    Selective: only judges capabilities registered in :data:`CAPABILITY_RUBRICS`
    (currently T4/T5/T6/T7 per D32.1). Capabilities without a rubric return
    ``value="NA"``. Parse-failed cases (output.goal is None) return ``"NA"``.
    LLM call exceptions / parse failures return ``value="ERROR"`` with
    diagnostic — D32.4 4-state semantics, never collapse failure modes.

    Bias defenses inline in the rubric (D32.5):
    - Verbosity: rubrics say "Length does NOT factor"
    - Format: short-circuit "NA" on parse fail upstream
    - Self-preference: ACCEPTED (single-model env) — judge_model = main model
    - Position: N/A (single-case eval, not pairwise)
    - Calibration drift: out of MVP, Stage 4+ adds score-stamp ritual
    """

    def __init__(
        self,
        api_client: SupportsStreamingMessages,
        model: str,
        rubrics: dict[str, str] | None = None,
        *,
        cassette_store: CassetteStore | None = None,
        cassette_mode: CassetteMode = "live",
    ) -> None:
        """Construct with judge LLM dependencies.

        :param api_client: LLM client used for the judge call. May be the same
            as the main inference client (self-preference accepted per D32.5).
        :param model: Judge model name. Currently same as main model.
        :param rubrics: Override the default :data:`CAPABILITY_RUBRICS`. Used
            for unit testing or per-capability rubric iteration without
            mutating the module-level registry.
        :param cassette_store: Optional cassette backing for the judge LLM
            call (D33). When None, judge calls are always live.
        :param cassette_mode: ``"live"`` / ``"record"`` / ``"replay"``. Per
            D33.2 — only meaningful when ``cassette_store`` is set.
        """
        self._api_client = api_client
        self._model = model
        self._rubrics = rubrics if rubrics is not None else CAPABILITY_RUBRICS
        self._cassette_store = cassette_store
        self._cassette_mode: CassetteMode = cassette_mode

    @property
    def dim(self) -> str:
        return "capability_llm_judge"

    async def score(self, sample: Sample, output: FocusState) -> Score:
        dim = f"capability_{sample.capability}_llm_judge"
        case_id = sample.case_id

        # Short-circuit 1: this capability has no rubric registered (selective).
        rubric = self._rubrics.get(sample.capability)
        if rubric is None:
            return Score(
                dim=dim,
                value="NA",
                reason=f"no rubric registered for {sample.capability}",
                case_id=case_id,
            )

        # Short-circuit 2: parse failed upstream — nothing semantic to judge.
        if output.goal is None:
            return Score(
                dim=dim,
                value="NA",
                reason="goal is None — judge skipped",
                case_id=case_id,
            )

        # Build judge user-message payload: conversation + candidate outputs.
        payload = (
            f"Conversation:\n{_render_messages_for_judge(sample.messages)}\n\n"
            f"Candidate goal: {output.goal!r}\n"
            f"Candidate next_step: {output.next_step!r}\n\n"
            "Apply the rubric. Output the JSON object."
        )

        try:
            raw = await cassetted_judge_call(
                case_id=case_id,
                capability=sample.capability,
                judge_payload=payload,
                system_prompt=rubric,
                api_client=self._api_client,
                model=self._model,
                max_tokens=256,
                timeout_seconds=15.0,
                cassette_mode=self._cassette_mode,
                cassette_store=self._cassette_store,
            )
        except CassetteMissingError as exc:
            # Replay-mode cassette absent — surface as ERROR with clear diag.
            return Score(
                dim=dim,
                value="ERROR",
                reason=f"CASSETTE_MISSING: {exc}",
                case_id=case_id,
            )
        except Exception as exc:
            return Score(
                dim=dim,
                value="ERROR",
                reason=(f"LLM_CALL_EXCEPTION: {type(exc).__name__}: {str(exc)[:120]}"),
                case_id=case_id,
            )

        # Parse judge response — D32.4 ERROR diagnostic per failure mode.
        if not raw.strip():
            return Score(
                dim=dim,
                value="ERROR",
                reason="EMPTY_RESPONSE: judge returned empty",
                case_id=case_id,
            )

        # Strip markdown fence if judge added one (mirrors focus_state parser).
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 2:
                text = "\n".join(lines[1:-1]).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            preview = raw[:120].replace("\n", " ")
            return Score(
                dim=dim,
                value="ERROR",
                reason=f"NON_JSON: {exc.msg}; raw_preview={preview!r}",
                case_id=case_id,
            )

        if not isinstance(data, dict):
            return Score(
                dim=dim,
                value="ERROR",
                reason=f"NOT_DICT: root is {type(data).__name__}",
                case_id=case_id,
            )

        score_int = data.get("score")
        reason_str = data.get("reason", "(no reason)")
        if score_int not in (0, 1):
            return Score(
                dim=dim,
                value="ERROR",
                reason=f"INVALID_SCORE: {score_int!r}",
                case_id=case_id,
            )
        if not isinstance(reason_str, str):
            reason_str = str(reason_str)

        return Score(
            dim=dim,
            value=float(score_int),
            reason=reason_str[:200],
            case_id=case_id,
        )
