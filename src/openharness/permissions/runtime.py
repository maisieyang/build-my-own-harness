"""Exact, boundary-derived permission escalation lifecycle."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from openharness.execution.boundary import (
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from openharness.permissions.profile import RuntimePermissionProfile


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class PermissionDeltaKind(str, Enum):
    NETWORK_DOMAIN = "network_domain"
    EXTERNAL_TOOL = "external_tool"
    FILESYSTEM_PATH = "filesystem_path"


class PermissionFilesystemAccess(str, Enum):
    READ = "read"
    WRITE = "write"
    SEARCH = "search"


class PermissionDelta(_FrozenModel):
    kind: PermissionDeltaKind
    value: str = Field(min_length=1)
    hard_deny: bool = False
    filesystem_access: PermissionFilesystemAccess | None = None

    @classmethod
    def network_domain(cls, domain: str) -> PermissionDelta:
        return cls(kind=PermissionDeltaKind.NETWORK_DOMAIN, value=domain)

    @classmethod
    def external_tool(cls, surface: str) -> PermissionDelta:
        return cls(kind=PermissionDeltaKind.EXTERNAL_TOOL, value=surface)

    @classmethod
    def filesystem_path(
        cls,
        path: str,
        *,
        access: PermissionFilesystemAccess = PermissionFilesystemAccess.WRITE,
        hard_deny: bool = False,
    ) -> PermissionDelta:
        return cls(
            kind=PermissionDeltaKind.FILESYSTEM_PATH,
            value=path,
            hard_deny=hard_deny,
            filesystem_access=access,
        )


class PermissionDeltaRequest(_FrozenModel):
    request_id: str
    request_fingerprint: str
    grant_fingerprint: str
    tool_use_id: str
    tool_name: str
    final_arguments: dict[str, Any]
    arguments_fingerprint: str
    profile_fingerprint: str
    boundary_fingerprint: str
    backend: str
    backend_fingerprint: str
    profile_facts: dict[str, Any]
    boundary_facts: dict[str, Any]
    delta: PermissionDelta
    crossing: BoundaryViolation
    authorization_context: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    data_destinations: tuple[str, ...] = ()

    def _expected_request_fingerprint(self) -> str:
        exact_request = {
            "tool_name": self.tool_name,
            "arguments_fingerprint": self.arguments_fingerprint,
            "profile_fingerprint": self.profile_fingerprint,
            "boundary_fingerprint": self.boundary_fingerprint,
            "backend_fingerprint": self.backend_fingerprint,
            "authorization_context": list(self.authorization_context),
            "profile_facts": self.profile_facts,
            "boundary_facts": self.boundary_facts,
            "delta": self.delta.model_dump(mode="json"),
            "crossing": {
                "dimension": self.crossing.dimension,
                "requested": self.crossing.requested,
                "evidence": self.crossing.evidence,
                "hard_deny": self.crossing.hard_deny,
            },
            "data_sources": sorted(set(self.data_sources)),
            "data_destinations": sorted(set(self.data_destinations)),
        }
        return _fingerprint(exact_request)

    def validate_integrity(self) -> None:
        expected_arguments = _fingerprint(self.final_arguments)
        expected_request = self._expected_request_fingerprint()
        expected_event = _fingerprint(
            {"tool_use_id": self.tool_use_id, "request_fingerprint": expected_request}
        )
        if (
            self.arguments_fingerprint != expected_arguments
            or self.request_fingerprint != expected_request
            or self.grant_fingerprint != expected_request
            or self.request_id != expected_event
        ):
            raise ValueError("permission request fingerprint integrity failure")

    @classmethod
    def create(
        cls,
        *,
        tool_use_id: str,
        tool_name: str,
        final_arguments: dict[str, Any],
        profile: RuntimePermissionProfile,
        boundary: EnforcedBoundary,
        delta: PermissionDelta,
        crossing: BoundaryViolation,
        data_sources: tuple[str, ...] = (),
        data_destinations: tuple[str, ...] = (),
        authorization_context: tuple[str, ...] = (),
    ) -> PermissionDeltaRequest:
        if not boundary.is_verified:
            raise ValueError("permission resolution requires a verified boundary")
        if boundary.profile_fingerprint != profile.fingerprint:
            raise ValueError("boundary profile fingerprint does not match active profile")
        arguments_fingerprint = _fingerprint(final_arguments)
        provisional = cls(
            request_id="pending",
            request_fingerprint="pending",
            grant_fingerprint="pending",
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            final_arguments=final_arguments,
            arguments_fingerprint=arguments_fingerprint,
            profile_fingerprint=profile.fingerprint,
            boundary_fingerprint=boundary.fingerprint,
            backend=boundary.backend,
            backend_fingerprint=boundary.backend_fingerprint,
            authorization_context=tuple(authorization_context),
            profile_facts=profile.normalized(),
            boundary_facts=boundary.normalized(),
            delta=delta,
            crossing=crossing,
            data_sources=tuple(sorted(set(data_sources))),
            data_destinations=tuple(sorted(set(data_destinations))),
        )
        request_fingerprint = provisional._expected_request_fingerprint()
        return provisional.model_copy(
            update={
                "request_id": _fingerprint(
                    {
                        "tool_use_id": tool_use_id,
                        "request_fingerprint": request_fingerprint,
                    }
                ),
                "request_fingerprint": request_fingerprint,
                "grant_fingerprint": request_fingerprint,
            }
        )


class PermissionReviewDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    DEFER = "defer"
    FAILED = "failed"


class PermissionReviewVerdict(_FrozenModel):
    decision: PermissionReviewDecision
    reason: str = Field(min_length=1)

    @classmethod
    def approve(cls, reason: str) -> PermissionReviewVerdict:
        return cls(decision=PermissionReviewDecision.APPROVE, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> PermissionReviewVerdict:
        return cls(decision=PermissionReviewDecision.DENY, reason=reason)

    @classmethod
    def defer(cls, reason: str) -> PermissionReviewVerdict:
        return cls(decision=PermissionReviewDecision.DEFER, reason=reason)

    @classmethod
    def failed(cls, reason: str) -> PermissionReviewVerdict:
        return cls(decision=PermissionReviewDecision.FAILED, reason=reason)


class PermissionReviewer(Protocol):
    async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict: ...


class PermissionResolutionStatus(str, Enum):
    CONTAINED = "contained"
    RETRY_ONCE = "retry_once"
    DENIED = "denied"
    PARKED = "parked"


class PermissionResolution(_FrozenModel):
    status: PermissionResolutionStatus
    reason: str
    request: PermissionDeltaRequest | None = None


class PermissionResumeTransition(_FrozenModel):
    request_id: str
    request_fingerprint: str
    grant_fingerprint: str
    decision: PermissionReviewDecision


class PermissionRuntimeState(_FrozenModel):
    profile_fingerprint: str
    boundary_fingerprint: str
    backend_fingerprint: str
    parked_request: PermissionDeltaRequest | None = None
    parked_reason: str | None = None
    grants: tuple[str, ...] = ()
    denials: dict[str, int] = Field(default_factory=dict)
    last_human_decision: PermissionReviewDecision | None = None
    last_decided_request: PermissionDeltaRequest | None = None
    last_decision_resumed: bool = False


class PermissionRuntime:
    """Session-scoped resolver. It cannot operate without verified facts."""

    def __init__(
        self,
        *,
        profile: RuntimePermissionProfile,
        boundary: EnforcedBoundary,
        reviewer: PermissionReviewer | None = None,
        denial_limit: int = 2,
    ) -> None:
        if not boundary.is_verified:
            raise ValueError("permission runtime requires a verified boundary")
        if boundary.profile_fingerprint != profile.fingerprint:
            raise ValueError("boundary profile fingerprint does not match active profile")
        if denial_limit < 1:
            raise ValueError("denial_limit must be positive")
        self.profile = profile
        self.boundary = boundary
        self.reviewer = reviewer
        self.denial_limit = denial_limit
        self.parked_request: PermissionDeltaRequest | None = None
        self.parked_reason: str | None = None
        self.last_human_decision: PermissionReviewDecision | None = None
        self.last_decided_request: PermissionDeltaRequest | None = None
        self._last_decision_resumed = False
        self._grants: set[str] = set()
        self._denials: dict[str, int] = {}

    async def resolve_boundary_result(
        self,
        result: ExecutionResult,
        *,
        request_factory: Callable[[], PermissionDeltaRequest],
    ) -> PermissionResolution:
        if not isinstance(result, BoundaryViolation):
            return PermissionResolution(
                status=PermissionResolutionStatus.CONTAINED,
                reason="inside verified boundary",
            )
        return await self.resolve_external(request_factory())

    async def resolve_external(self, request: PermissionDeltaRequest) -> PermissionResolution:
        self._validate_request(request)
        if request.delta.hard_deny or request.crossing.hard_deny:
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason="hard deny cannot be reviewed",
                request=request,
            )
        if self._denials.get(request.request_fingerprint, 0) >= self.denial_limit:
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason="denial circuit open",
                request=request,
            )
        if self.reviewer is None:
            self.park(request, reason="no automatic reviewer is available")
            return PermissionResolution(
                status=PermissionResolutionStatus.PARKED,
                reason=self.parked_reason or "parked",
                request=request,
            )
        try:
            verdict = await self.reviewer.review(request)
        except Exception as exc:
            self.park(request, reason=f"reviewer failed: {exc}")
            return PermissionResolution(
                status=PermissionResolutionStatus.PARKED,
                reason=self.parked_reason or "parked",
                request=request,
            )
        if verdict.decision is PermissionReviewDecision.APPROVE:
            self._grants.add(request.grant_fingerprint)
            return PermissionResolution(
                status=PermissionResolutionStatus.RETRY_ONCE,
                reason=verdict.reason,
                request=request,
            )
        if verdict.decision is PermissionReviewDecision.DENY:
            self._denials[request.request_fingerprint] = (
                self._denials.get(request.request_fingerprint, 0) + 1
            )
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason=verdict.reason,
                request=request,
            )
        self.park(request, reason=verdict.reason)
        return PermissionResolution(
            status=PermissionResolutionStatus.PARKED,
            reason=verdict.reason,
            request=request,
        )

    def consume_grant(self, request: PermissionDeltaRequest) -> bool:
        self._validate_request(request)
        if request.grant_fingerprint not in self._grants:
            return False
        self._grants.remove(request.grant_fingerprint)
        return True

    def park(self, request: PermissionDeltaRequest, *, reason: str) -> None:
        self._validate_request(request)
        self.parked_request = request
        self.parked_reason = reason
        self.last_human_decision = None
        self.last_decided_request = None
        self._last_decision_resumed = False

    def approve_parked(self, request_id: str) -> None:
        request = self._require_parked(request_id)
        self._grants.add(request.grant_fingerprint)
        self.parked_request = None
        self.parked_reason = None
        self.last_human_decision = PermissionReviewDecision.APPROVE
        self.last_decided_request = request
        self._last_decision_resumed = False

    def deny_parked(self, request_id: str, *, reason: str) -> None:
        request = self._require_parked(request_id)
        self._denials[request.request_fingerprint] = self.denial_limit
        self.parked_request = None
        self.parked_reason = reason
        self.last_human_decision = PermissionReviewDecision.DENY
        self.last_decided_request = request
        self._last_decision_resumed = False

    def resume_decided(self) -> PermissionResumeTransition:
        request = self.last_decided_request
        decision = self.last_human_decision
        if request is None or decision is None or self._last_decision_resumed:
            raise ValueError("no permission decision is ready to resume")
        self._validate_request(request)
        self._last_decision_resumed = True
        return PermissionResumeTransition(
            request_id=request.request_id,
            request_fingerprint=request.request_fingerprint,
            grant_fingerprint=request.grant_fingerprint,
            decision=decision,
        )

    def _require_parked(self, request_id: str) -> PermissionDeltaRequest:
        request = self.parked_request
        if request is None or len(request_id) < 8 or not request.request_id.startswith(request_id):
            raise ValueError("no matching parked permission request")
        return request

    def _validate_request(self, request: PermissionDeltaRequest) -> None:
        if request.profile_fingerprint != self.profile.fingerprint:
            raise ValueError("permission request profile drift")
        if request.backend_fingerprint != self.boundary.backend_fingerprint:
            raise ValueError("permission request backend drift")
        if request.boundary_fingerprint != self.boundary.fingerprint:
            raise ValueError("permission request boundary drift")
        request.validate_integrity()

    def export_state(self) -> PermissionRuntimeState:
        return PermissionRuntimeState(
            profile_fingerprint=self.profile.fingerprint,
            boundary_fingerprint=self.boundary.fingerprint,
            backend_fingerprint=self.boundary.backend_fingerprint,
            parked_request=self.parked_request,
            parked_reason=self.parked_reason,
            grants=tuple(sorted(self._grants)),
            denials=dict(sorted(self._denials.items())),
            last_human_decision=self.last_human_decision,
            last_decided_request=self.last_decided_request,
            last_decision_resumed=self._last_decision_resumed,
        )

    @classmethod
    def from_state(
        cls,
        *,
        profile: RuntimePermissionProfile,
        boundary: EnforcedBoundary,
        state: PermissionRuntimeState | dict[str, Any],
        reviewer: PermissionReviewer | None = None,
        denial_limit: int = 2,
    ) -> PermissionRuntime:
        parsed = (
            state
            if isinstance(state, PermissionRuntimeState)
            else PermissionRuntimeState.model_validate(state)
        )
        if parsed.profile_fingerprint != profile.fingerprint:
            raise ValueError("permission profile drift while resuming")
        if parsed.boundary_fingerprint != boundary.fingerprint:
            raise ValueError("permission boundary drift while resuming")
        if parsed.backend_fingerprint != boundary.backend_fingerprint:
            raise ValueError("permission backend drift while resuming")
        runtime = cls(
            profile=profile,
            boundary=boundary,
            reviewer=reviewer,
            denial_limit=denial_limit,
        )
        runtime.parked_request = parsed.parked_request
        runtime.parked_reason = parsed.parked_reason
        runtime.last_human_decision = parsed.last_human_decision
        runtime.last_decided_request = parsed.last_decided_request
        runtime._last_decision_resumed = parsed.last_decision_resumed
        runtime._grants = set(parsed.grants)
        runtime._denials = dict(parsed.denials)
        return runtime
