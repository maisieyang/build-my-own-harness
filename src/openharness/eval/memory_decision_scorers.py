"""Scorers for the typed durable-memory decision eval."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

from openharness.eval.cassette import (
    CassetteMissingError,
    CassetteMode,
    CassetteStore,
    cassetted_judge_call,
)
from openharness.eval.protocol import Score
from openharness.eval.rubrics import CAPABILITY_RUBRICS

if TYPE_CHECKING:
    from openharness.api import SupportsStreamingMessages
    from openharness.eval.memory_decision import MemoryDecisionOutput, MemoryDecisionSample
    from openharness.protocols.content import ToolUseBlock


def _upserts(tool_uses: tuple[ToolUseBlock, ...]) -> list[ToolUseBlock]:
    return [tool for tool in tool_uses if tool.name == "MemoryUpsert"]


class JudgmentScorer:
    """Did the model correctly choose whether this turn deserves memory?"""

    @property
    def dim(self) -> str:
        return "memory_decision_judgment"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        upserts = _upserts(output.tool_uses)
        if sample.expect_write == bool(upserts):
            reason = (
                "model emitted MemoryUpsert — judgment correct"
                if upserts
                else "model correctly skipped durable memory"
            )
            return Score(dim=self.dim, value=1.0, reason=reason, case_id=sample.case_id)
        reason = (
            "model emitted no MemoryUpsert for a memorable fact"
            if sample.expect_write
            else "model persisted a trivial or ephemeral turn"
        )
        return Score(dim=self.dim, value=0.0, reason=reason, case_id=sample.case_id)


class PayloadValidScorer:
    """Does MemoryUpsert carry the minimal semantic record contract?"""

    @property
    def dim(self) -> str:
        return "memory_decision_payload"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        upserts = _upserts(output.tool_uses)
        if not upserts:
            return Score(
                dim=self.dim,
                value="NA",
                reason="no MemoryUpsert payload to validate",
                case_id=sample.case_id,
            )
        payload = upserts[0].input
        missing = [
            field
            for field in ("name", "description", "type", "body")
            if not isinstance(payload.get(field), str) or not payload[field].strip()
        ]
        allowed_types = {"user", "feedback", "project", "reference"}
        if payload.get("type") not in allowed_types:
            missing.append("type(valid category)")
        if missing:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"invalid MemoryUpsert fields: {', '.join(missing)}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason=f"valid typed payload for {payload['name']!r}",
            case_id=sample.case_id,
        )


class PersistenceIntegrityScorer:
    """Did the typed operation persist its record without dropping seeds?"""

    @property
    def dim(self) -> str:
        return "memory_decision_persistence"

    async def score(self, sample: MemoryDecisionSample, output: MemoryDecisionOutput) -> Score:
        upserts = _upserts(output.tool_uses)
        if not upserts:
            return Score(
                dim=self.dim,
                value="NA",
                reason="no MemoryUpsert; judgment dimension owns the skip",
                case_id=sample.case_id,
            )
        seed_names = {
            filename.removesuffix(".md")
            for filename in sample.pre_populated_files
            if filename.endswith(".md") and filename != "MEMORY.md"
        }
        chosen_names = {str(tool.input.get("name")) for tool in upserts if tool.input.get("name")}
        persisted = set(output.persisted_names)
        missing_seeds = sorted(seed_names - persisted)
        missing_writes = sorted(chosen_names - persisted)
        expected_seed_hashes = {
            filename: sha256(content.encode("utf-8")).hexdigest()
            for filename, content in sample.pre_populated_files.items()
            if filename.endswith(".md") and filename != "MEMORY.md"
        }
        actual_record_hashes = dict(output.persisted_record_hashes)
        missing_seed_fingerprints = sorted(
            filename for filename in expected_seed_hashes if filename not in actual_record_hashes
        )
        changed_seeds = sorted(
            filename
            for filename, expected_hash in expected_seed_hashes.items()
            if filename in actual_record_hashes and actual_record_hashes[filename] != expected_hash
        )
        if missing_seeds or missing_writes or missing_seed_fingerprints or changed_seeds:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=(
                    f"persistence mismatch: missing seeds={missing_seeds}, "
                    f"missing upserts={missing_writes}, "
                    f"missing seed fingerprints={missing_seed_fingerprints}, "
                    f"changed seeds={changed_seeds}"
                ),
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason=(
                f"persisted {len(chosen_names)} upsert(s); "
                f"preserved {len(seed_names)} seed record(s)"
            ),
            case_id=sample.case_id,
        )


class MemoryTypeLLMJudgeScorer:
    """Judge whether the model chose a defensible typed-memory category."""

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
        rubric = self._rubrics.get(sample.capability)
        if rubric is None:
            return Score(
                dim=self.dim,
                value="NA",
                reason=f"no rubric registered for capability {sample.capability!r}",
                case_id=sample.case_id,
            )
        if sample.expected_memory_type is None:
            return Score(
                dim=self.dim,
                value="NA",
                reason="no expected type for skip case",
                case_id=sample.case_id,
            )
        upserts = _upserts(output.tool_uses)
        if not upserts:
            return Score(
                dim=self.dim,
                value="NA",
                reason="no MemoryUpsert type to judge",
                case_id=sample.case_id,
            )
        chosen_type = upserts[0].input.get("type")
        payload = (
            f"User message:\n{sample.user_msg}\n\n"
            f"Expected baseline type: {sample.expected_memory_type!r}\n"
            f"Model's chosen type: {chosen_type!r}\n\n"
            "Apply the rubric. Output the JSON object."
        )
        try:
            raw = await cassetted_judge_call(
                case_id=sample.case_id,
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
                dim=self.dim,
                value="ERROR",
                reason=f"CASSETTE_MISSING: {exc}",
                case_id=sample.case_id,
            )
        except Exception as exc:
            return Score(
                dim=self.dim,
                value="ERROR",
                reason=f"LLM_CALL_EXCEPTION: {type(exc).__name__}: {str(exc)[:120]}",
                case_id=sample.case_id,
            )

        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]).strip() if len(lines) >= 2 else ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return Score(
                dim=self.dim,
                value="ERROR",
                reason=f"NON_JSON: {exc.msg}; raw_preview={raw[:120]!r}",
                case_id=sample.case_id,
            )
        if not isinstance(data, dict) or data.get("score") not in (0, 1):
            return Score(
                dim=self.dim,
                value="ERROR",
                reason=f"INVALID_JUDGE_PAYLOAD: {data!r}",
                case_id=sample.case_id,
            )
        reason = data.get("reason", "(no reason)")
        return Score(
            dim=self.dim,
            value=float(data["score"]),
            reason=str(reason)[:200],
            case_id=sample.case_id,
        )
