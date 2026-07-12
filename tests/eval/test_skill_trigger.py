"""skill_trigger eval — decision surface #5 / C1 (D41 P0 第二个).

Mirrors test_tool_choice.py's shape: dataset loading, per-dim scorers,
subject-under-test wiring, cassette wrapper, runner. The distinctive
assertions here:

- the eval system prompt must actually carry the fixture catalog
  (``## Available Skills`` + slugs) — without it the eval is meaningless;
- restraint semantics differ from tool_choice TC5: other tool calls are
  ALLOWED, only LoadSkill must be absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openharness.eval.skill_trigger import (
    SkillTriggerOutput,
    SkillTriggerSample,
    build_fixture_store,
    infer_skill_trigger,
    load_skill_trigger_dataset,
    run_skill_trigger_eval,
)
from openharness.eval.skill_trigger_scorers import SlugSelectionScorer, TriggerDecisionScorer
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from openharness.protocols.requests import ApiMessageRequest

DATASET = Path(__file__).parents[2] / "evals" / "skill_trigger" / "dataset.yaml"


def make_sample(
    *,
    case_id: str = "TS1-x",
    expected_slug: str | None = "parse-credit-report",
) -> SkillTriggerSample:
    return SkillTriggerSample(
        case_id=case_id,
        capability="trigger",
        shape="single-user-message",
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="解析这份征信报告")]),
        ],
        expected_slug=expected_slug,
        notes="",
    )


def load_skill_call(name: str = "parse-credit-report") -> ToolUseBlock:
    return ToolUseBlock(id="tu_1", name="LoadSkill", input={"name": name})


def other_call(tool: str = "Edit") -> ToolUseBlock:
    return ToolUseBlock(id="tu_2", name=tool, input={"path": "config.yaml"})


def make_output(*tool_uses: ToolUseBlock) -> SkillTriggerOutput:
    return SkillTriggerOutput(tool_uses=tuple(tool_uses), text="", stop_reason="tool_use")


class TestLoadDataset:
    def test_loads_at_least_eight_samples_and_catalog(self) -> None:
        samples, catalog = load_skill_trigger_dataset(DATASET)
        assert len(samples) >= 8
        slugs = {s.name for s in catalog}
        # discrimination pair must exist in the fixture catalog
        assert "parse-credit-report" in slugs
        assert "loan-contract-review" in slugs
        # every non-restraint expectation must point at a real catalog slug
        for sample in samples:
            if sample.expected_slug is not None:
                assert sample.expected_slug in slugs, sample.case_id

    def test_planted_error_case_present(self) -> None:
        samples, _ = load_skill_trigger_dataset(DATASET)
        planted = [s for s in samples if len(s.messages) > 1]
        assert planted, "TS4 self-correction case (planted history) missing"

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "catalog:\n  - name: a-skill\n    description: d\nsamples:\n  - case_id: X\n",
            encoding="utf-8",
        )
        with pytest.raises(KeyError):
            load_skill_trigger_dataset(bad)


class TestTriggerDecisionScorer:
    async def test_pass_when_expected_and_called(self) -> None:
        score = await TriggerDecisionScorer().score(make_sample(), make_output(load_skill_call()))
        assert score.value == 1.0

    async def test_fail_when_expected_but_not_called(self) -> None:
        score = await TriggerDecisionScorer().score(make_sample(), make_output(other_call()))
        assert score.value == 0.0

    async def test_restraint_pass_other_tools_allowed(self) -> None:
        # 与 tool_choice TC5 的关键差异: restraint 只断言无 LoadSkill,
        # 其他工具调用是正当的.
        sample = make_sample(expected_slug=None)
        score = await TriggerDecisionScorer().score(sample, make_output(other_call()))
        assert score.value == 1.0

    async def test_restraint_fail_when_load_skill_called(self) -> None:
        sample = make_sample(expected_slug=None)
        score = await TriggerDecisionScorer().score(sample, make_output(load_skill_call()))
        assert score.value == 0.0


class TestSlugSelectionScorer:
    async def test_pass_on_exact_slug(self) -> None:
        score = await SlugSelectionScorer().score(make_sample(), make_output(load_skill_call()))
        assert score.value == 1.0

    async def test_fail_on_wrong_slug(self) -> None:
        score = await SlugSelectionScorer().score(
            make_sample(), make_output(load_skill_call("loan-contract-review"))
        )
        assert score.value == 0.0

    async def test_fail_on_case_or_separator_mangle(self) -> None:
        # slug 是精确匹配 (LoadSkill 目录 case-sensitive): 下划线化 = 失败
        score = await SlugSelectionScorer().score(
            make_sample(), make_output(load_skill_call("parse_credit_report"))
        )
        assert score.value == 0.0

    async def test_vacuous_pass_on_restraint_case(self) -> None:
        sample = make_sample(expected_slug=None)
        score = await SlugSelectionScorer().score(sample, make_output(other_call()))
        assert score.value == 1.0

    async def test_scores_first_load_skill_call_only(self) -> None:
        out = make_output(load_skill_call("parse-credit-report"), load_skill_call("wrong-one"))
        score = await SlugSelectionScorer().score(make_sample(), out)
        assert score.value == 1.0


class FakeApiClient:
    def __init__(self, *blocks: ToolUseBlock | TextBlock) -> None:
        self._blocks = list(blocks)
        self.last_request: ApiMessageRequest | None = None

    async def stream_message(self, request: ApiMessageRequest):  # type: ignore[no-untyped-def]
        self.last_request = request
        yield ApiTextDeltaEvent(text="")
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=self._blocks),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="tool_use",
        )


class TestInferSkillTrigger:
    async def test_system_prompt_carries_fixture_catalog(self) -> None:
        """接线断言: 目录必须真的进 system prompt, 否则整个 eval 测了个寂寞."""
        _, catalog = load_skill_trigger_dataset(DATASET)
        client = FakeApiClient(load_skill_call())
        await infer_skill_trigger(
            sample=make_sample(),
            catalog=catalog,
            api_client=client,
            model="fake",
        )
        request = client.last_request
        assert request is not None
        assert request.system is not None
        assert "Available Skills" in request.system
        for skill in catalog:
            assert skill.name in request.system

    async def test_load_skill_registered_in_tools(self) -> None:
        _, catalog = load_skill_trigger_dataset(DATASET)
        client = FakeApiClient(load_skill_call())
        await infer_skill_trigger(
            sample=make_sample(), catalog=catalog, api_client=client, model="fake"
        )
        assert client.last_request is not None
        tool_names = {t.name for t in client.last_request.tools or []}
        assert "LoadSkill" in tool_names

    async def test_output_collects_tool_uses(self) -> None:
        _, catalog = load_skill_trigger_dataset(DATASET)
        client = FakeApiClient(load_skill_call(), TextBlock(text="ok"))
        output = await infer_skill_trigger(
            sample=make_sample(), catalog=catalog, api_client=client, model="fake"
        )
        assert [tu.name for tu in output.tool_uses] == ["LoadSkill"]
        assert output.text == "ok"


class TestFixtureStore:
    def test_store_satisfies_protocol_and_get(self) -> None:
        _, catalog = load_skill_trigger_dataset(DATASET)
        store = build_fixture_store(catalog)
        discovered = store.discover()
        assert set(discovered) == {s.name for s in catalog}
        assert store.get("parse-credit-report") is not None
        assert store.get("nope") is None


class TestRunEval:
    async def test_end_to_end_with_fake_client(self) -> None:
        client = FakeApiClient(load_skill_call())
        results = await run_skill_trigger_eval(
            DATASET,
            [TriggerDecisionScorer(), SlugSelectionScorer()],
            client,
            "fake",
        )
        samples, _ = load_skill_trigger_dataset(DATASET)
        assert len(results) == len(samples)
        for result in results:
            assert {s.dim for s in result.scores} == {"trigger_decision", "slug_selection"}
