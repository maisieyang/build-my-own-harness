from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    ExternalToolPolicy,
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
        filesystem_rules=("write:/workspace", "deny_write:/workspace/.git"),
        network_rules=("deny-all",),
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
        authorization_context=("Inspect the public example.com page, but do not publish.",),
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
    assert '"kind":"local_boundary"' in sent
    assert "Inspect the public example.com page, but do not publish." in sent
    assert '"filesystem_rules":["deny_write:/workspace/.git","write:/workspace"]' in sent
    assert '"network_rules":["deny-all"]' in sent
    assert '"name":"workspace"' in sent
    assert client.last_request.tools == []


async def test_reviewer_receives_external_policy_evidence_without_fake_boundary() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    request = PermissionDeltaRequest.create_external(
        tool_use_id="tool-external",
        tool_name="Github.create_issue",
        final_arguments={"title": "exact issue"},
        profile=profile,
        policy=profile.external_tools,
        surface="mcp",
        effect_kind="mutating",
        trust_source="trusted-server",
        tool_identity="Github.create_issue",
        server_identity="Github",
        delta=PermissionDelta.external_tool("mcp"),
        crossing=BoundaryViolation(
            dimension="external.mcp",
            requested="Github.create_issue",
            evidence="mutating external effect",
        ),
    )
    client = _Client('{"decision":"defer","reason":"needs owner"}')

    await LlmPermissionReviewer(api_client=client, model="qwen-plus").review(request)

    assert client.last_request is not None
    sent = client.last_request.messages[0].content[0].text
    assert '"kind":"external_policy"' in sent
    assert '"surface":"mcp"' in sent
    assert '"effect_kind":"mutating"' in sent
    assert '"server_identity":"Github"' in sent
    assert "boundary_fingerprint" not in sent


async def test_reviewer_prompt_separates_preparation_from_consequential_action() -> None:
    client = _Client('{"decision":"defer","reason":"needs explicit authorization"}')

    await LlmPermissionReviewer(api_client=client, model="qwen-plus").review(_request())

    assert client.last_request is not None
    assert client.last_request.system is not None
    normalized_prompt = " ".join(client.last_request.system.split())
    assert (
        "Do not infer authorization for a consequential action from preparatory language"
        in normalized_prompt
    )
    assert "publish" in normalized_prompt
    assert "DEFER" in normalized_prompt


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
