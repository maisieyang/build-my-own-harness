"""S3 contracts for the native macOS Seatbelt backend."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from openharness.execution import (
    BoundaryViolation,
    CommandOperation,
    ExecutionEffect,
    FileEditOperation,
    FileReadOperation,
    FileSearchOperation,
    FileWriteOperation,
    OperationCompleted,
    ProcessCompleted,
)
from openharness.execution.overlay import OneShotOverlaySession
from openharness.execution.seatbelt import SeatbeltBackend, compile_seatbelt_profile
from openharness.permissions import (
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    FilesystemScope,
    NetworkPolicy,
    RuntimePermissionProfile,
)
from openharness.permissions.runtime import PermissionDelta, PermissionDeltaRequest

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

    assert (
        f'(deny file-write* (require-all (require-not (subpath "{tmp_path}")) '
        '(require-not (literal "/dev/null"))))'
    ) in profile_text
    assert '(allow sysctl-read (sysctl-name "hw.pagesize_compat"))' in profile_text
    assert '(allow sysctl-read (sysctl-name "kern.ostype"))' in profile_text
    assert '(allow file-write* (literal "/dev/null"))' in profile_text
    assert '(allow file-write* (subpath "/dev"))' not in profile_text
    assert '(require-not (literal "/dev/null"))' in profile_text
    assert f'(allow file-write* (subpath "{tmp_path}"))' in profile_text
    assert f'(deny file-read* file-write* (subpath "{tmp_path / ".git"}"))' in profile_text
    assert "(deny network*)" in profile_text


def test_compiler_does_not_widen_profile_to_host_configuration_trees(
    tmp_path: Path,
) -> None:
    profile_text = compile_seatbelt_profile(_workspace_profile(), cwd=tmp_path)

    for undeclared_root in ("/Library", "/usr", "/usr/local", "/opt/homebrew"):
        assert f'(allow file-read* (subpath "{undeclared_root}"))' not in profile_text


def test_compiler_preserves_read_traversal_for_lexical_symlink_ancestors(
    tmp_path: Path,
) -> None:
    profile = RuntimePermissionProfile(
        name="tmp-exact-write",
        filesystem=FilesystemPolicy(
            rules=(
                FilesystemRule(
                    path="/tmp/permission-dogfood.txt",
                    access=FilesystemAccess.WRITE,
                    scope=FilesystemScope.EXACT,
                ),
            )
        ),
    )

    profile_text = compile_seatbelt_profile(profile, cwd=tmp_path)

    assert '(allow file-read* (literal "/tmp"))' in profile_text
    assert '(allow file-read* (subpath "/tmp"))' not in profile_text
    assert '(allow file-write* (subpath "/tmp"))' not in profile_text


@requires_macos_seatbelt
async def test_seatbelt_supports_minimal_toolchain_runtime(tmp_path: Path) -> None:
    if shutil.which("rg") is None:
        pytest.skip("requires ripgrep (rg) on PATH")

    target = tmp_path / "haystack.txt"
    target.write_text("alpha\nneedle-in-haystack\ngamma\n", encoding="utf-8")
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    try:
        search = await session.execute(FileSearchOperation(pattern="needle", path=tmp_path))
        uv_version = await session.execute(CommandOperation(command="uv --version", cwd=tmp_path))
        uname = await session.execute(CommandOperation(command="uname -s", cwd=tmp_path))
        devnull = await session.execute(
            CommandOperation(command="printf ok > /dev/null", cwd=tmp_path)
        )
    finally:
        await session.close()

    assert isinstance(search, OperationCompleted)
    assert search.is_error is False
    assert "needle-in-haystack" in search.output
    assert isinstance(uv_version, ProcessCompleted)
    assert uv_version.exit_code == 0
    assert uv_version.output.startswith("uv ")
    assert uname == ProcessCompleted(output="Darwin\n", exit_code=0)
    assert devnull == ProcessCompleted(output="", exit_code=0)
    assert "runtime_write:/dev/null" in session.boundary.filesystem_rules
    assert "runtime_sysctl_read:hw.pagesize_compat" in session.boundary.process_rules
    assert "runtime_sysctl_read:kern.ostype" in session.boundary.process_rules


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
async def test_approved_external_write_creates_file_with_exact_one_shot_overlay(
    tmp_path: Path,
) -> None:
    profile = _workspace_profile()
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    base = await backend.open(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    outside = Path("/tmp") / f"{tmp_path.name}-outside-write.txt"
    outside.unlink(missing_ok=True)
    operation = FileWriteOperation(path=outside, content="permission dogfood")
    try:
        crossing = await session.execute(operation)
        assert isinstance(crossing, BoundaryViolation)
        request = PermissionDeltaRequest.create(
            tool_use_id="write-tool-1",
            tool_name="Write",
            final_arguments={
                "path": str(outside),
                "content": "permission dogfood",
            },
            profile=profile,
            boundary=base.boundary,
            delta=PermissionDelta.filesystem_path(str(outside)),
            crossing=crossing,
        )
        session.arm(request)

        result = await session.execute(operation)

        assert isinstance(result, OperationCompleted)
        assert result.is_error is False
        assert outside.read_text(encoding="utf-8") == "permission dogfood"
    finally:
        outside.unlink(missing_ok=True)
        await session.close()


@requires_macos_seatbelt
async def test_approved_external_edit_uses_exact_one_shot_atomic_overlay(
    tmp_path: Path,
) -> None:
    profile = _workspace_profile()
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    base = await backend.open(profile)
    session = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-edit.txt"
    outside.write_text("old value", encoding="utf-8")
    operation = FileEditOperation(path=outside, old_str="old", new_str="new")
    try:
        crossing = await session.execute(operation)
        assert isinstance(crossing, BoundaryViolation)
        request = PermissionDeltaRequest.create(
            tool_use_id="edit-tool-1",
            tool_name="Edit",
            final_arguments={
                "path": str(outside),
                "old_str": "old",
                "new_str": "new",
                "replace_all": False,
            },
            profile=profile,
            boundary=base.boundary,
            delta=PermissionDelta.filesystem_path(str(outside)),
            crossing=crossing,
        )
        session.arm(request)

        result = await session.execute(operation)

        assert isinstance(result, OperationCompleted)
        assert result.is_error is False
        assert outside.read_text(encoding="utf-8") == "new value"
        assert not (
            outside.parent / f".{outside.name}.openharness-{request.request_id[:16]}.tmp"
        ).exists()
    finally:
        outside.unlink(missing_ok=True)
        await session.close()


@requires_macos_seatbelt
async def test_runtime_support_does_not_expose_library_configuration(
    tmp_path: Path,
) -> None:
    protected = Path("/Library/LaunchDaemons/postgresql-17.plist")
    if not protected.is_file():
        pytest.skip("host fixture is not installed")
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    try:
        result = await session.execute(FileReadOperation(path=protected))

        assert isinstance(result, BoundaryViolation)
        assert result.dimension == "filesystem.read"
    finally:
        await session.close()


@requires_macos_seatbelt
async def test_command_and_file_worker_cannot_write_protected_workspace_path(
    tmp_path: Path,
) -> None:
    protected = tmp_path / ".git"
    protected.mkdir()
    target = protected / "config"
    target.write_text("original", encoding="utf-8")
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    try:
        command_result = await session.execute(
            CommandOperation(command="printf changed > .git/config", cwd=tmp_path)
        )
        file_result = await session.execute(FileWriteOperation(path=target, content="changed"))

        assert isinstance(command_result, ProcessCompleted)
        assert command_result.exit_code != 0
        assert isinstance(file_result, BoundaryViolation)
        assert file_result.dimension == "filesystem.write"
        assert target.read_text(encoding="utf-8") == "original"
    finally:
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


@requires_macos_seatbelt
async def test_command_environment_excludes_ambient_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "credential-must-not-cross")
    monkeypatch.setenv("DATABASE_PASSWORD", "credential-must-not-cross")
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    try:
        result = await session.execute(CommandOperation(command="/usr/bin/env", cwd=tmp_path))

        assert isinstance(result, ProcessCompleted)
        assert result.exit_code == 0
        assert "OPENHARNESS_API_KEY" not in result.output
        assert "DATABASE_PASSWORD" not in result.output
        assert "credential-must-not-cross" not in result.output
    finally:
        await session.close()


@requires_macos_seatbelt
async def test_default_network_policy_blocks_loopback_child_connection(tmp_path: Path) -> None:
    if shutil.which("nc") is None:
        pytest.skip("requires netcat (nc) on PATH")
    connected = asyncio.Event()

    async def _accept(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")
    session = await backend.open(_workspace_profile())
    try:
        result = await session.execute(
            CommandOperation(command=f"/usr/bin/nc -z 127.0.0.1 {port}", cwd=tmp_path)
        )

        assert isinstance(result, ProcessCompleted)
        assert result.exit_code != 0
        assert connected.is_set() is False
    finally:
        await session.close()
        server.close()
        await server.wait_closed()
