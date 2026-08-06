from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionReviewDecision,
    workspace_runtime_profile,
)
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ConversationMessage,
    TextBlock,
    UsageSnapshot,
)
from openharness.services.permission_reviewer import LlmPermissionReviewer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


class _Client:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_request: ApiMessageRequest | None = None

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        yield ApiTextDeltaEvent(text=self.response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self.response)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _request() -> PermissionDeltaRequest:
    profile = workspace_runtime_profile()
    boundary = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="test",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    return PermissionDeltaRequest.create(
        tool_use_id="tool-1",
        tool_name="WebFetch",
        final_arguments={"url": "https://example.com/private"},
        profile=profile,
        boundary=boundary,
        delta=PermissionDelta.external_tool("web"),
        crossing=BoundaryViolation(
            dimension="external.web",
            requested="WebFetch",
            evidence="outside local sandbox",
        ),
        data_sources=("final tool arguments",),
        data_destinations=("web",),
    )


async def test_reviewer_receives_exact_structured_envelope() -> None:
    client = _Client('{"decision":"approve","reason":"safe once"}')
    reviewer = LlmPermissionReviewer(api_client=client, model="qwen-plus")

    verdict = await reviewer.review(_request())

    assert verdict.decision is PermissionReviewDecision.APPROVE
    assert client.last_request is not None
    sent = client.last_request.messages[0].content[0].text
    assert "https://example.com/private" in sent
    assert _request().arguments_fingerprint in sent
    assert _request().boundary_fingerprint in sent
    assert client.last_request.tools == []


async def test_invalid_or_failed_review_defers_fail_closed() -> None:
    reviewer = LlmPermissionReviewer(api_client=_Client("not json"), model="qwen-plus")

    verdict = await reviewer.review(_request())

    assert verdict.decision is PermissionReviewDecision.FAILED


async def test_reviewer_can_explicitly_defer() -> None:
    client = _Client('{"decision":"defer","reason":"needs owner context"}')
    verdict = await LlmPermissionReviewer(api_client=client, model="qwen-plus").review(_request())
    assert verdict.decision is PermissionReviewDecision.DEFER


async def test_non_object_invalid_decision_and_invalid_reason_fail_closed() -> None:
    for response in (
        "[]",
        '{"decision":"maybe","reason":"unclear"}',
        '{"decision":"deny","reason":""}',
    ):
        verdict = await LlmPermissionReviewer(
            api_client=_Client(response), model="qwen-plus"
        ).review(_request())
        assert verdict.decision is PermissionReviewDecision.FAILED
