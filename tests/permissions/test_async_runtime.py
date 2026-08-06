from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
    ProcessCompleted,
)
from openharness.permissions import (
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionResolutionStatus,
    PermissionReviewDecision,
    PermissionReviewVerdict,
    PermissionRuntime,
    workspace_runtime_profile,
)


def _boundary(*, profile_fingerprint: str, backend_version: str = "1") -> EnforcedBoundary:
    return EnforcedBoundary(
        profile_fingerprint=profile_fingerprint,
        backend="test",
        backend_version=backend_version,
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )


def _request(*, command: str = "curl https://example.com") -> PermissionDeltaRequest:
    profile = workspace_runtime_profile()
    return PermissionDeltaRequest.create(
        tool_use_id="tool-1",
        tool_name="Bash",
        final_arguments={"command": command},
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        delta=PermissionDelta.network_domain("example.com"),
    )


@dataclass
class _Reviewer:
    verdict: PermissionReviewVerdict
    calls: list[PermissionDeltaRequest] = field(default_factory=list)

    async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict:
        self.calls.append(request)
        return self.verdict


def test_request_fingerprints_exact_final_arguments_and_boundary() -> None:
    request = _request()
    changed = _request(command="curl https://other.example")

    assert request.arguments_fingerprint != changed.arguments_fingerprint
    assert request.profile_fingerprint == workspace_runtime_profile().fingerprint
    assert request.boundary_fingerprint
    assert request.request_id != changed.request_id


def test_request_refuses_unverified_or_mismatched_boundary() -> None:
    profile = workspace_runtime_profile()
    unverified = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="test",
        backend_version="1",
        covered_effects=(),
        verification=BoundaryVerification.UNVERIFIED,
    )
    with pytest.raises(ValueError, match="verified boundary"):
        PermissionDeltaRequest.create(
            tool_use_id="tool-1",
            tool_name="Bash",
            final_arguments={"command": "true"},
            profile=profile,
            boundary=unverified,
            delta=PermissionDelta.network_domain("example.com"),
        )
    mismatch = _boundary(profile_fingerprint="wrong")
    with pytest.raises(ValueError, match="profile fingerprint"):
        PermissionDeltaRequest.create(
            tool_use_id="tool-1",
            tool_name="Bash",
            final_arguments={"command": "true"},
            profile=profile,
            boundary=mismatch,
            delta=PermissionDelta.network_domain("example.com"),
        )


@pytest.mark.asyncio
async def test_contained_result_makes_zero_reviewer_calls() -> None:
    profile = workspace_runtime_profile()
    reviewer = _Reviewer(PermissionReviewVerdict.approve("exact request is safe"))
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        reviewer=reviewer,
    )

    resolution = await runtime.resolve_boundary_result(
        ProcessCompleted(output="ok", exit_code=0),
        request_factory=_request,
    )

    assert resolution.status is PermissionResolutionStatus.CONTAINED
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_reviewer_approval_is_an_exact_one_shot_grant() -> None:
    profile = workspace_runtime_profile()
    reviewer = _Reviewer(PermissionReviewVerdict.approve("allow once"))
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        reviewer=reviewer,
    )
    request = _request()

    resolution = await runtime.resolve_boundary_result(
        BoundaryViolation(dimension="network.domain", requested="example.com", evidence="deny"),
        request_factory=lambda: request,
    )

    assert resolution.status is PermissionResolutionStatus.RETRY_ONCE
    assert runtime.consume_grant(request) is True
    assert runtime.consume_grant(request) is False
    assert runtime.consume_grant(_request(command="curl https://other.example")) is False
    assert reviewer.calls == [request]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict",
    [PermissionReviewVerdict.defer("needs a person"), PermissionReviewVerdict.failed("offline")],
)
async def test_reviewer_defer_or_failure_parks_without_grant(
    verdict: PermissionReviewVerdict,
) -> None:
    profile = workspace_runtime_profile()
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        reviewer=_Reviewer(verdict),
    )
    request = _request()

    resolution = await runtime.resolve_external(request)

    assert resolution.status is PermissionResolutionStatus.PARKED
    assert runtime.parked_request == request
    assert runtime.consume_grant(request) is False


@pytest.mark.asyncio
async def test_repeated_denial_opens_circuit_without_reviewer_recall() -> None:
    profile = workspace_runtime_profile()
    reviewer = _Reviewer(PermissionReviewVerdict.deny("not allowed"))
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        reviewer=reviewer,
        denial_limit=1,
    )
    request = _request()

    first = await runtime.resolve_external(request)
    second = await runtime.resolve_external(request)

    assert first.status is PermissionResolutionStatus.DENIED
    assert second.status is PermissionResolutionStatus.DENIED
    assert second.reason == "denial circuit open"
    assert reviewer.calls == [request]


def test_park_state_round_trips_and_resume_refuses_boundary_drift() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    request = _request()
    runtime.park(request, reason="needs a person")

    restored = PermissionRuntime.from_state(
        profile=profile,
        boundary=boundary,
        state=runtime.export_state(),
    )
    assert restored.parked_request == request

    drifted = _boundary(profile_fingerprint=profile.fingerprint, backend_version="2")
    with pytest.raises(ValueError, match="boundary drift"):
        PermissionRuntime.from_state(
            profile=profile,
            boundary=drifted,
            state=runtime.export_state(),
        )


def test_human_approve_and_deny_are_typed_and_exact() -> None:
    profile = workspace_runtime_profile()
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
    )
    request = _request()
    runtime.park(request, reason="needs a person")

    runtime.approve_parked(request.request_id)
    assert runtime.parked_request is None
    assert runtime.consume_grant(request) is True

    runtime.park(request, reason="needs a person")
    runtime.deny_parked(request.request_id, reason="user denied")
    assert runtime.parked_request is None
    assert runtime.consume_grant(request) is False
    assert runtime.last_human_decision is PermissionReviewDecision.DENY


@pytest.mark.asyncio
async def test_hard_deny_never_calls_reviewer() -> None:
    profile = workspace_runtime_profile()
    reviewer = _Reviewer(PermissionReviewVerdict.approve("must not be used"))
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        reviewer=reviewer,
    )
    base = _request()
    request = base.model_copy(update={"delta": base.delta.model_copy(update={"hard_deny": True})})
    request = PermissionDeltaRequest.create(
        tool_use_id=request.tool_use_id,
        tool_name=request.tool_name,
        final_arguments=request.final_arguments,
        profile=profile,
        boundary=runtime.boundary,
        delta=request.delta,
    )

    resolution = await runtime.resolve_external(request)

    assert resolution.status is PermissionResolutionStatus.DENIED
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_reviewer_exception_parks() -> None:
    class _BrokenReviewer:
        async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict:
            del request
            raise RuntimeError("offline")

    profile = workspace_runtime_profile()
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        reviewer=_BrokenReviewer(),
    )

    resolution = await runtime.resolve_external(_request())

    assert resolution.status is PermissionResolutionStatus.PARKED
    assert "reviewer failed" in resolution.reason


def test_runtime_constructor_and_park_ids_fail_closed() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    with pytest.raises(ValueError, match="positive"):
        PermissionRuntime(profile=profile, boundary=boundary, denial_limit=0)
    unverified = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="test",
        backend_version="1",
        covered_effects=(),
        verification=BoundaryVerification.UNVERIFIED,
    )
    with pytest.raises(ValueError, match="verified"):
        PermissionRuntime(profile=profile, boundary=unverified)
    with pytest.raises(ValueError, match="profile fingerprint"):
        PermissionRuntime(
            profile=profile,
            boundary=_boundary(profile_fingerprint="wrong"),
        )

    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    runtime.park(_request(), reason="person")
    with pytest.raises(ValueError, match="matching"):
        runtime.approve_parked("short")


@pytest.mark.asyncio
async def test_missing_reviewer_parks_and_request_drift_is_rejected() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    request = _request()

    resolution = await runtime.resolve_external(request)

    assert resolution.status is PermissionResolutionStatus.PARKED
    assert resolution.reason == "no automatic reviewer is available"

    profile_drift = request.model_copy(update={"profile_fingerprint": "wrong"})
    with pytest.raises(ValueError, match="profile drift"):
        runtime.consume_grant(profile_drift)
    boundary_drift = request.model_copy(update={"boundary_fingerprint": "wrong"})
    with pytest.raises(ValueError, match="boundary drift"):
        runtime.consume_grant(boundary_drift)


def test_state_dict_round_trip_and_profile_drift_are_rejected() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    state = runtime.export_state().model_dump(mode="json")
    assert (
        PermissionRuntime.from_state(profile=profile, boundary=boundary, state=state)
        .export_state()
        .profile_fingerprint
        == profile.fingerprint
    )

    changed = profile.model_copy(update={"name": "changed"})
    changed_boundary = _boundary(profile_fingerprint=changed.fingerprint)
    with pytest.raises(ValueError, match="profile drift"):
        PermissionRuntime.from_state(
            profile=changed,
            boundary=changed_boundary,
            state=state,
        )
