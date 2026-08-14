"""memory_compact eval (B2, D45) — 面 #1 fail-open 最高风险面。

被测对象:生产 full_compact(9-slot L4 摘要)。oracle = 种植事实回收
(D45.1):待压缩历史里埋 N 个关键事实,压缩后判它们在不在 summary。
不测生成质量(软),测信息保真(硬 keyword)——把开放问题重述成封闭
存在性检查。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openharness.eval.memory_compact import (
    MemoryCompactOutput,
    MemoryCompactSample,
    infer_memory_compact,
    load_memory_compact_dataset,
    run_memory_compact_eval,
)
from openharness.eval.memory_compact_scorers import (
    FactRecallScorer,
    NoiseExclusionScorer,
)
from openharness.protocols.content import TextBlock, ToolResultBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent

DATASET = Path(__file__).parents[2] / "evals" / "memory_compact" / "dataset.yaml"


def make_sample(
    *,
    must_recall: tuple[str, ...] = ("Tavily", "8080"),
    must_not_recall: tuple[str, ...] = (),
) -> MemoryCompactSample:
    # 一段够长的历史(> preserve_recent=12),埋入事实
    msgs = []
    for i in range(20):
        msgs.append(ConversationMessage(role="user", content=[TextBlock(text=f"filler turn {i}")]))
        msgs.append(ConversationMessage(role="assistant", content=[TextBlock(text=f"ok {i}")]))
    # 把关键事实塞进中段(会被压缩掉的 older 区)
    msgs[4] = ConversationMessage(
        role="user", content=[TextBlock(text="我们用 Tavily 做搜索,不用 SerpAPI")]
    )
    msgs[6] = ConversationMessage(role="user", content=[TextBlock(text="端口从 3000 改成 8080")])
    return MemoryCompactSample(
        case_id="X-1",
        capability="fact-recall",
        messages=msgs,
        must_recall=must_recall,
        must_not_recall=must_not_recall,
        notes="",
    )


def out(summary: str) -> MemoryCompactOutput:
    return MemoryCompactOutput(summary_text=summary, did_apply=True)


class TestFactRecallScorer:
    async def test_pass_when_all_facts_present(self) -> None:
        s = make_sample(must_recall=("Tavily", "8080"))
        score = await FactRecallScorer().score(
            s, out("...decided to use Tavily for search... port is now 8080...")
        )
        assert score.value == 1.0

    async def test_fail_when_a_fact_dropped(self) -> None:
        s = make_sample(must_recall=("Tavily", "8080"))
        # summary 丢了 8080 —— fail-open 的静默丢失
        score = await FactRecallScorer().score(s, out("...decided to use Tavily for search..."))
        assert score.value == 0.0
        assert "8080" in score.reason  # 诊断要点名丢了哪个

    async def test_case_insensitive(self) -> None:
        s = make_sample(must_recall=("Tavily",))
        score = await FactRecallScorer().score(s, out("... we use tavily ..."))
        assert score.value == 1.0

    async def test_did_not_apply_is_fail(self) -> None:
        # 压缩根本没跑成(did_apply=False)→ 事实必然没保留 → FAIL
        s = make_sample(must_recall=("Tavily",))
        score = await FactRecallScorer().score(
            s, MemoryCompactOutput(summary_text="", did_apply=False)
        )
        assert score.value == 0.0


class TestNoiseExclusionScorer:
    async def test_pass_when_noise_absent(self) -> None:
        s = make_sample(must_not_recall=("filler turn 3",))
        score = await NoiseExclusionScorer().score(s, out("clean summary, no filler"))
        assert score.value == 1.0

    async def test_fail_when_noise_leaked(self) -> None:
        s = make_sample(must_not_recall=("filler turn 3",))
        score = await NoiseExclusionScorer().score(s, out("... filler turn 3 ..."))
        assert score.value == 0.0

    async def test_vacuous_pass_on_empty(self) -> None:
        s = make_sample(must_not_recall=())
        score = await NoiseExclusionScorer().score(s, out("anything"))
        assert score.value == 1.0


class _FakeClient:
    """摘要里回显所有 must_recall 事实的假 client(replay 不到时的单测替身)。"""

    def __init__(self, summary: str) -> None:
        self._summary = summary

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        del request
        text = f"<analysis>x</analysis><summary>{self._summary}</summary>"
        yield ApiTextDeltaEvent(text=text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class TestLoadDataset:
    def test_loads_at_least_six_with_facts(self) -> None:
        samples = load_memory_compact_dataset(DATASET)
        assert len(samples) >= 6
        for s in samples:
            # 每个 case 至少埋 3 个必留事实
            assert len(s.must_recall) >= 3, s.case_id
            assert len(s.messages) > 12  # 够长才会触发 full_compact

    def test_has_noise_exclusion_case(self) -> None:
        samples = load_memory_compact_dataset(DATASET)
        assert any(s.must_not_recall for s in samples)

    def test_required_facts_are_in_the_compacted_older_region(self) -> None:
        for sample in load_memory_compact_dataset(DATASET):
            older_text = "\n".join(
                block.text if isinstance(block, TextBlock) else getattr(block, "content", "")
                for message in sample.messages[:-12]
                for block in message.content
            )
            for fact in sample.must_recall:
                assert fact in older_text, f"{sample.case_id}: {fact} leaked into preserved tail"

    def test_includes_context_lifecycle_dogfood_regressions(self) -> None:
        samples = {sample.case_id: sample for sample in load_memory_compact_dataset(DATASET)}
        dogfood_cases = {
            "MC7-current-state-supersedes-stale",
            "MC8-slash-skill-provenance",
            "MC9-latest-error-ordering",
        }
        repaired_cases = {
            "MC2-decision-facts",
            "MC4-user-request-facts",
            "MC5-pending-tasks-facts",
        }
        newly_ratified = dogfood_cases | repaired_cases

        assert newly_ratified <= samples.keys()
        for case_id in dogfood_cases:
            assert len(samples[case_id].must_recall) >= 3
            assert len(samples[case_id].messages) > 12
        assert all(sample.status == "ratified" for sample in samples.values())

    def test_refetchable_result_cleanup_case_is_well_formed(self) -> None:
        samples = {sample.case_id: sample for sample in load_memory_compact_dataset(DATASET)}
        sample = samples["MC10-refetchable-result-cleanup"]

        assert sample.status == "ratified"
        assert "RAW_READ_BODY_SHOULD_BE_CLEARED" in sample.must_not_recall
        assert sample.messages[-12:][0].content[0].text == "填充消息 16"  # type: ignore[union-attr]
        old_read_result = sample.messages[2].content[0]
        assert isinstance(old_read_result, ToolResultBlock)
        assert len(old_read_result.content) > 500


class TestRunEval:
    async def test_inference_scores_only_the_generated_summary_not_preserved_tail(self) -> None:
        sample = make_sample(must_recall=("Tavily", "8080"))
        output = await infer_memory_compact(
            sample=sample,
            api_client=_FakeClient("SUMMARY_ONLY_SENTINEL"),
            model="fake",
        )

        assert output.summary_text.strip() == "SUMMARY_ONLY_SENTINEL"
        assert "filler turn" not in output.summary_text

    async def test_end_to_end_calls_production_full_compact(self) -> None:
        client = _FakeClient("used Tavily, port 8080, tests via uv run pytest -q")
        results = await run_memory_compact_eval(
            DATASET,
            [FactRecallScorer(), NoiseExclusionScorer()],
            client,
            "fake",
        )
        samples = load_memory_compact_dataset(DATASET)
        assert len(results) == len(samples)
        for r in results:
            assert {sc.dim for sc in r.scores} == {"fact_recall", "noise_exclusion"}
