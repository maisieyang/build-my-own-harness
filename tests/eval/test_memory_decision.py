"""Deterministic contract tests for the typed memory_decision eval."""

from __future__ import annotations

from pathlib import Path

from openharness.eval.memory_decision import (
    MemoryDecisionOutput,
    MemoryDecisionSample,
    _execute_tool_call,
    _memory_registry,
    load_memory_decision_dataset,
)
from openharness.eval.memory_decision_scorers import (
    JudgmentScorer,
    PayloadValidScorer,
    PersistenceIntegrityScorer,
)
from openharness.memory import FilesystemMemoryStore
from openharness.protocols.content import ToolUseBlock

DATASET = Path(__file__).parents[2] / "evals" / "memory_decision" / "dataset.yaml"


def _sample(*, expect_write: bool = True) -> MemoryDecisionSample:
    return MemoryDecisionSample(
        case_id="typed-contract",
        capability="M-judge-project",
        shape="warm-start",
        user_msg="The release freeze starts on 2026-08-20.",
        expect_write=expect_write,
        expected_memory_type="project" if expect_write else None,
        pre_populated_files={"existing.md": ""},
    )


def _upsert() -> ToolUseBlock:
    return ToolUseBlock(
        id="memory-1",
        name="MemoryUpsert",
        input={
            "name": "release-freeze",
            "description": "Release freeze starts on 2026-08-20",
            "type": "project",
            "body": "Non-critical merges freeze on 2026-08-20.",
        },
    )


class TestTypedEvalExecution:
    async def test_executes_real_upsert_and_generates_index(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        result = await _execute_tool_call(_upsert(), _memory_registry(store), tmp_path)

        assert result.is_error is False
        assert store.get("release-freeze") is not None
        assert "release-freeze" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")

    async def test_rejects_retired_general_write_surface(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        retired = ToolUseBlock(
            id="write-1",
            name="Write",
            input={"file_path": str(tmp_path / "memory.md"), "content": "legacy"},
        )

        result = await _execute_tool_call(retired, _memory_registry(store), tmp_path)

        assert result.is_error is True
        assert "unknown tool 'Write'" in result.content


class TestTypedEvalScorers:
    async def test_scores_valid_persisted_upsert(self) -> None:
        output = MemoryDecisionOutput(
            tool_uses=(_upsert(),),
            text="",
            memory_dir=Path("/tmp/unused"),
            persisted_names=("existing", "release-freeze"),
        )
        sample = _sample()

        scores = [
            await scorer.score(sample, output)
            for scorer in (JudgmentScorer(), PayloadValidScorer(), PersistenceIntegrityScorer())
        ]

        assert [score.value for score in scores] == [1.0, 1.0, 1.0]

    async def test_skip_case_passes_judgment_without_payload(self) -> None:
        output = MemoryDecisionOutput(
            tool_uses=(),
            text="4",
            memory_dir=Path("/tmp/unused"),
        )
        sample = _sample(expect_write=False)

        judgment = await JudgmentScorer().score(sample, output)
        payload = await PayloadValidScorer().score(sample, output)

        assert judgment.value == 1.0
        assert payload.value == "NA"


def test_dataset_no_longer_seeds_generated_index() -> None:
    samples = load_memory_decision_dataset(DATASET)

    assert len(samples) == 6
    assert all("MEMORY.md" not in sample.pre_populated_files for sample in samples)
