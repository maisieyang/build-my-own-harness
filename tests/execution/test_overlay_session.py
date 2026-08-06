from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openharness.execution import (
    BackendSupport,
    BoundaryVerification,
    BoundaryViolation,
    CommandOperation,
    EnforcedBoundary,
    ExecutionEffect,
    OneShotOverlaySession,
    OperationCompleted,
)
from openharness.permissions import (
    FilesystemAccess,
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionFilesystemAccess,
    RuntimePermissionProfile,
)


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


@dataclass
class _ViolatingSession(_Session):
    async def execute(self, operation: object) -> BoundaryViolation:
        del operation
        self.calls += 1
        return BoundaryViolation(
            dimension="network.domain",
            requested="example.com:443",
            evidence="not in allowlist",
        )


def _request(
    profile: RuntimePermissionProfile,
    boundary: EnforcedBoundary,
    *,
    delta: PermissionDelta | None = None,
    crossing: BoundaryViolation | None = None,
) -> PermissionDeltaRequest:
    crossing = crossing or BoundaryViolation(
        dimension="network.domain",
        requested="example.com:443",
        evidence="not in allowlist",
    )
    return PermissionDeltaRequest.create(
        tool_use_id="tool-1",
        tool_name="Bash",
        final_arguments={"command": "curl https://example.com"},
        profile=profile,
        boundary=boundary,
        delta=delta or PermissionDelta.network_domain("example.com"),
        crossing=crossing,
        data_sources=("sandbox-visible data",),
        data_destinations=("example.com:443",),
    )


@pytest.mark.asyncio
async def test_network_delta_opens_verified_overlay_for_exactly_one_execution() -> None:
    profile = RuntimePermissionProfile(name="base")
    backend = _Backend()
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    assert isinstance(await session.execute(operation), BoundaryViolation)
    session.arm(_request(profile, base.boundary))

    first = await session.execute(operation)
    second = await session.execute(CommandOperation(command="true", cwd=Path("/tmp")))

    assert backend.opened[0].profile.network.enabled is True
    assert backend.opened[0].profile.network.allow_domains == ("example.com",)
    assert backend.opened[0].closed is True
    assert first.output == backend.opened[0].profile.fingerprint
    assert isinstance(second, BoundaryViolation)
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
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=_BadBackend(), profile=profile, base=base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(operation)
    session.arm(_request(profile, base.boundary))

    with pytest.raises(RuntimeError, match="overlay boundary verification failed"):
        await session.execute(operation)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["backend", "coverage"])
async def test_overlay_fails_closed_when_verified_facts_do_not_cover_operation(
    failure: str,
) -> None:
    class _IncompleteSession(_Session):
        @property
        def boundary(self) -> EnforcedBoundary:
            return EnforcedBoundary(
                profile_fingerprint=self.profile.fingerprint,
                backend="other" if failure == "backend" else "fake",
                backend_version="1",
                covered_effects=() if failure == "coverage" else (ExecutionEffect.COMMAND,),
                verification=BoundaryVerification.VERIFIED,
            )

    class _IncompleteBackend(_Backend):
        async def open(self, profile: RuntimePermissionProfile) -> _Session:
            opened = _IncompleteSession(profile)
            self.opened.append(opened)
            return opened

    profile = RuntimePermissionProfile(name="base")
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=_IncompleteBackend(), profile=profile, base=base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(operation)
    session.arm(_request(profile, base.boundary))

    with pytest.raises(RuntimeError, match="overlay boundary verification failed"):
        await session.execute(operation)


@pytest.mark.asyncio
async def test_filesystem_overlay_is_exact_and_session_close_owns_base() -> None:
    profile = RuntimePermissionProfile(name="base")
    backend = _Backend()
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(operation)
    request = _request(
        profile,
        base.boundary,
        delta=PermissionDelta.filesystem_path("/tmp/generated.txt"),
    )
    session.arm(request)

    await session.execute(operation)
    await session.close()

    assert backend.opened[0].profile.filesystem.rules[0].path == "/tmp/generated.txt"
    assert base.closed is True


@pytest.mark.asyncio
async def test_filesystem_read_overlay_does_not_expand_to_write() -> None:
    profile = RuntimePermissionProfile(name="base")
    backend = _Backend()
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(operation)
    request = _request(
        profile,
        base.boundary,
        delta=PermissionDelta.filesystem_path(
            "/tmp/input.txt", access=PermissionFilesystemAccess.READ
        ),
    )
    session.arm(request)

    await session.execute(operation)

    assert backend.opened[0].profile.filesystem.rules[0].access is FilesystemAccess.READ


@pytest.mark.asyncio
async def test_overlay_rejects_double_arm_and_unsupported_preflight() -> None:
    class _Unsupported(_Backend):
        def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
            del profile
            return BackendSupport.unsupported(
                backend="fake", features=("network",), reason="network unsupported"
            )

    profile = RuntimePermissionProfile(name="base")
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=_Unsupported(), profile=profile, base=base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(operation)
    request = _request(profile, base.boundary)
    session.arm(request)
    with pytest.raises(RuntimeError, match="already armed"):
        session.arm(request)
    with pytest.raises(RuntimeError, match="network unsupported"):
        await session.execute(operation)


@pytest.mark.asyncio
async def test_local_overlay_rejects_external_tool_delta() -> None:
    profile = RuntimePermissionProfile(name="base")
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(
        backend=_Backend(),
        profile=profile,
        base=base,
    )
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(operation)
    request = _request(
        profile,
        base.boundary,
        delta=PermissionDelta.external_tool("web"),
    )
    session.arm(request)

    with pytest.raises(ValueError, match="cannot be installed by a local sandbox"):
        await session.execute(operation)


@pytest.mark.asyncio
async def test_overlay_rejects_parameter_change_and_consumes_approval() -> None:
    profile = RuntimePermissionProfile(name="base")
    backend = _Backend()
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    original = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(original)
    session.arm(_request(profile, base.boundary))

    changed = CommandOperation(command="curl https://other.example", cwd=Path("/tmp"))
    with pytest.raises(RuntimeError, match="exact operation"):
        await session.execute(changed)

    assert backend.opened == []
    assert isinstance(await session.execute(original), BoundaryViolation)


@pytest.mark.asyncio
async def test_overlay_arm_rejects_missing_or_mismatched_violation() -> None:
    profile = RuntimePermissionProfile(name="base")
    completed_base = _Session(profile)
    completed = OneShotOverlaySession(backend=_Backend(), profile=profile, base=completed_base)
    with pytest.raises(RuntimeError, match="observed boundary violation"):
        completed.arm(_request(profile, completed_base.boundary))

    violating_base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=_Backend(), profile=profile, base=violating_base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))
    await session.execute(operation)
    mismatch = BoundaryViolation(
        dimension="network.domain",
        requested="other.example:443",
        evidence="not in allowlist",
    )
    with pytest.raises(RuntimeError, match="observed boundary violation"):
        session.arm(_request(profile, violating_base.boundary, crossing=mismatch))


@pytest.mark.asyncio
async def test_overlay_arm_rejects_hard_deny_and_all_runtime_drift() -> None:
    profile = RuntimePermissionProfile(name="base")
    base = _ViolatingSession(profile)
    session = OneShotOverlaySession(backend=_Backend(), profile=profile, base=base)
    operation = CommandOperation(command="curl https://example.com", cwd=Path("/tmp"))

    await session.execute(operation)
    hard = PermissionDelta.network_domain("example.com").model_copy(update={"hard_deny": True})
    with pytest.raises(RuntimeError, match="hard-denied"):
        session.arm(_request(profile, base.boundary, delta=hard))

    changed_profile = RuntimePermissionProfile(name="changed")
    profile_boundary = EnforcedBoundary(
        profile_fingerprint=changed_profile.fingerprint,
        backend="fake",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    with pytest.raises(RuntimeError, match="profile fingerprint drift"):
        session.arm(_request(changed_profile, profile_boundary))

    backend_boundary = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="other",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    with pytest.raises(RuntimeError, match="backend fingerprint drift"):
        session.arm(_request(profile, backend_boundary))

    facts_boundary = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="fake",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND, ExecutionEffect.FILE_READ),
        verification=BoundaryVerification.VERIFIED,
    )
    with pytest.raises(RuntimeError, match="boundary fingerprint drift"):
        session.arm(_request(profile, facts_boundary))
