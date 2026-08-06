from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openharness.execution import (
    BackendSupport,
    BoundaryVerification,
    CommandOperation,
    EnforcedBoundary,
    ExecutionEffect,
    OneShotOverlaySession,
    OperationCompleted,
)
from openharness.permissions import PermissionDelta, RuntimePermissionProfile


@dataclass
class _Session:
    profile: RuntimePermissionProfile
    closed: bool = False
    calls: int = 0

    @property
    def boundary(self) -> EnforcedBoundary:
        return EnforcedBoundary(
            profile_fingerprint=self.profile.fingerprint,
            backend="fake",
            backend_version="1",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
        )

    async def execute(self, operation: object) -> OperationCompleted:
        del operation
        self.calls += 1
        return OperationCompleted(output=self.profile.fingerprint, metadata={})

    async def close(self) -> None:
        self.closed = True


@dataclass
class _Backend:
    opened: list[_Session] = field(default_factory=list)

    def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
        del profile
        return BackendSupport.available(backend="fake")

    async def open(self, profile: RuntimePermissionProfile) -> _Session:
        session = _Session(profile)
        self.opened.append(session)
        return session


@pytest.mark.asyncio
async def test_network_delta_opens_verified_overlay_for_exactly_one_execution() -> None:
    profile = RuntimePermissionProfile(name="base")
    backend = _Backend()
    base = _Session(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    session.arm(PermissionDelta.network_domain("example.com"))

    first = await session.execute(CommandOperation(command="true", cwd=Path("/tmp")))
    second = await session.execute(CommandOperation(command="true", cwd=Path("/tmp")))

    assert backend.opened[0].profile.network.enabled is True
    assert backend.opened[0].profile.network.allow_domains == ("example.com",)
    assert backend.opened[0].closed is True
    assert first.output == backend.opened[0].profile.fingerprint
    assert second.output == profile.fingerprint
    assert len(backend.opened) == 1


@pytest.mark.asyncio
async def test_overlay_fails_closed_when_backend_reports_wrong_fingerprint() -> None:
    class _BadSession(_Session):
        @property
        def boundary(self) -> EnforcedBoundary:
            return EnforcedBoundary(
                profile_fingerprint="wrong",
                backend="fake",
                backend_version="1",
                covered_effects=(ExecutionEffect.COMMAND,),
                verification=BoundaryVerification.VERIFIED,
            )

    class _BadBackend(_Backend):
        async def open(self, profile: RuntimePermissionProfile) -> _Session:
            session = _BadSession(profile)
            self.opened.append(session)
            return session

    profile = RuntimePermissionProfile(name="base")
    session = OneShotOverlaySession(backend=_BadBackend(), profile=profile, base=_Session(profile))
    session.arm(PermissionDelta.network_domain("example.com"))

    with pytest.raises(RuntimeError, match="overlay boundary verification failed"):
        await session.execute(CommandOperation(command="true", cwd=Path("/tmp")))


@pytest.mark.asyncio
async def test_filesystem_overlay_is_exact_and_session_close_owns_base() -> None:
    profile = RuntimePermissionProfile(name="base")
    backend = _Backend()
    base = _Session(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    session.arm(PermissionDelta.filesystem_path("/tmp/generated.txt"))

    await session.execute(CommandOperation(command="true", cwd=Path("/tmp")))
    await session.close()

    assert backend.opened[0].profile.filesystem.rules[0].path == "/tmp/generated.txt"
    assert base.closed is True


@pytest.mark.asyncio
async def test_overlay_rejects_double_arm_and_unsupported_preflight() -> None:
    class _Unsupported(_Backend):
        def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
            del profile
            return BackendSupport.unsupported(
                backend="fake", features=("network",), reason="network unsupported"
            )

    profile = RuntimePermissionProfile(name="base")
    session = OneShotOverlaySession(backend=_Unsupported(), profile=profile, base=_Session(profile))
    session.arm(PermissionDelta.network_domain("example.com"))
    with pytest.raises(RuntimeError, match="already armed"):
        session.arm(PermissionDelta.network_domain("other.example"))
    with pytest.raises(RuntimeError, match="network unsupported"):
        await session.execute(CommandOperation(command="true", cwd=Path("/tmp")))


@pytest.mark.asyncio
async def test_local_overlay_rejects_external_tool_delta() -> None:
    profile = RuntimePermissionProfile(name="base")
    session = OneShotOverlaySession(
        backend=_Backend(),
        profile=profile,
        base=_Session(profile),
    )
    session.arm(PermissionDelta.external_tool("web"))

    with pytest.raises(ValueError, match="cannot be installed by a local sandbox"):
        await session.execute(CommandOperation(command="true", cwd=Path("/tmp")))
