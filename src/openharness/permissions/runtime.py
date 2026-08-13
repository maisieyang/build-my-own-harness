"""Exact, boundary-derived permission escalation lifecycle."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openharness.execution.boundary import (
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionResult,
)
from openharness.protocols.content import ToolResultBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage

if TYPE_CHECKING:
    from collections.abc import Callable

    from openharness.permissions.profile import ExternalToolPolicy, RuntimePermissionProfile


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


class PermissionEvidenceKind(str, Enum):
    LOCAL_BOUNDARY = "local_boundary"
    EXTERNAL_POLICY = "external_policy"


class LocalBoundaryEvidence(_FrozenModel):
    """Verified local enforcement facts bound to one requested operation."""

    kind: Literal[PermissionEvidenceKind.LOCAL_BOUNDARY] = PermissionEvidenceKind.LOCAL_BOUNDARY
    profile_fingerprint: str = Field(min_length=1)
    profile_facts: dict[str, Any]
    boundary_fingerprint: str = Field(min_length=1)
    boundary_facts: dict[str, Any]
    backend: str = Field(min_length=1)
    backend_fingerprint: str = Field(min_length=1)
    operation_fingerprint: str = Field(min_length=1)


class ExternalPolicyEvidence(_FrozenModel):
    """Active external-policy facts; it makes no local sandbox claim."""

    kind: Literal[PermissionEvidenceKind.EXTERNAL_POLICY] = PermissionEvidenceKind.EXTERNAL_POLICY
    profile_fingerprint: str = Field(min_length=1)
    profile_facts: dict[str, Any]
    surface: str = Field(min_length=1)
    effect_kind: str = Field(min_length=1)
    trust_source: str = Field(min_length=1)
    tool_identity: str = Field(min_length=1)
    server_identity: str | None = None
    policy_mode: str = Field(min_length=1)
    policy_facts: dict[str, Any]
    policy_fingerprint: str = Field(min_length=1)


PermissionEnforcementEvidence = Annotated[
    LocalBoundaryEvidence | ExternalPolicyEvidence,
    Field(discriminator="kind"),
]


def _operation_fingerprint(tool_name: str, final_arguments: dict[str, Any]) -> str:
    return _fingerprint(
        {
            "tool_name": tool_name,
            "final_arguments": final_arguments,
        }
    )


def _crossing_facts(crossing: BoundaryViolation) -> dict[str, Any]:
    return {
        "dimension": crossing.dimension,
        "requested": crossing.requested,
        "evidence": crossing.evidence,
        "hard_deny": crossing.hard_deny,
    }


def _parse_crossing(value: Any) -> BoundaryViolation:
    if isinstance(value, BoundaryViolation):
        return value
    if not isinstance(value, dict):
        raise ValueError("permission crossing must be a structured boundary violation")
    return BoundaryViolation(
        dimension=value["dimension"],
        requested=value["requested"],
        evidence=value["evidence"],
        hard_deny=value.get("hard_deny", False),
    )


def _exact_request_facts(
    *,
    tool_name: str,
    arguments_fingerprint: str,
    enforcement: LocalBoundaryEvidence | ExternalPolicyEvidence,
    authorization_context: tuple[str, ...],
    delta: PermissionDelta,
    crossing: BoundaryViolation,
    data_sources: tuple[str, ...],
    data_destinations: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "arguments_fingerprint": arguments_fingerprint,
        "enforcement": enforcement.model_dump(mode="json"),
        "authorization_context": list(authorization_context),
        "delta": delta.model_dump(mode="json"),
        "crossing": _crossing_facts(crossing),
        "data_sources": sorted(set(data_sources)),
        "data_destinations": sorted(set(data_destinations)),
    }


def _legacy_request_fingerprint(value: dict[str, Any]) -> str:
    """Recompute the v1 flat-local fingerprint before accepting migration."""
    crossing = _parse_crossing(value["crossing"])
    delta = PermissionDelta.model_validate(value["delta"])
    exact_request = {
        "tool_name": value["tool_name"],
        "arguments_fingerprint": value["arguments_fingerprint"],
        "profile_fingerprint": value["profile_fingerprint"],
        "boundary_fingerprint": value["boundary_fingerprint"],
        "backend_fingerprint": value["backend_fingerprint"],
        "authorization_context": list(value.get("authorization_context", ())),
        "profile_facts": value["profile_facts"],
        "boundary_facts": value["boundary_facts"],
        "delta": delta.model_dump(mode="json"),
        "crossing": _crossing_facts(crossing),
        "data_sources": sorted(set(value.get("data_sources", ()))),
        "data_destinations": sorted(set(value.get("data_destinations", ()))),
    }
    return _fingerprint(exact_request)


class PermissionDeltaRequest(_FrozenModel):
    schema_version: Literal[2] = 2
    request_id: str
    request_fingerprint: str
    grant_fingerprint: str
    tool_use_id: str
    tool_name: str
    final_arguments: dict[str, Any]
    arguments_fingerprint: str
    enforcement: PermissionEnforcementEvidence
    delta: PermissionDelta
    crossing: BoundaryViolation
    authorization_context: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    data_destinations: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def migrate_flat_local_v1(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "enforcement" in value:
            return value

        required = {
            "profile_fingerprint",
            "boundary_fingerprint",
            "backend",
            "backend_fingerprint",
            "profile_facts",
            "boundary_facts",
        }
        if not required.issubset(value):
            return value

        migrated = dict(value)
        expected_arguments = _fingerprint(migrated["final_arguments"])
        expected_request = _legacy_request_fingerprint(migrated)
        expected_id = _fingerprint(
            {
                "tool_use_id": migrated["tool_use_id"],
                "request_fingerprint": expected_request,
            }
        )
        if (
            migrated.get("arguments_fingerprint") != expected_arguments
            or migrated.get("request_fingerprint") != expected_request
            or migrated.get("grant_fingerprint") != expected_request
            or migrated.get("request_id") != expected_id
        ):
            raise ValueError("legacy permission request fingerprint integrity failure")

        enforcement = LocalBoundaryEvidence(
            profile_fingerprint=migrated.pop("profile_fingerprint"),
            profile_facts=migrated.pop("profile_facts"),
            boundary_fingerprint=migrated.pop("boundary_fingerprint"),
            boundary_facts=migrated.pop("boundary_facts"),
            backend=migrated.pop("backend"),
            backend_fingerprint=migrated.pop("backend_fingerprint"),
            operation_fingerprint=_operation_fingerprint(
                migrated["tool_name"], migrated["final_arguments"]
            ),
        )
        delta = PermissionDelta.model_validate(migrated["delta"])
        crossing = _parse_crossing(migrated["crossing"])
        request_fingerprint = _fingerprint(
            _exact_request_facts(
                tool_name=migrated["tool_name"],
                arguments_fingerprint=expected_arguments,
                enforcement=enforcement,
                authorization_context=tuple(migrated.get("authorization_context", ())),
                delta=delta,
                crossing=crossing,
                data_sources=tuple(migrated.get("data_sources", ())),
                data_destinations=tuple(migrated.get("data_destinations", ())),
            )
        )
        migrated.update(
            {
                "schema_version": 2,
                "arguments_fingerprint": expected_arguments,
                "enforcement": enforcement.model_dump(mode="json"),
                "request_fingerprint": request_fingerprint,
                "grant_fingerprint": request_fingerprint,
                "request_id": _fingerprint(
                    {
                        "tool_use_id": migrated["tool_use_id"],
                        "request_fingerprint": request_fingerprint,
                    }
                ),
            }
        )
        return migrated

    @property
    def profile_fingerprint(self) -> str:
        return self.enforcement.profile_fingerprint

    @property
    def profile_facts(self) -> dict[str, Any]:
        return self.enforcement.profile_facts

    def require_local_evidence(self) -> LocalBoundaryEvidence:
        evidence = self.enforcement
        if not isinstance(evidence, LocalBoundaryEvidence):
            raise ValueError("external permission request has no local boundary evidence")
        return evidence

    @property
    def boundary_fingerprint(self) -> str:
        return self.require_local_evidence().boundary_fingerprint

    @property
    def boundary_facts(self) -> dict[str, Any]:
        return self.require_local_evidence().boundary_facts

    @property
    def backend(self) -> str:
        return self.require_local_evidence().backend

    @property
    def backend_fingerprint(self) -> str:
        return self.require_local_evidence().backend_fingerprint

    def _expected_request_fingerprint(self) -> str:
        return _fingerprint(
            _exact_request_facts(
                tool_name=self.tool_name,
                arguments_fingerprint=self.arguments_fingerprint,
                enforcement=self.enforcement,
                authorization_context=self.authorization_context,
                delta=self.delta,
                crossing=self.crossing,
                data_sources=self.data_sources,
                data_destinations=self.data_destinations,
            )
        )

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
        enforcement = LocalBoundaryEvidence(
            profile_fingerprint=profile.fingerprint,
            profile_facts=profile.normalized(),
            boundary_fingerprint=boundary.fingerprint,
            boundary_facts=boundary.normalized(),
            backend=boundary.backend,
            backend_fingerprint=boundary.backend_fingerprint,
            operation_fingerprint=_operation_fingerprint(tool_name, final_arguments),
        )
        return cls._create(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            final_arguments=final_arguments,
            enforcement=enforcement,
            delta=delta,
            crossing=crossing,
            data_sources=data_sources,
            data_destinations=data_destinations,
            authorization_context=authorization_context,
        )

    @classmethod
    def create_external(
        cls,
        *,
        tool_use_id: str,
        tool_name: str,
        final_arguments: dict[str, Any],
        profile: RuntimePermissionProfile,
        policy: ExternalToolPolicy,
        surface: str,
        effect_kind: str,
        trust_source: str,
        tool_identity: str,
        server_identity: str | None,
        delta: PermissionDelta,
        crossing: BoundaryViolation,
        data_sources: tuple[str, ...] = (),
        data_destinations: tuple[str, ...] = (),
        authorization_context: tuple[str, ...] = (),
    ) -> PermissionDeltaRequest:
        policy_facts = policy.model_dump(mode="json")
        if policy_facts != profile.external_tools.model_dump(mode="json"):
            raise ValueError("external policy does not match active permission profile")
        try:
            policy_mode = policy_facts[surface]
        except KeyError as exc:
            raise ValueError(f"unknown external policy surface: {surface}") from exc
        if delta.kind is not PermissionDeltaKind.EXTERNAL_TOOL or delta.value != surface:
            raise ValueError("external request delta does not match its policy surface")
        if tool_identity != tool_name:
            raise ValueError("external tool identity does not match dispatched tool")
        if crossing.dimension != f"external.{surface}" or crossing.requested != tool_identity:
            raise ValueError("external crossing does not match its surface and tool identity")
        enforcement = ExternalPolicyEvidence(
            profile_fingerprint=profile.fingerprint,
            profile_facts=profile.normalized(),
            surface=surface,
            effect_kind=effect_kind,
            trust_source=trust_source,
            tool_identity=tool_identity,
            server_identity=server_identity,
            policy_mode=policy_mode,
            policy_facts=policy_facts,
            policy_fingerprint=_fingerprint(policy_facts),
        )
        return cls._create(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            final_arguments=final_arguments,
            enforcement=enforcement,
            delta=delta,
            crossing=crossing,
            data_sources=data_sources,
            data_destinations=data_destinations,
            authorization_context=authorization_context,
        )

    @classmethod
    def _create(
        cls,
        *,
        tool_use_id: str,
        tool_name: str,
        final_arguments: dict[str, Any],
        enforcement: LocalBoundaryEvidence | ExternalPolicyEvidence,
        delta: PermissionDelta,
        crossing: BoundaryViolation,
        data_sources: tuple[str, ...],
        data_destinations: tuple[str, ...],
        authorization_context: tuple[str, ...],
    ) -> PermissionDeltaRequest:
        arguments_fingerprint = _fingerprint(final_arguments)
        provisional = cls(
            request_id="pending",
            request_fingerprint="pending",
            grant_fingerprint="pending",
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            final_arguments=final_arguments,
            arguments_fingerprint=arguments_fingerprint,
            enforcement=enforcement,
            authorization_context=tuple(authorization_context),
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


class PermissionParkedReviewStatus(str, Enum):
    """Why a reviewable exact request reached the human decision surface."""

    MANUAL = "manual"
    DEFERRED = "deferred"
    FAILED = "failed"


class PermissionResolutionStatus(str, Enum):
    CONTAINED = "contained"
    RETRY_ONCE = "retry_once"
    DENIED = "denied"
    PARKED = "parked"


class PermissionResolution(_FrozenModel):
    status: PermissionResolutionStatus
    reason: str
    request: PermissionDeltaRequest | None = None


class ParkedControllerState(_FrozenModel):
    """Controller state that must survive a permission interruption."""

    mode: Literal["default", "plan", "goal"]
    goal_condition: str | None = None


class ParkedContinuation(_FrozenModel):
    """Serializable dispatch continuation owned by the harness.

    It binds the exact permission request to the typed conversation and the
    position of the interrupted tool inside the assistant's complete tool
    batch.  The permission error shown in the UI is deliberately not part of
    ``completed_tool_results``: it is a control-plane event, not model input.
    """

    schema_version: Literal[1] = 1
    request: PermissionDeltaRequest
    messages: tuple[ConversationMessage, ...]
    assistant_message: ConversationMessage
    tool_uses: tuple[ToolUseBlock, ...]
    completed_tool_results: tuple[ToolResultBlock, ...] = ()
    next_tool_index: int = Field(ge=0)
    controller: ParkedControllerState
    continuation_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        request: PermissionDeltaRequest,
        messages: tuple[ConversationMessage, ...],
        assistant_message: ConversationMessage,
        tool_uses: tuple[ToolUseBlock, ...],
        completed_tool_results: tuple[ToolResultBlock, ...],
        next_tool_index: int,
        controller: ParkedControllerState,
    ) -> ParkedContinuation:
        facts = cls._fingerprint_facts(
            request=request,
            messages=messages,
            assistant_message=assistant_message,
            tool_uses=tool_uses,
            completed_tool_results=completed_tool_results,
            next_tool_index=next_tool_index,
            controller=controller,
        )
        continuation = cls(
            request=request,
            messages=messages,
            assistant_message=assistant_message,
            tool_uses=tool_uses,
            completed_tool_results=completed_tool_results,
            next_tool_index=next_tool_index,
            controller=controller,
            continuation_fingerprint=_fingerprint(facts),
        )
        continuation.validate_integrity()
        return continuation

    @staticmethod
    def _fingerprint_facts(
        *,
        request: PermissionDeltaRequest,
        messages: tuple[ConversationMessage, ...],
        assistant_message: ConversationMessage,
        tool_uses: tuple[ToolUseBlock, ...],
        completed_tool_results: tuple[ToolResultBlock, ...],
        next_tool_index: int,
        controller: ParkedControllerState,
    ) -> dict[str, Any]:
        return {
            "request": request.model_dump(mode="json"),
            "messages": [message.model_dump(mode="json") for message in messages],
            "assistant_message": assistant_message.model_dump(mode="json"),
            "tool_uses": [tool.model_dump(mode="json") for tool in tool_uses],
            "completed_tool_results": [
                result.model_dump(mode="json") for result in completed_tool_results
            ],
            "next_tool_index": next_tool_index,
            "controller": controller.model_dump(mode="json"),
        }

    @property
    def current_tool_use(self) -> ToolUseBlock:
        try:
            return self.tool_uses[self.next_tool_index]
        except IndexError as exc:
            raise ValueError("continuation dispatch position is out of range") from exc

    @property
    def remaining_tool_uses(self) -> tuple[ToolUseBlock, ...]:
        return self.tool_uses[self.next_tool_index :]

    def validate_integrity(self) -> None:
        self.request.validate_integrity()
        if self.assistant_message.role != "assistant":
            raise ValueError("continuation assistant message has invalid role")
        assistant_tool_uses = tuple(
            block for block in self.assistant_message.content if isinstance(block, ToolUseBlock)
        )
        current = self.current_tool_use
        completed_ids = tuple(result.tool_use_id for result in self.completed_tool_results)
        expected_completed_ids = tuple(tool.id for tool in self.tool_uses[: self.next_tool_index])
        facts = self._fingerprint_facts(
            request=self.request,
            messages=self.messages,
            assistant_message=self.assistant_message,
            tool_uses=self.tool_uses,
            completed_tool_results=self.completed_tool_results,
            next_tool_index=self.next_tool_index,
            controller=self.controller,
        )
        assistant_tool_identities = tuple((tool.id, tool.name) for tool in assistant_tool_uses)
        dispatch_tool_identities = tuple((tool.id, tool.name) for tool in self.tool_uses)
        if (
            assistant_tool_identities != dispatch_tool_identities
            or completed_ids != expected_completed_ids
            or current.id != self.request.tool_use_id
            or current.name != self.request.tool_name
            or current.input != self.request.final_arguments
            or self.continuation_fingerprint != _fingerprint(facts)
        ):
            raise ValueError("continuation integrity failure")

    def validate_for(self, runtime: PermissionRuntime) -> None:
        runtime.validate_request(self.request)
        self.validate_integrity()


class PermissionResumeTransition(_FrozenModel):
    request_id: str
    request_fingerprint: str
    grant_fingerprint: str
    decision: PermissionReviewDecision
    continuation: ParkedContinuation | None = None


class PermissionDenialRecord(_FrozenModel):
    request: PermissionDeltaRequest
    count: int = Field(ge=1)


class PermissionRuntimeState(_FrozenModel):
    schema_version: Literal[2] = 2
    profile_fingerprint: str
    parked_request: PermissionDeltaRequest | None = None
    parked_reason: str | None = None
    parked_review_status: PermissionParkedReviewStatus | None = None
    parked_continuation: ParkedContinuation | None = None
    grants: tuple[PermissionDeltaRequest, ...] = ()
    denials: tuple[PermissionDenialRecord, ...] = ()
    request_id_aliases: dict[str, str] = Field(default_factory=dict)
    last_human_decision: PermissionReviewDecision | None = None
    last_decided_request: PermissionDeltaRequest | None = None
    last_decision_resumed: bool = False
    # Migration-only v1 facts. They are checked by ``from_state`` but never
    # emitted by the v2 writer.
    legacy_boundary_fingerprint: str | None = Field(default=None, exclude=True)
    legacy_backend_fingerprint: str | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def migrate_local_v1(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("schema_version") == 2:
            return value
        if "boundary_fingerprint" not in value or "backend_fingerprint" not in value:
            return value

        migrated = dict(value)
        request_by_legacy_fingerprint: dict[str, PermissionDeltaRequest] = {}
        request_id_aliases: dict[str, str] = {}

        def migrate_request(raw: Any) -> Any:
            if raw is None or isinstance(raw, PermissionDeltaRequest):
                return raw
            if not isinstance(raw, dict):
                return raw
            legacy_fingerprint = raw.get("request_fingerprint")
            legacy_request_id = raw.get("request_id")
            request = PermissionDeltaRequest.model_validate(raw)
            if isinstance(legacy_fingerprint, str):
                request_by_legacy_fingerprint[legacy_fingerprint] = request
            if isinstance(legacy_request_id, str) and legacy_request_id != request.request_id:
                request_id_aliases[legacy_request_id] = request.request_id
            return request.model_dump(mode="json")

        migrated["parked_request"] = migrate_request(migrated.get("parked_request"))
        migrated["last_decided_request"] = migrate_request(migrated.get("last_decided_request"))

        migrated_grants: list[dict[str, Any]] = []
        for fingerprint in migrated.get("grants", ()):
            request = request_by_legacy_fingerprint.get(fingerprint)
            if request is not None:
                migrated_grants.append(request.model_dump(mode="json"))

        migrated_denials: list[dict[str, Any]] = []
        raw_denials = migrated.get("denials", {})
        if isinstance(raw_denials, dict):
            for fingerprint, count in raw_denials.items():
                request = request_by_legacy_fingerprint.get(fingerprint)
                if request is not None and isinstance(count, int) and count > 0:
                    migrated_denials.append(
                        {
                            "request": request.model_dump(mode="json"),
                            "count": count,
                        }
                    )

        migrated.update(
            {
                "schema_version": 2,
                "legacy_boundary_fingerprint": migrated.pop("boundary_fingerprint"),
                "legacy_backend_fingerprint": migrated.pop("backend_fingerprint"),
                "grants": migrated_grants,
                "denials": migrated_denials,
                "request_id_aliases": request_id_aliases,
            }
        )
        return migrated


class PermissionRuntime:
    """Session-scoped exact authorization ledger.

    A local request requires a verified boundary. External requests bind to
    active policy evidence and deliberately work without a local sandbox.
    """

    def __init__(
        self,
        *,
        profile: RuntimePermissionProfile,
        boundary: EnforcedBoundary | None,
        reviewer: PermissionReviewer | None = None,
        denial_limit: int = 2,
    ) -> None:
        if boundary is not None:
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
        self.parked_review_status: PermissionParkedReviewStatus | None = None
        self.parked_continuation: ParkedContinuation | None = None
        self.last_human_decision: PermissionReviewDecision | None = None
        self.last_decided_request: PermissionDeltaRequest | None = None
        self._last_decision_resumed = False
        self._grants: dict[str, PermissionDeltaRequest] = {}
        self._denials: dict[str, PermissionDenialRecord] = {}
        self._request_id_aliases: dict[str, str] = {}

    def require_local_boundary(self) -> EnforcedBoundary:
        boundary = self.boundary
        if boundary is None:
            raise ValueError("local permission request requires a verified local boundary")
        return boundary

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
        if (
            isinstance(request.enforcement, ExternalPolicyEvidence)
            and request.enforcement.policy_mode == "deny"
        ):
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason="external surface policy denies this request",
                request=request,
            )
        if request.delta.hard_deny or request.crossing.hard_deny:
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason="hard deny cannot be reviewed",
                request=request,
            )
        denial = self._denials.get(request.request_fingerprint)
        if denial is not None and denial.count >= self.denial_limit:
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason="denial circuit open",
                request=request,
            )
        if self.reviewer is None:
            self.park(
                request,
                reason="no automatic reviewer is available",
                review_status=PermissionParkedReviewStatus.MANUAL,
            )
            return PermissionResolution(
                status=PermissionResolutionStatus.PARKED,
                reason=self.parked_reason or "parked",
                request=request,
            )
        try:
            verdict = await self.reviewer.review(request)
        except Exception as exc:
            self.park(
                request,
                reason=f"reviewer failed: {exc}",
                review_status=PermissionParkedReviewStatus.FAILED,
            )
            return PermissionResolution(
                status=PermissionResolutionStatus.PARKED,
                reason=self.parked_reason or "parked",
                request=request,
            )
        if verdict.decision is PermissionReviewDecision.APPROVE:
            self._grants[request.grant_fingerprint] = request
            return PermissionResolution(
                status=PermissionResolutionStatus.RETRY_ONCE,
                reason=verdict.reason,
                request=request,
            )
        if verdict.decision is PermissionReviewDecision.DENY:
            count = denial.count + 1 if denial is not None else 1
            self._denials[request.request_fingerprint] = PermissionDenialRecord(
                request=request,
                count=count,
            )
            return PermissionResolution(
                status=PermissionResolutionStatus.DENIED,
                reason=verdict.reason,
                request=request,
            )
        self.park(
            request,
            reason=verdict.reason,
            review_status=(
                PermissionParkedReviewStatus.FAILED
                if verdict.decision is PermissionReviewDecision.FAILED
                else PermissionParkedReviewStatus.DEFERRED
            ),
        )
        return PermissionResolution(
            status=PermissionResolutionStatus.PARKED,
            reason=verdict.reason,
            request=request,
        )

    def consume_grant(self, request: PermissionDeltaRequest) -> bool:
        self._validate_request(request)
        grant = self._grants.get(request.grant_fingerprint)
        if grant is None:
            return False
        self._validate_request(grant)
        del self._grants[request.grant_fingerprint]
        return True

    def park(
        self,
        request: PermissionDeltaRequest,
        *,
        reason: str,
        review_status: PermissionParkedReviewStatus = PermissionParkedReviewStatus.MANUAL,
    ) -> None:
        self._validate_request(request)
        self.parked_request = request
        self.parked_reason = reason
        self.parked_review_status = review_status
        self.parked_continuation = None
        self.last_human_decision = None
        self.last_decided_request = None
        self._last_decision_resumed = False

    def approve_parked(self, request_id: str) -> None:
        request = self._require_parked(request_id)
        self._grants[request.grant_fingerprint] = request
        self.parked_request = None
        self.parked_reason = None
        self.parked_review_status = None
        self.last_human_decision = PermissionReviewDecision.APPROVE
        self.last_decided_request = request
        self._last_decision_resumed = False

    def deny_parked(self, request_id: str, *, reason: str) -> None:
        request = self._require_parked(request_id)
        self._denials[request.request_fingerprint] = PermissionDenialRecord(
            request=request,
            count=self.denial_limit,
        )
        self.parked_request = None
        self.parked_reason = reason
        self.parked_review_status = None
        self.last_human_decision = PermissionReviewDecision.DENY
        self.last_decided_request = request
        self._last_decision_resumed = False

    def clear_pending_state(self) -> None:
        """Drop conversation-bound permission UI state, preserving the ledger.

        ``/clear`` starts a fresh conversation, so an exact request parked by
        the old transcript and an unconsumed approve/deny transition must not
        reappear on resume. One-shot grants and denial records remain part of
        the session authorization ledger and retain their existing semantics.
        """
        # An approved-but-unconsumed continuation is conversation-bound. If
        # /clear dropped only its UI transition, the exact grant could later
        # authorize a newly generated look-alike call in the fresh
        # conversation. Revoke that one-shot grant before forgetting the
        # transition. Denial records remain narrowing facts and are safe to
        # preserve.
        if (
            self.last_human_decision is PermissionReviewDecision.APPROVE
            and self.last_decided_request is not None
            and self.parked_continuation is not None
        ):
            self._grants.pop(self.last_decided_request.grant_fingerprint, None)
        self.parked_request = None
        self.parked_reason = None
        self.parked_review_status = None
        self.parked_continuation = None
        self.last_human_decision = None
        self.last_decided_request = None
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
            continuation=self.parked_continuation,
        )

    @property
    def decision_ready_for_continuation(self) -> bool:
        return (
            self.last_human_decision is not None
            and self.last_decided_request is not None
            and self._last_decision_resumed
        )

    def bind_continuation(self, continuation: ParkedContinuation) -> None:
        request = self.parked_request
        if request is None or request != continuation.request:
            raise ValueError("continuation does not match parked permission request")
        continuation.validate_for(self)
        self.parked_continuation = continuation

    def consume_continuation(self, continuation: ParkedContinuation) -> None:
        if self.parked_continuation != continuation:
            raise ValueError("continuation is not the active permission continuation")
        continuation.validate_for(self)
        self.parked_continuation = None
        self.last_human_decision = None
        self.last_decided_request = None
        self._last_decision_resumed = False

    def validate_request(self, request: PermissionDeltaRequest) -> None:
        """Public fail-closed validation used by continuation orchestration."""
        self._validate_request(request)

    def _require_parked(self, request_id: str) -> PermissionDeltaRequest:
        request = self.parked_request
        if request is None or len(request_id) < 8:
            raise ValueError("no matching parked permission request")
        if request.request_id.startswith(request_id):
            return request
        alias_targets = (
            target
            for alias, target in self._request_id_aliases.items()
            if alias.startswith(request_id)
        )
        if request.request_id not in alias_targets:
            raise ValueError("no matching parked permission request")
        return request

    def _validate_request(self, request: PermissionDeltaRequest) -> None:
        if request.profile_fingerprint != self.profile.fingerprint:
            raise ValueError("permission request profile drift")
        if request.profile_facts != self.profile.normalized():
            raise ValueError("permission request profile facts drift")
        evidence = request.enforcement
        if isinstance(evidence, LocalBoundaryEvidence):
            boundary = self.require_local_boundary()
            if evidence.backend_fingerprint != boundary.backend_fingerprint:
                raise ValueError("permission request backend drift")
            if evidence.boundary_fingerprint != boundary.fingerprint:
                raise ValueError("permission request boundary drift")
            if evidence.boundary_facts != boundary.normalized():
                raise ValueError("permission request boundary facts drift")
            expected_operation = _operation_fingerprint(request.tool_name, request.final_arguments)
            if evidence.operation_fingerprint != expected_operation:
                raise ValueError("permission request operation drift")
        else:
            policy_facts = self.profile.external_tools.model_dump(mode="json")
            if evidence.policy_facts != policy_facts:
                raise ValueError("permission request external policy drift")
            if evidence.policy_fingerprint != _fingerprint(policy_facts):
                raise ValueError("permission request external policy fingerprint drift")
            if evidence.policy_mode != policy_facts.get(evidence.surface):
                raise ValueError("permission request external surface policy drift")
            if evidence.tool_identity != request.tool_name:
                raise ValueError("permission request external tool identity drift")
            if (
                request.delta.kind is not PermissionDeltaKind.EXTERNAL_TOOL
                or request.delta.value != evidence.surface
            ):
                raise ValueError("permission request external surface drift")
        request.validate_integrity()

    def export_state(self) -> PermissionRuntimeState:
        return PermissionRuntimeState(
            profile_fingerprint=self.profile.fingerprint,
            parked_request=self.parked_request,
            parked_reason=self.parked_reason,
            parked_review_status=self.parked_review_status,
            parked_continuation=self.parked_continuation,
            grants=tuple(self._grants[key] for key in sorted(self._grants)),
            denials=tuple(self._denials[key] for key in sorted(self._denials)),
            request_id_aliases=dict(sorted(self._request_id_aliases.items())),
            last_human_decision=self.last_human_decision,
            last_decided_request=self.last_decided_request,
            last_decision_resumed=self._last_decision_resumed,
        )

    @classmethod
    def from_state(
        cls,
        *,
        profile: RuntimePermissionProfile,
        boundary: EnforcedBoundary | None,
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
        if parsed.legacy_boundary_fingerprint is not None:
            if boundary is None:
                raise ValueError("legacy permission state requires a local boundary")
            if parsed.legacy_boundary_fingerprint != boundary.fingerprint:
                raise ValueError("permission boundary drift while resuming")
            if parsed.legacy_backend_fingerprint != boundary.backend_fingerprint:
                raise ValueError("permission backend drift while resuming")
        runtime = cls(
            profile=profile,
            boundary=boundary,
            reviewer=reviewer,
            denial_limit=denial_limit,
        )
        for request in (
            parsed.parked_request,
            parsed.last_decided_request,
            parsed.parked_continuation.request if parsed.parked_continuation is not None else None,
            *parsed.grants,
            *(record.request for record in parsed.denials),
        ):
            if request is not None:
                runtime._validate_request(request)
        known_request_ids = {
            request.request_id
            for request in (
                parsed.parked_request,
                parsed.last_decided_request,
                *parsed.grants,
                *(record.request for record in parsed.denials),
            )
            if request is not None
        }
        for alias, target in parsed.request_id_aliases.items():
            if len(alias) < 8 or target not in known_request_ids:
                raise ValueError("permission request id alias integrity failure")
        runtime.parked_request = parsed.parked_request
        runtime.parked_reason = parsed.parked_reason
        runtime.parked_review_status = parsed.parked_review_status
        runtime.parked_continuation = parsed.parked_continuation
        runtime.last_human_decision = parsed.last_human_decision
        runtime.last_decided_request = parsed.last_decided_request
        runtime._last_decision_resumed = parsed.last_decision_resumed
        runtime._grants = {request.grant_fingerprint: request for request in parsed.grants}
        runtime._denials = {record.request.request_fingerprint: record for record in parsed.denials}
        runtime._request_id_aliases = dict(parsed.request_id_aliases)
        if runtime.parked_continuation is not None:
            runtime.parked_continuation.validate_for(runtime)
        return runtime
