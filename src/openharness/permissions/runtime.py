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


class PermissionDelta(_FrozenModel):
    kind: PermissionDeltaKind
    value: str = Field(min_length=1)
    hard_deny: bool = False

    @classmethod
    def network_domain(cls, domain: str) -> PermissionDelta:
        return cls(kind=PermissionDeltaKind.NETWORK_DOMAIN, value=domain)

    @classmethod
    def external_tool(cls, surface: str) -> PermissionDelta:
        return cls(kind=PermissionDeltaKind.EXTERNAL_TOOL, value=surface)

    @classmethod
    def filesystem_path(cls, path: str) -> PermissionDelta:
        return cls(kind=PermissionDeltaKind.FILESYSTEM_PATH, value=path)


class PermissionDeltaRequest(_FrozenModel):
    request_id: str
    tool_use_id: str
    tool_name: str
    final_arguments: dict[str, Any]
    arguments_fingerprint: str
    profile_fingerprint: str
    boundary_fingerprint: str
    backend: str
    delta: PermissionDelta

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
    ) -> PermissionDeltaRequest:
        if not boundary.is_verified:
            raise ValueError("permission resolution requires a verified boundary")
        if boundary.profile_fingerprint != profile.fingerprint:
            raise ValueError("boundary profile fingerprint does not match active profile")
        arguments_fingerprint = _fingerprint(final_arguments)
        identity = {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "arguments_fingerprint": arguments_fingerprint,
            "profile_fingerprint": profile.fingerprint,
            "boundary_fingerprint": boundary.fingerprint,
            "delta": delta.model_dump(mode="json"),
        }
        return cls(
            request_id=_fingerprint(identity),
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            final_arguments=final_arguments,
            arguments_fingerprint=arguments_fingerprint,
            profile_fingerprint=profile.fingerprint,
            boundary_fingerprint=boundary.fingerprint,
            backend=boundary.backend,
            delta=delta,
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


class PermissionRuntimeState(_FrozenModel):
    profile_fingerprint: str
    boundary_fingerprint: str
    parked_request: PermissionDeltaRequest | None = None
    parked_reason: str | None = None
    grants: tuple[str, ...] = ()
    denials: dict[str, int] = Field(default_factory=dict)
    last_human_decision: PermissionReviewDecision | None = None
    last_decided_request: PermissionDeltaRequest | None = None


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
        if request.delta.hard_deny:
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason="hard deny cannot be reviewed",
                request=request,
            )
        if self._denials.get(request.request_id, 0) >= self.denial_limit:
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
            self._grants.add(request.request_id)
            return PermissionResolution(
                status=PermissionResolutionStatus.RETRY_ONCE,
                reason=verdict.reason,
                request=request,
            )
        if verdict.decision is PermissionReviewDecision.DENY:
            self._denials[request.request_id] = self._denials.get(request.request_id, 0) + 1
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
        if request.request_id not in self._grants:
            return False
        self._grants.remove(request.request_id)
        return True

    def park(self, request: PermissionDeltaRequest, *, reason: str) -> None:
        self._validate_request(request)
        self.parked_request = request
        self.parked_reason = reason

    def approve_parked(self, request_id: str) -> None:
        request = self._require_parked(request_id)
        self._grants.add(request.request_id)
        self.parked_request = None
        self.parked_reason = None
        self.last_human_decision = PermissionReviewDecision.APPROVE
        self.last_decided_request = request

    def deny_parked(self, request_id: str, *, reason: str) -> None:
        request = self._require_parked(request_id)
        self._denials[request.request_id] = self.denial_limit
        self.parked_request = None
        self.parked_reason = reason
        self.last_human_decision = PermissionReviewDecision.DENY
        self.last_decided_request = request

    def _require_parked(self, request_id: str) -> PermissionDeltaRequest:
        request = self.parked_request
        if request is None or len(request_id) < 8 or not request.request_id.startswith(request_id):
            raise ValueError("no matching parked permission request")
        return request

    def _validate_request(self, request: PermissionDeltaRequest) -> None:
        if request.profile_fingerprint != self.profile.fingerprint:
            raise ValueError("permission request profile drift")
        if request.boundary_fingerprint != self.boundary.fingerprint:
            raise ValueError("permission request boundary drift")

    def export_state(self) -> PermissionRuntimeState:
        return PermissionRuntimeState(
            profile_fingerprint=self.profile.fingerprint,
            boundary_fingerprint=self.boundary.fingerprint,
            parked_request=self.parked_request,
            parked_reason=self.parked_reason,
            grants=tuple(sorted(self._grants)),
            denials=dict(sorted(self._denials.items())),
            last_human_decision=self.last_human_decision,
            last_decided_request=self.last_decided_request,
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
        runtime._grants = set(parsed.grants)
        runtime._denials = dict(parsed.denials)
        return runtime
