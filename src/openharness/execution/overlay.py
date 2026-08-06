"""Verified, minimal one-shot overlays over a stable base sandbox session."""

from __future__ import annotations

from openharness.execution.boundary import (
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
from openharness.permissions.runtime import PermissionDelta, PermissionDeltaKind


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
        rules = (
            *profile.filesystem.rules,
            FilesystemRule(path=delta.value, access=FilesystemAccess.WRITE),
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
        self._armed: PermissionDelta | None = None

    @property
    def boundary(self) -> EnforcedBoundary:
        return self._base.boundary

    def arm(self, delta: PermissionDelta) -> None:
        if self._armed is not None:
            raise RuntimeError("a one-shot permission overlay is already armed")
        self._armed = delta

    async def execute(self, operation: DataPlaneOperation) -> ExecutionResult:
        delta = self._armed
        if delta is None:
            return await self._base.execute(operation)
        self._armed = None
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
            ):
                raise RuntimeError("overlay boundary verification failed")
            return await overlay.execute(operation)
        finally:
            await overlay.close()

    async def close(self) -> None:
        await self._base.close()
