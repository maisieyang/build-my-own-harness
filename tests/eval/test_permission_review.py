from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openharness.eval.cassette import CassetteStore
from openharness.eval.permission_review import (
    PermissionReviewOutput,
    cassetted_infer_permission_review,
    infer_permission_review,
    load_permission_review_dataset,
)
from openharness.eval.permission_review_scorers import (
    PermissionVerdictScorer,
    ReviewLifecycleScorer,
)
from openharness.permissions import PermissionResolutionStatus, PermissionReviewDecision
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ConversationMessage,
    TextBlock,
    UsageSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


DATASET = Path(__file__).parents[2] / "evals" / "permission_review" / "dataset.yaml"


class _Client:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        self.calls += 1
        assert request.tools == []
        yield ApiTextDeltaEvent(text=self.response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self.response)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def test_dataset_declares_six_required_capabilities() -> None:
    samples = load_permission_review_dataset(DATASET)

    assert len(samples) == 6
    assert {sample.capability for sample in samples} == {
        "approve-once",
        "deny",
        "defer",
        "hard-deny-exclusion",
        "prompt-injection",
        "data-exfiltration",
    }


async def test_infer_calls_production_reviewer_for_normal_request() -> None:
    sample = load_permission_review_dataset(DATASET)[0]
    client = _Client('{"decision":"approve","reason":"exact action is authorized"}')

    output = await infer_permission_review(sample=sample, api_client=client, model="qwen-max")

    assert output.decision is PermissionReviewDecision.APPROVE
    assert output.review_called is True
    assert output.resolution_status is PermissionResolutionStatus.RETRY_ONCE
    assert client.calls == 1


async def test_hard_deny_excludes_reviewer_call() -> None:
    sample = next(
        sample
        for sample in load_permission_review_dataset(DATASET)
        if sample.capability == "hard-deny-exclusion"
    )
    client = _Client('{"decision":"approve","reason":"must never be read"}')

    output = await infer_permission_review(sample=sample, api_client=client, model="qwen-max")

    assert output.decision is PermissionReviewDecision.DENY
    assert output.review_called is False
    assert output.resolution_status is PermissionResolutionStatus.DENIED
    assert client.calls == 0


async def test_scorers_require_exact_verdict_and_lifecycle() -> None:
    sample = load_permission_review_dataset(DATASET)[0]
    output = PermissionReviewOutput(
        decision=PermissionReviewDecision.APPROVE,
        reason="ok",
        review_called=True,
        resolution_status=PermissionResolutionStatus.RETRY_ONCE,
    )

    assert (await PermissionVerdictScorer().score(sample, output)).value == 1.0
    assert (await ReviewLifecycleScorer().score(sample, output)).value == 1.0

    wrong = PermissionReviewOutput(
        decision=PermissionReviewDecision.DEFER,
        reason="wrong",
        review_called=False,
        resolution_status=PermissionResolutionStatus.PARKED,
    )
    assert (await PermissionVerdictScorer().score(sample, wrong)).value == 0.0
    assert (await ReviewLifecycleScorer().score(sample, wrong)).value == 0.0


async def test_cassette_round_trip_never_calls_client_on_replay(tmp_path: Path) -> None:
    sample = load_permission_review_dataset(DATASET)[0]
    store = CassetteStore(tmp_path)
    client = _Client('{"decision":"approve","reason":"authorized"}')

    recorded = await cassetted_infer_permission_review(
        sample=sample,
        api_client=client,
        model="qwen-max",
        cassette_mode="record",
        cassette_store=store,
    )
    replayed = await cassetted_infer_permission_review(
        sample=sample,
        api_client=None,
        model="qwen-max",
        cassette_mode="replay",
        cassette_store=store,
    )

    assert replayed == recorded
    assert client.calls == 1
