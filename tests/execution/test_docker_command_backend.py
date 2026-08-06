"""S2 contracts for the command-only Docker sandbox backend."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from openharness.execution import (
    BoundaryVerification,
    CommandOperation,
    DockerCommandBackend,
    ExecutionEffect,
    ExecutionFailed,
    FileReadOperation,
    ProcessCompleted,
    SandboxUnavailableError,
    TimedOut,
)
from openharness.execution.docker_backend import _bounded_timeout
from openharness.permissions import (
    EnvironmentInheritance,
    EnvironmentPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    NetworkPolicy,
    ProcessPolicy,
    RuntimePermissionProfile,
    workspace_runtime_profile,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_preflight_rejects_domain_filtered_network_policy(tmp_path: Path) -> None:
    backend = DockerCommandBackend(cwd=tmp_path)
    profile = RuntimePermissionProfile(
        name="networked",
        network=NetworkPolicy(enabled=True, allow_domains=("pypi.org",)),
    )

    support = backend.preflight(profile)

    assert support.supported is False
    assert "network.domain_allowlist" in support.unsupported_features


def test_preflight_rejects_unix_socket_policy(tmp_path: Path) -> None:
    profile = workspace_runtime_profile().model_copy(
        update={
            "network": NetworkPolicy(
                enabled=True,
                allow_loopback=True,
                allow_private=True,
                allow_link_local=True,
                allow_unix_sockets=("/tmp/service.sock",),
            )
        }
    )

    support = DockerCommandBackend(cwd=tmp_path).preflight(profile)

    assert "network.unix_socket_allowlist" in support.unsupported_features


def test_bounded_timeout_uses_the_stricter_available_limit() -> None:
    assert _bounded_timeout(None, 3) == 3
    assert _bounded_timeout(5, None) == 5
    assert _bounded_timeout(5, 3) == 3


@pytest.mark.parametrize(
    "profile",
    [
        RuntimePermissionProfile(name="no-filesystem-grant"),
        RuntimePermissionProfile(
            name="read-only",
            filesystem=FilesystemPolicy(
                rules=(FilesystemRule(path=".", access=FilesystemAccess.READ),)
            ),
        ),
        RuntimePermissionProfile(
            name="login-shell",
            filesystem=workspace_runtime_profile().filesystem,
            process=ProcessPolicy(login_shell=True),
        ),
    ],
)
def test_preflight_rejects_profiles_the_fixed_docker_boundary_cannot_enforce(
    tmp_path: Path,
    profile: RuntimePermissionProfile,
) -> None:
    support = DockerCommandBackend(cwd=tmp_path).preflight(profile)

    assert support.supported is False


async def test_open_reports_verified_command_only_coverage(tmp_path: Path) -> None:
    profile = workspace_runtime_profile().model_copy(
        update={
            "environment": EnvironmentPolicy(
                inherit=EnvironmentInheritance.NONE,
                set_values={"SAFE": "1"},
            ),
            "process": ProcessPolicy(
                timeout_seconds=3,
                memory_bytes=1024,
                cpu_count=2,
                pids_limit=12,
                run_as_uid=123,
                run_as_gid=456,
            ),
        }
    )
    sandbox = AsyncMock()
    sandbox.run_command.return_value.output = "ok\n"
    sandbox.run_command.return_value.exit_code = 0
    sandbox.run_command.return_value.timed_out = False

    with patch("openharness.execution.docker_backend.SandboxExecution") as sandbox_cls:
        sandbox_cls.return_value = sandbox
        sandbox.__aenter__.return_value = sandbox
        backend = DockerCommandBackend(cwd=tmp_path)
        session = await backend.open(profile)

    assert session.boundary.verification is BoundaryVerification.VERIFIED
    assert session.boundary.profile_fingerprint == profile.fingerprint
    assert session.boundary.covered_effects == (ExecutionEffect.COMMAND,)
    assert sandbox_cls.call_args.kwargs["environment"] == {"SAFE": "1"}
    assert sandbox_cls.call_args.kwargs["memory"] == "1024b"
    assert sandbox_cls.call_args.kwargs["cpus"] == 2
    assert sandbox_cls.call_args.kwargs["pids"] == 12
    assert sandbox_cls.call_args.kwargs["uid"] == 123
    assert sandbox_cls.call_args.kwargs["gid"] == 456

    result = await session.execute(CommandOperation(command="printf ok", cwd=tmp_path, timeout=600))
    assert result == ProcessCompleted(output="ok\n", exit_code=0)
    sandbox.run_command.assert_awaited_once_with("printf ok", cwd=tmp_path, timeout=3.0)
    rejected = await session.execute(FileReadOperation(path=tmp_path / "x"))
    assert isinstance(rejected, ExecutionFailed)

    sandbox.run_command.return_value.output = "partial"
    sandbox.run_command.return_value.timed_out = True
    timed_out = await session.execute(CommandOperation(command="sleep 10", cwd=tmp_path))
    assert timed_out == TimedOut(output="partial")
    await session.close()
    await session.close()
    sandbox.__aexit__.assert_awaited_once_with(None, None, None)


async def test_open_failure_is_typed_and_never_falls_back_to_host(tmp_path: Path) -> None:
    profile = workspace_runtime_profile()

    with patch("openharness.execution.docker_backend.SandboxExecution") as sandbox_cls:
        sandbox_cls.return_value.__aenter__ = AsyncMock(side_effect=OSError("daemon missing"))
        backend = DockerCommandBackend(cwd=tmp_path)

        with pytest.raises(SandboxUnavailableError, match="daemon missing"):
            await backend.open(profile)
