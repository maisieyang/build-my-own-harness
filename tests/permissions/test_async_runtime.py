from __future__ import annotations

import hashlib
import json
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
    ExternalPolicyEvidence,
    ExternalToolPolicy,
    LocalBoundaryEvidence,
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionEvidenceKind,
    PermissionFilesystemAccess,
    PermissionResolutionStatus,
    PermissionReviewDecision,
    PermissionReviewVerdict,
    PermissionRuntime,
    RuntimePermissionProfile,
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
        crossing=BoundaryViolation(
            dimension="network.domain",
            requested="example.com:443",
            evidence="network allowlist rejected the destination",
        ),
        data_sources=("workspace",),
        data_destinations=("example.com:443",),
    )


def _external_request(
    *,
    profile: RuntimePermissionProfile | None = None,
    tool_use_id: str = "tool-external",
    tool_name: str = "Github.create_issue",
    value: str = "one exact issue",
    surface: str = "mcp",
    effect_kind: str = "mutating",
    trust_source: str = "trusted-server",
    server_identity: str | None = "Github",
) -> PermissionDeltaRequest:
    active = profile or workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask", web="ask")}
    )
    return PermissionDeltaRequest.create_external(
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        final_arguments={"value": value},
        profile=active,
        policy=active.external_tools,
        surface=surface,
        effect_kind=effect_kind,
        trust_source=trust_source,
        tool_identity=tool_name,
        server_identity=server_identity,
        delta=PermissionDelta.external_tool(surface),
        crossing=BoundaryViolation(
            dimension=f"external.{surface}",
            requested=tool_name,
            evidence=f"{effect_kind} external effect",
        ),
        data_sources=("final tool arguments",),
        data_destinations=(surface,),
    )


def _legacy_v1_request(request: PermissionDeltaRequest) -> dict[str, object]:
    evidence = request.require_local_evidence()
    payload: dict[str, object] = {
        "request_id": "pending",
        "request_fingerprint": "pending",
        "grant_fingerprint": "pending",
        "tool_use_id": request.tool_use_id,
        "tool_name": request.tool_name,
        "final_arguments": request.final_arguments,
        "arguments_fingerprint": request.arguments_fingerprint,
        "profile_fingerprint": evidence.profile_fingerprint,
        "boundary_fingerprint": evidence.boundary_fingerprint,
        "backend": evidence.backend,
        "backend_fingerprint": evidence.backend_fingerprint,
        "profile_facts": evidence.profile_facts,
        "boundary_facts": evidence.boundary_facts,
        "delta": request.delta.model_dump(mode="json"),
        "crossing": {
            "dimension": request.crossing.dimension,
            "requested": request.crossing.requested,
            "evidence": request.crossing.evidence,
            "hard_deny": request.crossing.hard_deny,
        },
        "authorization_context": request.authorization_context,
        "data_sources": request.data_sources,
        "data_destinations": request.data_destinations,
    }
    exact = {
        "tool_name": request.tool_name,
        "arguments_fingerprint": request.arguments_fingerprint,
        "profile_fingerprint": evidence.profile_fingerprint,
        "boundary_fingerprint": evidence.boundary_fingerprint,
        "backend_fingerprint": evidence.backend_fingerprint,
        "authorization_context": list(request.authorization_context),
        "profile_facts": evidence.profile_facts,
        "boundary_facts": evidence.boundary_facts,
        "delta": request.delta.model_dump(mode="json"),
        "crossing": {
            "dimension": request.crossing.dimension,
            "requested": request.crossing.requested,
            "evidence": request.crossing.evidence,
            "hard_deny": request.crossing.hard_deny,
        },
        "data_sources": sorted(set(request.data_sources)),
        "data_destinations": sorted(set(request.data_destinations)),
    }

    def fingerprint(value: object) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    request_fingerprint = fingerprint(exact)
    payload.update(
        {
            "request_id": fingerprint(
                {
                    "tool_use_id": request.tool_use_id,
                    "request_fingerprint": request_fingerprint,
                }
            ),
            "request_fingerprint": request_fingerprint,
            "grant_fingerprint": request_fingerprint,
        }
    )
    return payload


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
    assert request.backend_fingerprint
    assert request.request_fingerprint
    assert request.grant_fingerprint == request.request_fingerprint
    assert request.data_sources == ("workspace",)
    assert request.data_destinations == ("example.com:443",)
    assert request.request_id != changed.request_id


def test_local_request_serializes_closed_boundary_evidence() -> None:
    request = _request()

    assert isinstance(request.enforcement, LocalBoundaryEvidence)
    assert request.enforcement.kind is PermissionEvidenceKind.LOCAL_BOUNDARY
    assert request.enforcement.operation_fingerprint
    payload = request.model_dump(mode="json")
    assert payload["schema_version"] == 2
    assert payload["enforcement"]["kind"] == "local_boundary"
    assert "boundary_fingerprint" not in payload
    assert "backend_fingerprint" not in payload

    missing = dict(payload)
    missing.pop("enforcement")
    with pytest.raises(ValueError, match="enforcement"):
        PermissionDeltaRequest.model_validate(missing)


def test_external_request_has_policy_evidence_without_fake_local_boundary() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )

    request = PermissionDeltaRequest.create_external(
        tool_use_id="tool-external",
        tool_name="Github.create_issue",
        final_arguments={"title": "one exact issue"},
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
        data_sources=("final tool arguments",),
        data_destinations=("mcp",),
    )

    assert isinstance(request.enforcement, ExternalPolicyEvidence)
    assert request.enforcement.kind is PermissionEvidenceKind.EXTERNAL_POLICY
    assert request.enforcement.surface == "mcp"
    assert request.enforcement.tool_identity == "Github.create_issue"
    assert request.enforcement.server_identity == "Github"
    assert request.enforcement.policy_fingerprint
    payload = request.model_dump(mode="json")
    assert payload["enforcement"]["kind"] == "external_policy"
    assert "boundary_fingerprint" not in payload
    assert "backend" not in payload

    with pytest.raises(ValueError, match="no local boundary evidence"):
        _ = request.boundary_fingerprint


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("policy", "does not match active permission profile"),
        ("surface", "unknown external policy surface"),
        ("delta", "delta does not match"),
        ("tool", "tool identity does not match"),
        ("crossing", "crossing does not match"),
    ],
)
def test_external_factory_rejects_inconsistent_evidence(case: str, match: str) -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask", web="ask")}
    )
    policy = profile.external_tools
    surface = "mcp"
    tool_name = "Github.create_issue"
    tool_identity = tool_name
    delta = PermissionDelta.external_tool(surface)
    crossing = BoundaryViolation(
        dimension=f"external.{surface}",
        requested=tool_identity,
        evidence="mutating external effect",
    )

    if case == "policy":
        policy = ExternalToolPolicy(mcp="allow", web="ask")
    elif case == "surface":
        surface = "unknown"
        delta = PermissionDelta.external_tool(surface)
        crossing = BoundaryViolation(
            dimension=f"external.{surface}",
            requested=tool_identity,
            evidence="unknown external surface",
        )
    elif case == "delta":
        delta = PermissionDelta.network_domain("example.com")
    elif case == "tool":
        tool_identity = "Github.delete_issue"
    elif case == "crossing":
        crossing = BoundaryViolation(
            dimension="external.web",
            requested=tool_identity,
            evidence="wrong external surface",
        )

    with pytest.raises(ValueError, match=match):
        PermissionDeltaRequest.create_external(
            tool_use_id="tool-invalid",
            tool_name=tool_name,
            final_arguments={"title": "one exact issue"},
            profile=profile,
            policy=policy,
            surface=surface,
            effect_kind="mutating",
            trust_source="trusted-server",
            tool_identity=tool_identity,
            server_identity="Github",
            delta=delta,
            crossing=crossing,
        )


@pytest.mark.asyncio
async def test_external_runtime_approves_once_without_local_boundary() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    reviewer = _Reviewer(PermissionReviewVerdict.approve("allow exact external call"))
    runtime = PermissionRuntime(profile=profile, boundary=None, reviewer=reviewer)
    request = _external_request(profile=profile)

    resolution = await runtime.resolve_external(request)

    assert resolution.status is PermissionResolutionStatus.RETRY_ONCE
    assert reviewer.calls == [request]
    assert runtime.consume_grant(request) is True
    assert runtime.consume_grant(request) is False


@pytest.mark.asyncio
async def test_external_deny_policy_never_calls_reviewer() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="deny")}
    )
    reviewer = _Reviewer(PermissionReviewVerdict.approve("must not override policy"))
    runtime = PermissionRuntime(profile=profile, boundary=None, reviewer=reviewer)
    request = _external_request(profile=profile)

    resolution = await runtime.resolve_external(request)

    assert resolution.status is PermissionResolutionStatus.DENIED
    assert resolution.reason == "external surface policy denies this request"
    assert reviewer.calls == []


def test_external_runtime_parks_and_round_trips_without_local_boundary() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    runtime = PermissionRuntime(profile=profile, boundary=None)
    request = _external_request(profile=profile)
    runtime.park(request, reason="needs a person")

    state = runtime.export_state()
    assert state.schema_version == 2
    assert "boundary_fingerprint" not in state.model_dump(mode="json")
    restored = PermissionRuntime.from_state(
        profile=profile,
        boundary=None,
        state=state,
    )

    assert restored.parked_request == request
    restored.approve_parked(request.request_id)
    assert restored.resume_decided().decision is PermissionReviewDecision.APPROVE
    assert restored.consume_grant(request) is True


def test_v1_local_state_migrates_exact_request_and_ledger() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    request = PermissionDeltaRequest.create(
        tool_use_id="legacy-tool",
        tool_name="Bash",
        final_arguments={"command": "curl https://example.com"},
        profile=profile,
        boundary=boundary,
        delta=PermissionDelta.network_domain("example.com"),
        crossing=BoundaryViolation(
            dimension="network.domain",
            requested="example.com:443",
            evidence="not in allowlist",
        ),
    )
    legacy_request = _legacy_v1_request(request)
    legacy_fingerprint = legacy_request["grant_fingerprint"]
    state = {
        "profile_fingerprint": profile.fingerprint,
        "boundary_fingerprint": boundary.fingerprint,
        "backend_fingerprint": boundary.backend_fingerprint,
        "parked_request": None,
        "parked_reason": None,
        "grants": [legacy_fingerprint],
        "denials": {},
        "last_human_decision": "approve",
        "last_decided_request": legacy_request,
        "last_decision_resumed": False,
    }

    restored = PermissionRuntime.from_state(
        profile=profile,
        boundary=boundary,
        state=state,
    )

    migrated = restored.last_decided_request
    assert migrated is not None
    assert migrated.schema_version == 2
    assert isinstance(migrated.enforcement, LocalBoundaryEvidence)
    assert restored.consume_grant(migrated) is True
    assert "boundary_fingerprint" not in restored.export_state().model_dump(mode="json")


def test_v1_state_requires_matching_boundary_and_rejects_tampered_request() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    legacy_request = _legacy_v1_request(_request())
    state = {
        "profile_fingerprint": profile.fingerprint,
        "boundary_fingerprint": boundary.fingerprint,
        "backend_fingerprint": boundary.backend_fingerprint,
        "parked_request": legacy_request,
        "parked_reason": "person",
        "grants": [],
        "denials": {},
        "last_human_decision": None,
        "last_decided_request": None,
        "last_decision_resumed": False,
    }

    with pytest.raises(ValueError, match="local boundary"):
        PermissionRuntime.from_state(profile=profile, boundary=None, state=state)

    restored = PermissionRuntime.from_state(profile=profile, boundary=boundary, state=state)
    old_request_id = str(legacy_request["request_id"])
    restored.approve_parked(old_request_id[:12])
    assert restored.last_human_decision is PermissionReviewDecision.APPROVE

    tampered = dict(state)
    tampered_request = dict(legacy_request)
    tampered_request["final_arguments"] = {"command": "curl https://attacker.invalid"}
    tampered["parked_request"] = tampered_request
    with pytest.raises(ValueError, match="legacy permission request fingerprint integrity"):
        PermissionRuntime.from_state(profile=profile, boundary=boundary, state=tampered)


def test_v1_request_migration_requires_structured_crossing() -> None:
    request = _request()
    legacy_request = _legacy_v1_request(request)
    legacy_request["crossing"] = request.crossing

    migrated = PermissionDeltaRequest.model_validate(legacy_request)
    assert migrated.crossing == request.crossing

    malformed = _legacy_v1_request(request)
    malformed["crossing"] = "network.domain"
    with pytest.raises(ValueError, match="structured boundary violation"):
        PermissionDeltaRequest.model_validate(malformed)


def test_v1_state_rejects_boundary_backend_and_alias_drift() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    legacy_request = _legacy_v1_request(_request())
    state = {
        "profile_fingerprint": profile.fingerprint,
        "boundary_fingerprint": boundary.fingerprint,
        "backend_fingerprint": boundary.backend_fingerprint,
        "parked_request": legacy_request,
        "parked_reason": "person",
        "grants": [],
        "denials": {},
        "last_human_decision": None,
        "last_decided_request": None,
        "last_decision_resumed": False,
    }

    boundary_drift = dict(state)
    boundary_drift["boundary_fingerprint"] = "wrong"
    with pytest.raises(ValueError, match="boundary drift while resuming"):
        PermissionRuntime.from_state(
            profile=profile,
            boundary=boundary,
            state=boundary_drift,
        )

    backend_drift = dict(state)
    backend_drift["backend_fingerprint"] = "wrong"
    with pytest.raises(ValueError, match="backend drift while resuming"):
        PermissionRuntime.from_state(
            profile=profile,
            boundary=boundary,
            state=backend_drift,
        )

    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    runtime.park(_request(), reason="person")
    current = runtime.export_state().model_dump(mode="json")
    current["request_id_aliases"] = {"legacy-id": "unknown-target"}
    with pytest.raises(ValueError, match="alias integrity"):
        PermissionRuntime.from_state(profile=profile, boundary=boundary, state=current)


def test_v1_state_migrates_exact_denials_and_drops_unbound_bare_entries() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    legacy_request = _legacy_v1_request(_request())
    legacy_fingerprint = legacy_request["request_fingerprint"]
    state = {
        "profile_fingerprint": profile.fingerprint,
        "boundary_fingerprint": boundary.fingerprint,
        "backend_fingerprint": boundary.backend_fingerprint,
        "parked_request": None,
        "parked_reason": None,
        "grants": ["unbound-grant"],
        "denials": {
            legacy_fingerprint: 2,
            "unbound-denial": 4,
            "invalid-count": 0,
        },
        "last_human_decision": "deny",
        "last_decided_request": legacy_request,
        "last_decision_resumed": False,
    }

    restored = PermissionRuntime.from_state(
        profile=profile,
        boundary=boundary,
        state=state,
    )
    migrated = restored.export_state()

    assert migrated.grants == ()
    assert len(migrated.denials) == 1
    assert migrated.denials[0].count == 2
    assert migrated.denials[0].request == migrated.last_decided_request

    state["denials"] = [legacy_fingerprint]
    assert (
        PermissionRuntime.from_state(profile=profile, boundary=boundary, state=state)
        .export_state()
        .denials
        == ()
    )

    state["parked_request"] = 42
    with pytest.raises(ValueError, match="parked_request"):
        PermissionRuntime.from_state(profile=profile, boundary=boundary, state=state)


def test_boundaryless_runtime_rejects_local_request() -> None:
    profile = workspace_runtime_profile()
    runtime = PermissionRuntime(profile=profile, boundary=None)

    with pytest.raises(ValueError, match="local boundary"):
        runtime.park(_request(), reason="must not accept local evidence")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("surface", "web"),
        ("tool_name", "Github.delete_issue"),
        ("trust_source", "strict-default"),
        ("effect_kind", "destructive"),
        ("server_identity", "OtherServer"),
        ("value", "different arguments"),
    ],
)
async def test_external_grant_is_invalid_after_security_fact_drift(
    change: str,
    value: str,
) -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask", web="ask")}
    )
    runtime = PermissionRuntime(
        profile=profile,
        boundary=None,
        reviewer=_Reviewer(PermissionReviewVerdict.approve("allow once")),
    )
    original = _external_request(profile=profile)
    assert (
        await runtime.resolve_external(original)
    ).status is PermissionResolutionStatus.RETRY_ONCE

    changed_args: dict[str, object] = {change: value}
    changed = _external_request(profile=profile, **changed_args)  # type: ignore[arg-type]

    assert changed.grant_fingerprint != original.grant_fingerprint
    assert runtime.consume_grant(changed) is False
    assert runtime.consume_grant(original) is True


def test_external_state_resume_fails_closed_after_policy_drift() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    runtime = PermissionRuntime(profile=profile, boundary=None)
    runtime.park(_external_request(profile=profile), reason="needs a person")
    state = runtime.export_state()
    changed = profile.model_copy(update={"external_tools": ExternalToolPolicy(mcp="allow")})

    with pytest.raises(ValueError, match="profile drift"):
        PermissionRuntime.from_state(profile=changed, boundary=None, state=state)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("profile_facts", {"name": "forged"}, "profile facts drift"),
        ("boundary_facts", {"backend": "forged"}, "boundary facts drift"),
        ("operation_fingerprint", "forged", "operation drift"),
    ],
)
def test_local_runtime_rejects_persisted_evidence_drift(
    field: str,
    value: object,
    match: str,
) -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    request = _request()
    enforcement = request.require_local_evidence().model_copy(update={field: value})
    drifted = request.model_copy(update={"enforcement": enforcement})

    with pytest.raises(ValueError, match=match):
        runtime.park(drifted, reason="must fail closed")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("policy_facts", {"mcp": "allow", "web": "ask"}, "external policy drift"),
        ("policy_fingerprint", "forged", "policy fingerprint drift"),
        ("policy_mode", "allow", "surface policy drift"),
        ("tool_identity", "Github.delete_issue", "tool identity drift"),
        ("surface", "unknown", "surface policy drift"),
    ],
)
def test_external_runtime_rejects_persisted_policy_evidence_drift(
    field: str,
    value: object,
    match: str,
) -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask", web="ask")}
    )
    runtime = PermissionRuntime(profile=profile, boundary=None)
    request = _external_request(profile=profile)
    enforcement = request.enforcement.model_copy(update={field: value})
    drifted = request.model_copy(update={"enforcement": enforcement})

    with pytest.raises(ValueError, match=match):
        runtime.park(drifted, reason="must fail closed")


def test_external_runtime_rejects_persisted_delta_surface_drift() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    runtime = PermissionRuntime(profile=profile, boundary=None)
    request = _external_request(profile=profile)
    drifted = request.model_copy(update={"delta": PermissionDelta.network_domain("mcp")})

    with pytest.raises(ValueError, match="external surface drift"):
        runtime.park(drifted, reason="must fail closed")


def test_filesystem_delta_records_the_minimum_access() -> None:
    read = PermissionDelta.filesystem_path(
        "/outside/input.txt", access=PermissionFilesystemAccess.READ
    )
    write = PermissionDelta.filesystem_path(
        "/outside/output.txt", access=PermissionFilesystemAccess.WRITE
    )

    assert read.filesystem_access is PermissionFilesystemAccess.READ
    assert write.filesystem_access is PermissionFilesystemAccess.WRITE


def test_exact_request_fingerprint_survives_new_tool_use_id() -> None:
    request = _request()
    retry = PermissionDeltaRequest.create(
        tool_use_id="tool-2",
        tool_name=request.tool_name,
        final_arguments=request.final_arguments,
        profile=workspace_runtime_profile(),
        boundary=_boundary(profile_fingerprint=workspace_runtime_profile().fingerprint),
        delta=request.delta,
        crossing=request.crossing,
        data_sources=request.data_sources,
        data_destinations=request.data_destinations,
    )

    assert retry.request_id != request.request_id
    assert retry.request_fingerprint == request.request_fingerprint
    assert retry.grant_fingerprint == request.grant_fingerprint


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
            crossing=BoundaryViolation(
                dimension="network.domain", requested="example.com", evidence="denied"
            ),
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
            crossing=BoundaryViolation(
                dimension="network.domain", requested="example.com", evidence="denied"
            ),
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
async def test_human_grant_matches_exact_retry_with_new_tool_use_id_only() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    request = _request()
    runtime.park(request, reason="needs a person")
    runtime.approve_parked(request.request_id)
    retry = PermissionDeltaRequest.create(
        tool_use_id="tool-2",
        tool_name=request.tool_name,
        final_arguments=request.final_arguments,
        profile=profile,
        boundary=boundary,
        delta=request.delta,
        crossing=request.crossing,
        data_sources=request.data_sources,
        data_destinations=request.data_destinations,
    )

    assert runtime.consume_grant(retry) is True
    assert runtime.consume_grant(retry) is False
    assert runtime.consume_grant(_request(command="curl https://other.example")) is False


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
    second_request = PermissionDeltaRequest.create(
        tool_use_id="tool-2",
        tool_name=request.tool_name,
        final_arguments=request.final_arguments,
        profile=profile,
        boundary=runtime.boundary,
        delta=request.delta,
        crossing=request.crossing,
        data_sources=request.data_sources,
        data_destinations=request.data_destinations,
    )
    second = await runtime.resolve_external(second_request)

    assert first.status is PermissionResolutionStatus.DENIED
    assert second.status is PermissionResolutionStatus.DENIED
    assert second.reason == "denial circuit open"
    assert reviewer.calls == [request]


@pytest.mark.asyncio
async def test_first_reviewer_deny_opens_only_the_exact_request_circuit() -> None:
    profile = workspace_runtime_profile()
    reviewer = _Reviewer(PermissionReviewVerdict.deny("not allowed"))
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
        reviewer=reviewer,
        denial_limit=2,
    )
    original = _request(command="curl https://example.com/a")

    first = await runtime.resolve_external(original)
    same_exact_request = PermissionDeltaRequest.create(
        tool_use_id="tool-retry",
        tool_name=original.tool_name,
        final_arguments=original.final_arguments,
        profile=profile,
        boundary=runtime.boundary,
        delta=original.delta,
        crossing=original.crossing,
        data_sources=original.data_sources,
        data_destinations=original.data_destinations,
    )
    repeated = await runtime.resolve_external(same_exact_request)
    different_arguments = _request(command="curl https://example.com/b")
    distinct = await runtime.resolve_external(different_arguments)

    assert first.status is PermissionResolutionStatus.DENIED
    assert repeated.status is PermissionResolutionStatus.DENIED
    assert repeated.reason == "denial circuit open"
    assert distinct.status is PermissionResolutionStatus.DENIED
    assert reviewer.calls == [original, different_arguments]


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
    assert (
        restored.parked_request.require_local_evidence().backend_fingerprint
        == boundary.backend_fingerprint
    )

    drifted = _boundary(profile_fingerprint=profile.fingerprint, backend_version="2")
    with pytest.raises(ValueError, match=r"(boundary|backend) drift"):
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
    resumed = runtime.resume_decided()
    assert resumed.request_fingerprint == request.request_fingerprint
    assert resumed.decision is PermissionReviewDecision.APPROVE
    with pytest.raises(ValueError, match="no permission decision"):
        runtime.resume_decided()
    assert runtime.consume_grant(request) is True

    runtime.park(request, reason="needs a person")
    runtime.deny_parked(request.request_id, reason="user denied")
    assert runtime.parked_request is None
    assert runtime.consume_grant(request) is False
    assert runtime.last_human_decision is PermissionReviewDecision.DENY


def test_clear_pending_state_drops_conversation_decision_but_keeps_ledger() -> None:
    profile = workspace_runtime_profile()
    runtime = PermissionRuntime(
        profile=profile,
        boundary=_boundary(profile_fingerprint=profile.fingerprint),
    )
    request = _request()
    runtime.park(request, reason="needs a person")
    runtime.approve_parked(request.request_id)

    runtime.clear_pending_state()

    state = runtime.export_state()
    assert state.parked_request is None
    assert state.parked_reason is None
    assert state.last_human_decision is None
    assert state.last_decided_request is None
    assert state.last_decision_resumed is False
    assert runtime.consume_grant(request) is True


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
        crossing=request.crossing,
        data_sources=request.data_sources,
        data_destinations=request.data_destinations,
    )

    resolution = await runtime.resolve_external(request)

    assert resolution.status is PermissionResolutionStatus.DENIED
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_hard_deny_crossing_never_calls_reviewer_even_if_delta_is_not_marked() -> None:
    profile = workspace_runtime_profile()
    reviewer = _Reviewer(PermissionReviewVerdict.approve("must not be used"))
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    runtime = PermissionRuntime(profile=profile, boundary=boundary, reviewer=reviewer)
    request = PermissionDeltaRequest.create(
        tool_use_id="tool-hard-crossing",
        tool_name="Write",
        final_arguments={"path": ".git/config", "content": "x"},
        profile=profile,
        boundary=boundary,
        delta=PermissionDelta.filesystem_path(".git/config"),
        crossing=BoundaryViolation(
            dimension="filesystem.write",
            requested=".git/config",
            evidence="protected path",
            hard_deny=True,
        ),
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

    profile_drift = request.model_copy(
        update={
            "enforcement": request.enforcement.model_copy(update={"profile_fingerprint": "wrong"})
        }
    )
    with pytest.raises(ValueError, match="profile drift"):
        runtime.consume_grant(profile_drift)
    boundary_drift = request.model_copy(
        update={
            "enforcement": request.require_local_evidence().model_copy(
                update={"boundary_fingerprint": "wrong"}
            )
        }
    )
    with pytest.raises(ValueError, match="boundary drift"):
        runtime.consume_grant(boundary_drift)
    other_backend = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="other",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    backend_drift = PermissionDeltaRequest.create(
        tool_use_id=request.tool_use_id,
        tool_name=request.tool_name,
        final_arguments=request.final_arguments,
        profile=profile,
        boundary=other_backend,
        delta=request.delta,
        crossing=request.crossing,
        data_sources=request.data_sources,
        data_destinations=request.data_destinations,
    )
    with pytest.raises(ValueError, match="backend drift"):
        runtime.consume_grant(backend_drift)
    tampered_grant = request.model_copy(update={"grant_fingerprint": "wrong"})
    with pytest.raises(ValueError, match="integrity"):
        runtime.consume_grant(tampered_grant)


def test_state_dict_round_trip_and_profile_drift_are_rejected() -> None:
    profile = workspace_runtime_profile()
    boundary = _boundary(profile_fingerprint=profile.fingerprint)
    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    runtime.park(_request(), reason="state carries exact local evidence")
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

    backend_tampered = dict(state)
    parked = dict(backend_tampered["parked_request"])
    enforcement = dict(parked["enforcement"])
    enforcement["backend_fingerprint"] = "wrong"
    parked["enforcement"] = enforcement
    backend_tampered["parked_request"] = parked
    with pytest.raises(ValueError, match="backend drift"):
        PermissionRuntime.from_state(
            profile=profile,
            boundary=boundary,
            state=backend_tampered,
        )
