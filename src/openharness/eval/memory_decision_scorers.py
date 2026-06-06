"""Scorers for the memory_decision eval — Stage 1 (programmatic) +
Stage 3 (LLM-judge). Decision surface #4 (inline decision) per
``decisions/35-eval-coverage-map.md`` §D35.5 P0.

Scorer category map:

- :class:`JudgmentScorer`              — programmatic (象限 I): did model
  correctly judge "should write memory"? (compare ``expect_write`` to
  observed Write tool calls)
- :class:`FrontmatterValidScorer`      — programmatic (象限 I): when the
  model wrote a ``.md``, does it carry valid frontmatter (name +
  description + metadata.type)?
- :class:`IndexUpdateScorer`           — programmatic (象限 I): cold-start
  accepts Write or Edit on MEMORY.md; warm-start requires Edit
  (Write-with-preservation also OK); silent miss = drift FAIL.
- :class:`NoDestructiveOverwriteScorer` — programmatic (象限 I): warm-start
  Write on MEMORY.md must preserve ≥50% pre-existing entries; otherwise
  FAIL ("canonical destructive drift" failure mode from the 2026-06-05
  spike).
- :class:`MemoryTypeLLMJudgeScorer`    — LLM-judge (象限 IV): classification
  of the written memory's frontmatter ``type`` against the rubric for
  the sample's ``capability`` family (M-judge-preference accepts user
  OR feedback, etc.). Coexists with the programmatic
  ``expected_memory_type`` baseline so disagreement surfaces as
  calibration signal (D32.6).

Composite verdict (4-state) is computed in the spike entry script
when summary-printing — scorers stay independent so they can be
inspected one-by-one and so a future rubric refinement on one axis
doesn't drag the others.
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
from openharness.eval.memory_decision import (
    FRONTMATTER_RE,
    MemoryDecisionOutput,
    MemoryDecisionSample,
)
from openharness.eval.protocol import Score
from openharness.eval.rubrics import CAPABILITY_RUBRICS

if TYPE_CHECKING:
    from openharness.api import SupportsStreamingMessages
    from openharness.protocols.content import ToolUseBlock


# ---------------------------------------------------------------------------
# Helpers (private — shared parsing logic)
# ---------------------------------------------------------------------------


def _writes_to_memory_md(tool_uses: tuple[ToolUseBlock, ...]) -> list[ToolUseBlock]:
    return [
        t for t in tool_uses if t.name == "Write" and "MEMORY.md" in t.input.get("file_path", "")
    ]


def _edits_to_memory_md(tool_uses: tuple[ToolUseBlock, ...]) -> list[ToolUseBlock]:
    return [
        t for t in tool_uses if t.name == "Edit" and "MEMORY.md" in t.input.get("file_path", "")
    ]


def _writes_to_target_md(tool_uses: tuple[ToolUseBlock, ...]) -> list[ToolUseBlock]:
    """Write calls that target a ``.md`` file other than MEMORY.md."""
    out: list[ToolUseBlock] = []
    for t in tool_uses:
        if t.name != "Write":
            continue
        path = t.input.get("file_path", "")
        if path.endswith(".md") and "MEMORY.md" not in path:
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# Judgment — did model correctly decide to write or skip?
# ---------------------------------------------------------------------------


class JudgmentScorer:
    """1.0 iff the judgment (write vs skip) matches ``expect_write``.

    PASS conditions:
    - ``expect_write=True``  → at least one Write call targeting a ``.md``
      file (MEMORY.md or otherwise)
    - ``expect_write=False`` → zero Write/Edit calls anywhere

    This is the "right kind of action" dim — it ignores correctness of
    the resulting file content (FrontmatterValidScorer covers that)
    and of the index update (IndexUpdateScorer covers that).
    """

    @property
    def dim(self) -> str:
        return "memory_decision_judgment"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        any_write = any(t.name == "Write" for t in output.tool_uses)
        any_edit = any(t.name == "Edit" for t in output.tool_uses)
        wrote = any_write or any_edit

        if sample.expect_write:
            if any_write:
                return Score(
                    dim=self.dim,
                    value=1.0,
                    reason="model emitted Write — judgment correct",
                    case_id=sample.case_id,
                )
            return Score(
                dim=self.dim,
                value=0.0,
                reason=("model emitted no Write — judgment missed a memorable moment"),
                case_id=sample.case_id,
            )

        # expect_write is False
        if not wrote:
            return Score(
                dim=self.dim,
                value=1.0,
                reason="model correctly skipped — no Write/Edit emitted",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=0.0,
            reason=(
                f"model wrote memory when input was trivial "
                f"(Writex{sum(1 for t in output.tool_uses if t.name == 'Write')}, "
                f"Editx{sum(1 for t in output.tool_uses if t.name == 'Edit')})"
            ),
            case_id=sample.case_id,
        )


# ---------------------------------------------------------------------------
# Frontmatter validity
# ---------------------------------------------------------------------------


class FrontmatterValidScorer:
    """When a ``.md`` body Write occurred, is the frontmatter valid?

    Returns ``"NA"`` when no target Write happened (the judgment scorer
    will already have flagged the skip / drift). Returns 1.0 if the
    first target Write's content matches :data:`FRONTMATTER_RE`,
    0.0 if malformed.
    """

    @property
    def dim(self) -> str:
        return "memory_decision_frontmatter"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        target_writes = _writes_to_target_md(output.tool_uses)
        if not target_writes:
            return Score(
                dim=self.dim,
                value="NA",
                reason="no target .md Write to validate",
                case_id=sample.case_id,
            )

        content = target_writes[0].input.get("content", "")
        match = FRONTMATTER_RE.search(content)
        if match is None:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=(f"frontmatter malformed; content preview={content[:160]!r}"),
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason=(
                f"valid frontmatter (name={match.group('name')!r}, type={match.group('type')!r})"
            ),
            case_id=sample.case_id,
        )


# ---------------------------------------------------------------------------
# Index update
# ---------------------------------------------------------------------------


class IndexUpdateScorer:
    """Did the model touch MEMORY.md appropriately for the sample shape?

    Cold-start (no MEMORY.md exists yet):
    - PASS: Write or Edit on MEMORY.md, OR no Write/Edit at all when
      ``expect_write=False``.
    - FAIL: target ``.md`` written but MEMORY.md untouched (drift).

    Warm-start (MEMORY.md pre-populated):
    - PASS: Edit on MEMORY.md (canonical), OR Write on MEMORY.md
      that PRESERVES at least one anchor from the seed (the spike
      established this as "wasteful but non-destructive").
    - FAIL: target ``.md`` written + MEMORY.md untouched (drift), OR
      Write on MEMORY.md that drops all seeded anchors (destructive
      overwrite — :class:`NoDestructiveOverwriteScorer` flags that
      separately on a dedicated dim).

    Trivial-skip: returns NA — the dim is meaningless when no write
    is expected.
    """

    @property
    def dim(self) -> str:
        return "memory_decision_index_update"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        if not sample.expect_write:
            return Score(
                dim=self.dim,
                value="NA",
                reason="trivial-skip sample — no index update expected",
                case_id=sample.case_id,
            )

        target_writes = _writes_to_target_md(output.tool_uses)
        if not target_writes:
            # Judgment scorer will flag this — return NA here so the
            # dim isn't double-counted.
            return Score(
                dim=self.dim,
                value="NA",
                reason="no target .md Write (judgment dim already reflects this)",
                case_id=sample.case_id,
            )

        memory_writes = _writes_to_memory_md(output.tool_uses)
        memory_edits = _edits_to_memory_md(output.tool_uses)
        memory_touched = bool(memory_writes or memory_edits)

        if not memory_touched:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=("wrote target .md but no action on MEMORY.md — canonical index drift"),
                case_id=sample.case_id,
            )

        if sample.shape == "warm-start" and memory_writes and not memory_edits:
            # Write on existing file — preservation check delegated to
            # NoDestructiveOverwriteScorer; this dim only needs "did
            # SOMETHING touch the index". Mark PARTIAL = 0.5.
            return Score(
                dim=self.dim,
                value=0.5,
                reason=(
                    "warm-start: MEMORY.md touched via Write (Edit was the "
                    "ideal action); preservation checked separately"
                ),
                case_id=sample.case_id,
            )

        return Score(
            dim=self.dim,
            value=1.0,
            reason=(f"MEMORY.md updated ({'Edit' if memory_edits else 'Write'} on {sample.shape})"),
            case_id=sample.case_id,
        )


# ---------------------------------------------------------------------------
# Destructive overwrite (warm-start specific)
# ---------------------------------------------------------------------------


class NoDestructiveOverwriteScorer:
    """Warm-start: does any Write on MEMORY.md preserve seeded entries?

    The 2026-06-05 spike's S5 case revealed qwen3.7-max emitting Write
    on a warm MEMORY.md that wiped 3 seeded entries — silent data
    loss. This scorer guards against that specific failure mode.

    PASS conditions:
    - No Write on MEMORY.md (Edit-only path, or no index touch at all
      — the latter is judged by IndexUpdateScorer; we return NA here)
    - Cold-start sample (no seed to preserve)
    - Warm-start Write on MEMORY.md whose content includes ≥1 anchor
      substring from any seeded ``.md`` filename or MEMORY.md line

    FAIL: warm-start Write on MEMORY.md with zero preserved anchors.
    """

    @property
    def dim(self) -> str:
        return "memory_decision_no_overwrite"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        if sample.shape != "warm-start":
            return Score(
                dim=self.dim,
                value="NA",
                reason="not warm-start — overwrite check N/A",
                case_id=sample.case_id,
            )

        memory_writes = _writes_to_memory_md(output.tool_uses)
        if not memory_writes:
            return Score(
                dim=self.dim,
                value="NA",
                reason="no Write on MEMORY.md (Edit / skip path safe by construction)",
                case_id=sample.case_id,
            )

        # Compute "anchor strings" from the seeded files: filenames
        # (minus .md) + each non-empty line of MEMORY.md. A
        # well-behaved Write should keep most of these.
        anchors: list[str] = []
        for fname, content in sample.pre_populated_files.items():
            if fname == "MEMORY.md":
                anchors.extend(line.strip() for line in content.splitlines() if line.strip())
            elif fname.endswith(".md"):
                anchors.append(fname[: -len(".md")])

        if not anchors:
            # Warm sample with no anchors to preserve is a degenerate
            # configuration — surface as NA so the dim isn't gamed.
            return Score(
                dim=self.dim,
                value="NA",
                reason="warm sample seeded no anchors — overwrite check vacuous",
                case_id=sample.case_id,
            )

        new_content = memory_writes[0].input.get("content", "")
        preserved = sum(1 for a in anchors if a in new_content)
        ratio = preserved / len(anchors)

        if ratio >= 0.5:
            return Score(
                dim=self.dim,
                value=1.0,
                reason=(
                    f"Write on MEMORY.md preserved {preserved}/{len(anchors)} "
                    f"anchors (≥50% threshold)"
                ),
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=0.0,
            reason=(
                f"DESTRUCTIVE OVERWRITE: Write on MEMORY.md preserved only "
                f"{preserved}/{len(anchors)} anchors ({ratio:.0%}) — pre-"
                f"existing entries dropped"
            ),
            case_id=sample.case_id,
        )


# ---------------------------------------------------------------------------
# Type LLM-judge
# ---------------------------------------------------------------------------


class MemoryTypeLLMJudgeScorer:
    """Per-sample LLM judge of the written memory's frontmatter type.

    The sample's ``capability`` field selects which rubric in
    :data:`CAPABILITY_RUBRICS` to apply. Rubrics are written to accept
    multiple defensible readings — e.g., "I prefer terse responses"
    may be ``user`` (preference about communication style) OR
    ``feedback`` (guidance on how to approach work). The judge returns
    1 when the model's choice is defensible under the rubric, 0
    otherwise.

    Returns ``"NA"`` when:
    - the sample has no expected memory_type (trivial-skip)
    - no target ``.md`` Write occurred (judgment dim reflects the
      miss)
    - the rubric is not registered for the sample's capability

    Self-preference bias: judge model = main model (D32.5 stance
    inherited from focus_state's CapabilityLLMJudgeScorer). When the
    eval is later used for cross-model comparison (D35.8), a separate
    decision doc lifts this constraint.
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
        self._api_client = api_client
        self._model = model
        self._rubrics = rubrics if rubrics is not None else CAPABILITY_RUBRICS
        self._cassette_store = cassette_store
        self._cassette_mode: CassetteMode = cassette_mode

    @property
    def dim(self) -> str:
        return "memory_decision_type_judge"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        dim = self.dim
        case_id = sample.case_id

        rubric = self._rubrics.get(sample.capability)
        if rubric is None:
            return Score(
                dim=dim,
                value="NA",
                reason=f"no rubric registered for capability {sample.capability!r}",
                case_id=case_id,
            )

        if sample.expected_memory_type is None:
            return Score(
                dim=dim,
                value="NA",
                reason="no expected_memory_type (trivial-skip family)",
                case_id=case_id,
            )

        target_writes = _writes_to_target_md(output.tool_uses)
        if not target_writes:
            return Score(
                dim=dim,
                value="NA",
                reason="no .md write to judge",
                case_id=case_id,
            )

        content = target_writes[0].input.get("content", "")
        match = FRONTMATTER_RE.search(content)
        if match is None:
            return Score(
                dim=dim,
                value="NA",
                reason="frontmatter malformed — type unreadable",
                case_id=case_id,
            )

        chosen_type = match.group("type")
        payload = (
            f"User message:\n{sample.user_msg}\n\n"
            f"Expected baseline type: {sample.expected_memory_type!r}\n"
            f"Model's chosen type: {chosen_type!r}\n\n"
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

        if not raw.strip():
            return Score(
                dim=dim,
                value="ERROR",
                reason="EMPTY_RESPONSE: judge returned empty",
                case_id=case_id,
            )

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
