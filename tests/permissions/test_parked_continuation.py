from __future__ import annotations

import pytest

from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    ParkedContinuation,
    ParkedControllerState,
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionReviewDecision,
    PermissionRuntime,
    workspace_runtime_profile,
)
from openharness.protocols import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock


def _runtime() -> PermissionRuntime:
    profile = workspace_runtime_profile()
    boundary = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="test",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    return PermissionRuntime(profile=profile, boundary=boundary)


def _request(runtime: PermissionRuntime) -> PermissionDeltaRequest:
    return PermissionDeltaRequest.create(
        tool_use_id="tool-web-1",
        tool_name="WebFetch",
        final_arguments={"url": "https://example.com"},
        profile=runtime.profile,
        boundary=runtime.boundary,
        delta=PermissionDelta.external_tool("web"),
        crossing=BoundaryViolation(
            dimension="external.web",
            requested="WebFetch",
            evidence="outside local sandbox",
        ),
        data_sources=("final tool arguments",),
        data_destinations=("web",),
    )


def _continuation(runtime: PermissionRuntime) -> ParkedContinuation:
    request = _request(runtime)
    tool_uses = (
        ToolUseBlock(
            id="tool-read-1",
            name="Read",
            input={"path": "README.md"},
        ),
        ToolUseBlock(
            id=request.tool_use_id,
            name=request.tool_name,
            input=request.final_arguments,
        ),
        ToolUseBlock(
            id="tool-read-2",
            name="Read",
            input={"path": "README.zh-CN.md"},
        ),
    )
    return ParkedContinuation.create(
        request=request,
        messages=(ConversationMessage(role="user", content=[TextBlock(text="inspect docs")]),),
        assistant_message=ConversationMessage(role="assistant", content=list(tool_uses)),
        tool_uses=tool_uses,
        completed_tool_results=(ToolResultBlock(tool_use_id="tool-read-1", content="ok"),),
        next_tool_index=1,
        controller=ParkedControllerState(mode="goal", goal_condition="finish the task"),
    )


def test_continuation_binds_exact_dispatch_position_and_controller() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)

    continuation.validate_for(runtime)

    assert continuation.current_tool_use.id == "tool-web-1"
    assert [tool.id for tool in continuation.remaining_tool_uses] == [
        "tool-web-1",
        "tool-read-2",
    ]
    assert continuation.controller.mode == "goal"
    assert continuation.controller.goal_condition == "finish the task"


def test_continuation_fails_closed_when_exact_arguments_drift() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    payload = continuation.model_dump(mode="json")
    payload["tool_uses"][1]["input"]["url"] = "https://attacker.invalid"

    drifted = ParkedContinuation.model_validate(payload)

    with pytest.raises(ValueError, match="continuation integrity failure"):
        drifted.validate_for(runtime)


def test_continuation_rejects_out_of_range_dispatch_position() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    payload = continuation.model_dump(mode="json")
    payload["next_tool_index"] = 99
    drifted = ParkedContinuation.model_validate(payload)

    with pytest.raises(ValueError, match="dispatch position is out of range"):
        drifted.validate_for(runtime)


def test_continuation_rejects_non_assistant_dispatch_message() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    payload = continuation.model_dump(mode="json")
    payload["assistant_message"]["role"] = "user"
    drifted = ParkedContinuation.model_validate(payload)

    with pytest.raises(ValueError, match="assistant message has invalid role"):
        drifted.validate_for(runtime)


def test_runtime_persists_unconsumed_continuation_and_exact_decision() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    request = continuation.request
    runtime.park(request, reason="owner decision needed")
    runtime.bind_continuation(continuation)
    runtime.approve_parked(request.request_id)

    restored = PermissionRuntime.from_state(
        profile=runtime.profile,
        boundary=runtime.boundary,
        state=runtime.export_state().model_dump(mode="json"),
    )
    resumed = restored.resume_decided()

    assert resumed.decision is PermissionReviewDecision.APPROVE
    assert resumed.continuation == continuation
    with pytest.raises(ValueError, match="no permission decision is ready to resume"):
        restored.resume_decided()


def test_clear_drops_continuation_but_not_consumed_permission_ledger() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    runtime.park(continuation.request, reason="owner decision needed")
    runtime.bind_continuation(continuation)

    runtime.clear_pending_state()

    assert runtime.parked_request is None
    assert runtime.parked_continuation is None
    with pytest.raises(ValueError, match="no permission decision is ready to resume"):
        runtime.resume_decided()


def test_clear_revokes_an_approved_but_unconsumed_exact_grant() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    request = continuation.request
    runtime.park(request, reason="owner decision needed")
    runtime.bind_continuation(continuation)
    runtime.approve_parked(request.request_id)

    runtime.clear_pending_state()

    assert runtime.consume_grant(request) is False
    assert runtime.parked_continuation is None


def test_runtime_rejects_binding_a_continuation_for_another_request() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    other = PermissionDeltaRequest.create(
        tool_use_id="tool-other",
        tool_name="WebFetch",
        final_arguments={"url": "https://other.example"},
        profile=runtime.profile,
        boundary=runtime.boundary,
        delta=PermissionDelta.external_tool("web"),
        crossing=BoundaryViolation(
            dimension="external.web",
            requested="WebFetch",
            evidence="outside local sandbox",
        ),
        data_sources=("final tool arguments",),
        data_destinations=("web",),
    )
    runtime.park(other, reason="other")

    with pytest.raises(ValueError, match="does not match parked"):
        runtime.bind_continuation(continuation)


def test_runtime_rejects_consuming_an_inactive_continuation() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)

    with pytest.raises(ValueError, match="not the active"):
        runtime.consume_continuation(continuation)


def test_restored_runtime_rejects_continuation_profile_drift() -> None:
    runtime = _runtime()
    continuation = _continuation(runtime)
    runtime.park(continuation.request, reason="owner")
    runtime.bind_continuation(continuation)
    state = runtime.export_state().model_dump(mode="json")
    state["parked_continuation"]["request"]["enforcement"]["profile_fingerprint"] = "drift"

    with pytest.raises(ValueError):
        PermissionRuntime.from_state(
            profile=runtime.profile,
            boundary=runtime.boundary,
            state=state,
        )
