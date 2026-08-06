"""Verified, minimal one-shot overlays over a stable base sandbox session."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from pathlib import Path

from openharness.execution.boundary import (
    BoundaryViolation,
    DataPlaneOperation,
    EnforcedBoundary,
    ExecutionResult,
    SandboxBackend,
    SandboxSession,
    SandboxUnavailableError,
)
from openharness.permissions.profile import (
    FilesystemAccess,
    FilesystemRule,
    NetworkPolicy,
    RuntimePermissionProfile,
)
from openharness.permissions.runtime import (
    PermissionDelta,
    PermissionDeltaKind,
    PermissionDeltaRequest,
    PermissionFilesystemAccess,
)


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    return value


def _operation_fingerprint(operation: DataPlaneOperation) -> str:
    payload = {
        "operation_type": type(operation).__qualname__,
        "operation": _jsonable(operation),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _overlay_profile(
    profile: RuntimePermissionProfile, delta: PermissionDelta
) -> RuntimePermissionProfile:
    if delta.kind is PermissionDeltaKind.NETWORK_DOMAIN:
        network = profile.network
        updated_network = NetworkPolicy(
            enabled=True,
            allow_domains=tuple(sorted({*network.allow_domains, delta.value})),
            deny_domains=network.deny_domains,
            allow_loopback=network.allow_loopback,
            allow_private=network.allow_private,
            allow_link_local=network.allow_link_local,
            allow_unix_sockets=network.allow_unix_sockets,
        )
        return profile.model_copy(update={"network": updated_network})
    if delta.kind is PermissionDeltaKind.FILESYSTEM_PATH:
        access = (
            FilesystemAccess.READ
            if delta.filesystem_access
            in {PermissionFilesystemAccess.READ, PermissionFilesystemAccess.SEARCH}
            else FilesystemAccess.WRITE
        )
        rules = (
            *profile.filesystem.rules,
            FilesystemRule(path=delta.value, access=access),
        )
        filesystem = profile.filesystem.model_copy(update={"rules": rules})
        return profile.model_copy(update={"filesystem": filesystem})
    raise ValueError(f"delta {delta.kind.value} cannot be installed by a local sandbox")


class OneShotOverlaySession:
    """Use the base boundary normally; compile an approved delta for one call."""

    def __init__(
        self,
        *,
        backend: SandboxBackend,
        profile: RuntimePermissionProfile,
        base: SandboxSession,
    ) -> None:
        self._backend = backend
        self._profile = profile
        self._base = base
        self._armed: tuple[PermissionDeltaRequest, str] | None = None
        self._last_violation: tuple[str, BoundaryViolation] | None = None

    @property
    def boundary(self) -> EnforcedBoundary:
        return self._base.boundary

    def arm(self, request: PermissionDeltaRequest) -> None:
        if self._armed is not None:
            raise RuntimeError("a one-shot permission overlay is already armed")
        request.validate_integrity()
        if request.delta.hard_deny:
            raise RuntimeError("a hard-denied permission delta cannot be armed")
        observed = self._last_violation
        if observed is None or observed[1] != request.crossing:
            raise RuntimeError("permission approval does not match the observed boundary violation")
        if request.profile_fingerprint != self._profile.fingerprint:
            raise RuntimeError("permission approval profile fingerprint drift")
        if request.backend_fingerprint != self.boundary.backend_fingerprint:
            raise RuntimeError("permission approval backend fingerprint drift")
        if request.boundary_fingerprint != self.boundary.fingerprint:
            raise RuntimeError("permission approval boundary fingerprint drift")
        self._armed = (request, observed[0])
        self._last_violation = None

    async def execute(self, operation: DataPlaneOperation) -> ExecutionResult:
        armed = self._armed
        if armed is None:
            result = await self._base.execute(operation)
            self._last_violation = (
                (_operation_fingerprint(operation), result)
                if isinstance(result, BoundaryViolation)
                else None
            )
            return result
        self._armed = None
        request, expected_operation = armed
        if _operation_fingerprint(operation) != expected_operation:
            raise RuntimeError("one-shot permission approval is not for this exact operation")
        delta = request.delta
        overlay_profile = _overlay_profile(self._profile, delta)
        support = self._backend.preflight(overlay_profile)
        if not support.supported:
            raise SandboxUnavailableError(
                support.reason or "backend cannot compile the approved permission delta"
            )
        overlay = await self._backend.open(overlay_profile)
        try:
            if (
                not overlay.boundary.is_verified
                or overlay.boundary.profile_fingerprint != overlay_profile.fingerprint
                or overlay.boundary.backend_fingerprint != self.boundary.backend_fingerprint
                or not overlay.boundary.covers(operation.required_effect)
            ):
                raise RuntimeError("overlay boundary verification failed")
            return await overlay.execute(operation)
        finally:
            await overlay.close()

    async def close(self) -> None:
        await self._base.close()
