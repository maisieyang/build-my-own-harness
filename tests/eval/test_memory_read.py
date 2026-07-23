"""memory_read eval (C4, D41 面 #4 的另一半) — memory READ 自选决策。

被测对象:生产 prompt 组装(build_system_prompt 的 `## Memory` = 写规则 +
MEMORY.md 索引)+ Read 工具。Phase 16 后 harness 不再代排相关性(relevance.py
退役),模型自己扫索引、自选 Read 哪个 memory 文件。

镜像 skill_trigger(C1):同是"看目录/索引 → 自决调不调工具、调哪个",单步、
tool-call 存在性硬 oracle。契约来自 prompts/memory.py:135-138:
- "MUST access memory when the user explicitly asks to check/recall/remember"
  → must-read case(强制触发,硬)
- "If the user says to ignore memory: do not apply" → restraint case
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openharness.eval.memory_read import (
    MemoryReadOutput,
    MemoryReadSample,
    load_memory_read_dataset,
    run_memory_read_eval,
)
from openharness.eval.memory_read_scorers import MemorySelectionScorer, ReadDecisionScorer
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent

DATASET = Path(__file__).parents[2] / "evals" / "memory_read" / "dataset.yaml"

_SLUGS = (
    "user_deploy_prefs",
    "project_aurora_status",
    "reference_staging_db",
    "feedback_review_tone",
)


def make_sample(*, expected_slug: str | None, capability: str = "MR-test") -> MemoryReadSample:
    return MemoryReadSample(
        case_id="X-1",
        capability=capability,
        shape="single-turn",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="...")])],
        expected_read_slug=expected_slug,
        all_memory_slugs=_SLUGS,
        notes="",
    )


def read_call(path: str) -> ToolUseBlock:
    return ToolUseBlock(id="t1", name="Read", input={"path": path})


def out(*tool_uses: ToolUseBlock, text: str = "") -> MemoryReadOutput:
    return MemoryReadOutput(tool_uses=tuple(tool_uses), text=text, stop_reason="tool_use")


class TestReadDecisionScorer:
    async def test_must_read_and_did_read_memory(self) -> None:
        s = make_sample(expected_slug="user_deploy_prefs")
        score = await ReadDecisionScorer().score(
            s, out(read_call("/eval-fixture/memory/user_deploy_prefs.md"))
        )
        assert score.value == 1.0

    async def test_must_read_but_read_nothing_is_fail(self) -> None:
        # 用户明确 recall,但模型没读任何 memory → 违反 MUST 契约(fail-open:静默漏)
        s = make_sample(expected_slug="user_deploy_prefs")
        score = await ReadDecisionScorer().score(s, out(text="sure, here you go"))
        assert score.value == 0.0

    async def test_restraint_and_abstained_is_pass(self) -> None:
        s = make_sample(expected_slug=None)
        score = await ReadDecisionScorer().score(s, out(text="fresh answer, no memory"))
        assert score.value == 1.0

    async def test_restraint_but_read_memory_is_fail(self) -> None:
        # 用户说 ignore memory,模型却读了 → 违反 restraint
        s = make_sample(expected_slug=None)
        score = await ReadDecisionScorer().score(
            s, out(read_call("/eval-fixture/memory/user_deploy_prefs.md"))
        )
        assert score.value == 0.0

    async def test_restraint_allows_non_memory_task_read(self) -> None:
        # restraint 只管 memory 读;读任务文件(非 memory)是允许的
        s = make_sample(expected_slug=None)
        score = await ReadDecisionScorer().score(s, out(read_call("/workspace/app.py")))
        assert score.value == 1.0


class TestMemorySelectionScorer:
    async def test_read_correct_memory(self) -> None:
        s = make_sample(expected_slug="reference_staging_db")
        score = await MemorySelectionScorer().score(
            s, out(read_call("/eval-fixture/memory/reference_staging_db.md"))
        )
        assert score.value == 1.0

    async def test_read_wrong_memory_is_fail(self) -> None:
        s = make_sample(expected_slug="reference_staging_db")
        score = await MemorySelectionScorer().score(
            s, out(read_call("/eval-fixture/memory/project_aurora_status.md"))
        )
        assert score.value == 0.0
        assert "project_aurora_status" in score.reason  # 点名读错了哪个

    async def test_read_nothing_when_expected_is_fail(self) -> None:
        s = make_sample(expected_slug="reference_staging_db")
        score = await MemorySelectionScorer().score(s, out(text="no read"))
        assert score.value == 0.0

    async def test_restraint_is_vacuous_pass(self) -> None:
        s = make_sample(expected_slug=None)
        score = await MemorySelectionScorer().score(s, out(text="nothing to select"))
        assert score.value == 1.0


class _FakeClient:
    """恒读固定路径的假 client(单测替身)。"""

    def __init__(self, read_path: str | None) -> None:
        self._read_path = read_path

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        from openharness.protocols.stream_events import ApiMessageCompleteEvent
        from openharness.protocols.usage import UsageSnapshot

        del request
        content: list[TextBlock | ToolUseBlock] = []
        if self._read_path is not None:
            content.append(ToolUseBlock(id="t1", name="Read", input={"path": self._read_path}))
        else:
            content.append(TextBlock(text="no memory read"))
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=content),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="tool_use" if self._read_path else "end_turn",
        )


class TestLoadDataset:
    def test_loads_at_least_six_with_both_kinds(self) -> None:
        samples, fixture = load_memory_read_dataset(DATASET)
        assert len(samples) >= 6
        assert any(s.expected_read_slug is not None for s in samples)  # 有 must-read
        assert any(s.expected_read_slug is None for s in samples)  # 有 restraint
        assert fixture.index_content  # MEMORY.md 索引非空
        assert len(fixture.memory_slugs) >= 3  # 索引里多个 memory 供选择


class TestRunEval:
    async def test_end_to_end_uses_production_prompt(self) -> None:
        # 假 client 恒读 user_deploy_prefs
        client = _FakeClient("/eval-fixture/memory/user_deploy_prefs.md")
        results = await run_memory_read_eval(
            DATASET,
            [ReadDecisionScorer(), MemorySelectionScorer()],
            client,
            "fake",
        )
        samples, _ = load_memory_read_dataset(DATASET)
        assert len(results) == len(samples)
        for r in results:
            assert {sc.dim for sc in r.scores} == {"read_decision", "memory_selection"}
