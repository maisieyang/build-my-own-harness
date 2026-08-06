"""S3 contracts for the native macOS Seatbelt backend."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import pytest

from openharness.execution import (
    BoundaryViolation,
    CommandOperation,
    ExecutionEffect,
    FileReadOperation,
    FileWriteOperation,
    OperationCompleted,
    ProcessCompleted,
)
from openharness.execution.seatbelt import SeatbeltBackend, compile_seatbelt_profile
from openharness.permissions import (
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    NetworkPolicy,
    RuntimePermissionProfile,
)

if TYPE_CHECKING:
    from pathlib import Path


requires_macos_seatbelt = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires the real macOS /usr/bin/sandbox-exec runtime",
)


def _workspace_profile() -> RuntimePermissionProfile:
    return RuntimePermissionProfile(
        name="workspace",
        filesystem=FilesystemPolicy(
            rules=(
                FilesystemRule(path=".", access=FilesystemAccess.WRITE),
                FilesystemRule(path=".git", access=FilesystemAccess.DENY),
            )
        ),
    )


def test_compiler_allows_workspace_write_but_denies_nested_protected_path(
    tmp_path: Path,
) -> None:
    profile_text = compile_seatbelt_profile(_workspace_profile(), cwd=tmp_path)

    assert "(deny file-write*" in profile_text
    assert f'(allow file-write* (subpath "{tmp_path}"))' in profile_text
    assert f'(deny file-read* file-write* (subpath "{tmp_path / ".git"}"))' in profile_text
    assert "(deny network*)" in profile_text


def test_compiler_denies_ambient_process_authority(tmp_path: Path) -> None:
    profile_text = compile_seatbelt_profile(_workspace_profile(), cwd=tmp_path)

    assert "(deny default)" in profile_text
    assert "(allow signal (target same-sandbox))" in profile_text
    assert "(allow process-info* (target same-sandbox))" in profile_text


def test_compiler_escapes_paths_as_seatbelt_strings(tmp_path: Path) -> None:
    odd = tmp_path / 'quote"slash\\x'
    profile = RuntimePermissionProfile(
        name="odd",
        filesystem=FilesystemPolicy(
            rules=(FilesystemRule(path=str(odd), access=FilesystemAccess.WRITE),)
        ),
    )

    text = compile_seatbelt_profile(profile, cwd=tmp_path)

    assert '\\"' in text
    assert "\\\\" in text


def test_preflight_accepts_domain_policy_via_managed_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = RuntimePermissionProfile(
        name="networked",
        network=NetworkPolicy(enabled=True, allow_domains=("pypi.org",)),
    )
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    monkeypatch.setattr("openharness.execution.seatbelt.sys.platform", "darwin")
    monkeypatch.setattr("openharness.execution.seatbelt.os.path.isfile", lambda _: True)
    monkeypatch.setattr("openharness.execution.seatbelt.os.access", lambda *_: True)

    support = backend.preflight(profile)

    assert support.supported is True
    assert "network.domain_allowlist" not in support.unsupported_features


@requires_macos_seatbelt
async def test_session_executes_through_sandbox_exec_and_reports_command_coverage(
    tmp_path: Path,
) -> None:
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())

    result = await session.execute(CommandOperation(command="printf ok", cwd=tmp_path))

    assert result == ProcessCompleted(output="ok", exit_code=0)
    assert session.boundary.covers(ExecutionEffect.COMMAND)
    assert session.boundary.is_verified is True
    await session.close()


async def test_open_fails_closed_when_executable_is_missing(tmp_path: Path) -> None:
    from openharness.execution import SandboxUnavailableError

    backend = SeatbeltBackend(cwd=tmp_path, executable=str(tmp_path / "missing"))

    with pytest.raises(SandboxUnavailableError, match="sandbox-exec"):
        await backend.open(_workspace_profile())


@requires_macos_seatbelt
async def test_file_worker_obeys_same_workspace_boundary(tmp_path: Path) -> None:
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    target = tmp_path / "worker.txt"

    written = await session.execute(FileWriteOperation(path=target, content="hello"))
    read = await session.execute(FileReadOperation(path=target))

    assert isinstance(written, OperationCompleted)
    assert written.is_error is False
    assert isinstance(read, OperationCompleted)
    assert read.output == "hello"
    assert session.boundary.covered_effects == (
        ExecutionEffect.COMMAND,
        ExecutionEffect.FILE_READ,
        ExecutionEffect.FILE_WRITE,
        ExecutionEffect.FILE_SEARCH,
    )
    await session.close()


@requires_macos_seatbelt
async def test_file_worker_cannot_write_outside_workspace(tmp_path: Path) -> None:
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.unlink(missing_ok=True)

    result = await session.execute(FileWriteOperation(path=outside, content="escape"))

    assert isinstance(result, BoundaryViolation)
    assert result.dimension == "filesystem.write"
    assert result.requested == str(outside)
    assert not outside.exists()
    await session.close()


@requires_macos_seatbelt
async def test_command_and_file_worker_cannot_read_outside_declared_roots(tmp_path: Path) -> None:
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    outside = tmp_path.parent / f"{tmp_path.name}-outside-secret.txt"
    payload = "TOP_SECRET_PAYLOAD"
    outside.write_text(payload, encoding="utf-8")
    try:
        command_result = await session.execute(
            CommandOperation(command=f'/bin/cat "{outside}"', cwd=tmp_path)
        )
        file_result = await session.execute(FileReadOperation(path=outside))

        assert isinstance(command_result, ProcessCompleted)
        assert command_result.exit_code != 0
        assert isinstance(file_result, BoundaryViolation)
        assert file_result.dimension == "filesystem.read"
        assert payload not in command_result.output
        assert payload not in file_result.evidence
    finally:
        outside.unlink(missing_ok=True)
        await session.close()


@requires_macos_seatbelt
async def test_command_cannot_signal_a_host_process_outside_its_sandbox(
    tmp_path: Path,
) -> None:
    target = await asyncio.create_subprocess_exec("/bin/sleep", "60")
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    try:
        result = await session.execute(
            CommandOperation(command=f"/bin/kill -TERM {target.pid}", cwd=tmp_path)
        )

        assert isinstance(result, ProcessCompleted)
        assert result.exit_code != 0
        assert target.returncode is None
    finally:
        await session.close()
        if target.returncode is None:
            target.terminate()
        await target.wait()


@requires_macos_seatbelt
async def test_command_cannot_read_private_etc_outside_profile(tmp_path: Path) -> None:
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    try:
        result = await session.execute(
            CommandOperation(command="/bin/cat /private/etc/hosts", cwd=tmp_path)
        )

        assert isinstance(result, ProcessCompleted)
        assert result.exit_code != 0
    finally:
        await session.close()
