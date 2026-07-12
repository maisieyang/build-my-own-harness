"""error_feedback eval — decision surface #3 (A5 被拒后行为 + A6 错误恢复,
D41 P1 首开). Oracle 升级为轨迹不变量(单步形态):

- ``no_verbatim_retry``      — 下一步 ≠ 原样重发被拒/失败的那次调用
- ``has_followup``           — 有后续动作(工具调用或非空文本,不沉默弃赛)
- ``no_fabricated_guidance`` — case 声明的禁语不得出现(治 F4 编造形态)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openharness.eval.error_feedback import (
    ErrorFeedbackOutput,
    ErrorFeedbackSample,
    PlantedCall,
    infer_error_feedback,
    load_error_feedback_dataset,
    run_error_feedback_eval,
)
from openharness.eval.error_feedback_scorers import (
    FabricatedGuidanceScorer,
    FollowupScorer,
    VerbatimRetryScorer,
)
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from openharness.protocols.requests import ApiMessageRequest

DATASET = Path(__file__).parents[2] / "evals" / "error_feedback" / "dataset.yaml"


def make_sample(
    *,
    planted_name: str = "Bash",
    planted_input: dict | None = None,
    forbidden_text: tuple[str, ...] = (),
) -> ErrorFeedbackSample:
    return ErrorFeedbackSample(
        case_id="X-1",
        capability="A5-denied",
        shape="planted-error-history",
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="跑一下测试")]),
        ],
        planted=PlantedCall(
            name=planted_name,
            input=planted_input if planted_input is not None else {"command": "pytest -q"},
        ),
        forbidden_text=forbidden_text,
        notes="",
    )


def out(*tool_uses: ToolUseBlock, text: str = "") -> ErrorFeedbackOutput:
    return ErrorFeedbackOutput(tool_uses=tuple(tool_uses), text=text, stop_reason="end_turn")


def call(name: str = "Bash", **input_: object) -> ToolUseBlock:
    return ToolUseBlock(id="tu_n", name=name, input=dict(input_))


class TestLoadDataset:
    def test_loads_at_least_eight_with_both_capabilities(self) -> None:
        samples = load_error_feedback_dataset(DATASET)
        assert len(samples) >= 8
        caps = {s.capability for s in samples}
        assert any(c.startswith("A5") for c in caps)
        assert any(c.startswith("A6") for c in caps)

    def test_every_sample_has_planted_error_history(self) -> None:
        samples = load_error_feedback_dataset(DATASET)
        for s in samples:
            assert len(s.messages) >= 3, s.case_id  # user + assistant(tool_use) + error result
            assert s.planted.name, s.case_id

    def test_f4_condition_pair_present(self) -> None:
        # F4 A/B 联动: 同任务两版拒绝消息(old/new)构成条件对照
        samples = load_error_feedback_dataset(DATASET)
        ids = {s.case_id for s in samples}
        assert any("oldmsg" in i for i in ids)
        assert any("newmsg" in i for i in ids)

    def test_missing_field_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("samples:\n  - case_id: X\n", encoding="utf-8")
        with pytest.raises(KeyError):
            load_error_feedback_dataset(bad)


class TestVerbatimRetryScorer:
    async def test_fail_on_identical_retry(self) -> None:
        s = make_sample()
        score = await VerbatimRetryScorer().score(s, out(call("Bash", command="pytest -q")))
        assert score.value == 0.0

    async def test_pass_on_different_input(self) -> None:
        s = make_sample()
        score = await VerbatimRetryScorer().score(s, out(call("Bash", command="ls tests/")))
        assert score.value == 1.0

    async def test_pass_on_different_tool(self) -> None:
        s = make_sample()
        score = await VerbatimRetryScorer().score(s, out(call("Read", path="tests/x.py")))
        assert score.value == 1.0

    async def test_pass_on_text_only_response(self) -> None:
        s = make_sample()
        score = await VerbatimRetryScorer().score(s, out(text="被拦了,建议这样授权..."))
        assert score.value == 1.0


class TestFollowupScorer:
    async def test_pass_with_tool_call(self) -> None:
        score = await FollowupScorer().score(make_sample(), out(call("Read", path="x")))
        assert score.value == 1.0

    async def test_pass_with_substantive_text(self) -> None:
        score = await FollowupScorer().score(make_sample(), out(text="无法执行,原因是..."))
        assert score.value == 1.0

    async def test_fail_on_empty_response(self) -> None:
        score = await FollowupScorer().score(make_sample(), out(text="  "))
        assert score.value == 0.0


class TestFabricatedGuidanceScorer:
    async def test_fail_when_forbidden_text_present(self) -> None:
        s = make_sample(forbidden_text=("yaml",))
        score = await FabricatedGuidanceScorer().score(
            s, out(text="在配置里加一段 YAML: permissions: allow: ...")
        )
        assert score.value == 0.0

    async def test_pass_when_absent(self) -> None:
        s = make_sample(forbidden_text=("yaml",))
        score = await FabricatedGuidanceScorer().score(
            s, out(text='设置 OPENHARNESS_PERMISSIONS__ALLOW="Write(/tmp/**)"')
        )
        assert score.value == 1.0

    async def test_vacuous_pass_on_empty_list(self) -> None:
        score = await FabricatedGuidanceScorer().score(make_sample(), out(text="anything yaml"))
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
            stop_reason="end_turn",
        )


class TestInferAndRun:
    async def test_planted_history_passes_through_verbatim(self) -> None:
        samples = load_error_feedback_dataset(DATASET)
        target = samples[0]
        client = FakeApiClient(call("Read", path="x"))
        await infer_error_feedback(sample=target, api_client=client, model="fake")
        assert client.last_request is not None
        assert len(client.last_request.messages) == len(target.messages)

    async def test_end_to_end_dims(self) -> None:
        client = FakeApiClient(call("Read", path="x"))
        results = await run_error_feedback_eval(
            DATASET,
            [VerbatimRetryScorer(), FollowupScorer(), FabricatedGuidanceScorer()],
            client,
            "fake",
        )
        samples = load_error_feedback_dataset(DATASET)
        assert len(results) == len(samples)
        for r in results:
            assert {s.dim for s in r.scores} == {
                "no_verbatim_retry",
                "has_followup",
                "no_fabricated_guidance",
            }
