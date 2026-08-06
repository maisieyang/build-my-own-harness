"""Branch-level tests for Seatbelt compilation and structured failures."""

from __future__ import annotations

import asyncio
import json
import signal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    CommandOperation,
    EnforcedBoundary,
    ExecutionEffect,
    ExecutionFailed,
    FileEditOperation,
    FileReadOperation,
    FileSearchOperation,
    FileWriteOperation,
    ProcessCompleted,
    SandboxUnavailableError,
    TimedOut,
)
from openharness.execution.seatbelt import (
    SeatbeltBackend,
    SeatbeltSession,
    _read_bounded_output,
    _worker_request,
    build_sandbox_environment,
    compile_seatbelt_profile,
)
from openharness.permissions import (
    EnvironmentInheritance,
    EnvironmentPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    NetworkPolicy,
    ProcessPolicy,
    RuntimePermissionProfile,
)

if TYPE_CHECKING:
    from pathlib import Path


def _boundary() -> EnforcedBoundary:
    return EnforcedBoundary(
        profile_fingerprint="a" * 64,
        backend="seatbelt",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )


def _session(tmp_path: Path) -> SeatbeltSession:
    return SeatbeltSession(
        executable="/usr/bin/sandbox-exec",
        profile_text="(version 1)\n(allow default)\n",
        environment={},
        boundary=_boundary(),
        boundary_root=tmp_path,
    )


async def test_command_output_reader_drains_but_retains_only_the_configured_limit() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"0123456789")
    reader.feed_eof()

    output, truncated = await _read_bounded_output(reader, max_bytes=4)

    assert output == b"6789"
    assert truncated is True


def test_compiler_covers_no_write_deny_read_deny_write_and_proxy_only_network(
    tmp_path: Path,
) -> None:
    profile = RuntimePermissionProfile(
        name="rules",
        filesystem=FilesystemPolicy(
            rules=(
                FilesystemRule(path="secret", access=FilesystemAccess.DENY_READ),
                FilesystemRule(path="control", access=FilesystemAccess.DENY_WRITE),
                FilesystemRule(path="docs", access=FilesystemAccess.READ),
            )
        ),
        network=NetworkPolicy(
            enabled=True,
            allow_loopback=True,
            allow_private=True,
            allow_link_local=True,
        ),
    )

    text = compile_seatbelt_profile(profile, cwd=tmp_path, network_proxy_port=43123)

    assert "(deny file-write*)" in text
    assert "(deny file-read*" in text
    assert f'(allow file-read* (subpath "{tmp_path / "docs"}"))' in text
    assert "deny file-read*" in text
    assert "control" in text
    assert "(deny network*)" in text
    assert '(allow network-outbound (remote ip "localhost:43123"))' in text


def test_environment_all_none_include_exclude_set_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAFE", "yes")
    monkeypatch.setenv("API_TOKEN", "secret")
    monkeypatch.setenv("DATABASE_PASSWORD", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient-proxy.invalid:8080")
    all_profile = RuntimePermissionProfile(
        name="all",
        environment=EnvironmentPolicy(
            inherit=EnvironmentInheritance.ALL,
            exclude=("SAFE",),
            set_values={"FIXED": "1"},
        ),
    )
    none_profile = RuntimePermissionProfile(
        name="none",
        environment=EnvironmentPolicy(
            inherit=EnvironmentInheritance.NONE,
            include=("SAFE",),
        ),
    )

    all_env = build_sandbox_environment(all_profile)
    none_env = build_sandbox_environment(none_profile)

    assert "SAFE" not in all_env
    assert "API_TOKEN" not in all_env
    assert "DATABASE_PASSWORD" not in all_env
    assert "HTTPS_PROXY" not in all_env
    assert all_env["FIXED"] == "1"
    assert none_env == {"SAFE": "yes"}


def test_preflight_reports_platform_socket_identity_and_resources(tmp_path: Path) -> None:
    profile = RuntimePermissionProfile(
        name="unsupported",
        network=NetworkPolicy(enabled=True, allow_unix_sockets=("/tmp/a.sock",)),
        process=ProcessPolicy(run_as_uid=1, memory_bytes=1024),
    )
    backend = SeatbeltBackend(cwd=tmp_path, executable=str(tmp_path / "missing"))

    with patch("openharness.execution.seatbelt.sys.platform", "linux"):
        support = backend.preflight(profile)

    assert set(support.unsupported_features) >= {
        "platform.macos",
        "sandbox-exec",
        "process.identity",
        "process.resources",
    }


def test_compiler_allows_only_explicit_unix_socket_paths(tmp_path: Path) -> None:
    socket_path = tmp_path / "daemon.sock"
    profile = RuntimePermissionProfile(
        name="socket",
        network=NetworkPolicy(
            enabled=True,
            allow_domains=("example.com",),
            allow_unix_sockets=(str(socket_path),),
        ),
    )

    text = compile_seatbelt_profile(profile, cwd=tmp_path, network_proxy_port=43123)

    assert "(deny network*)" in text
    assert f'(allow network-outbound (remote unix-socket (subpath "{socket_path}")))' in text


def test_preflight_rejects_login_shell_instead_of_silently_ignoring_it(tmp_path: Path) -> None:
    profile = RuntimePermissionProfile(
        name="login-shell",
        process=ProcessPolicy(login_shell=True),
    )
    backend = SeatbeltBackend(cwd=tmp_path, executable="/usr/bin/sandbox-exec")

    support = backend.preflight(profile)

    assert support.supported is False
    assert "process.login_shell" in support.unsupported_features


def test_worker_request_serializes_every_file_operation(tmp_path: Path) -> None:
    assert _worker_request(FileReadOperation(tmp_path))["kind"] == "read"  # type: ignore[index]
    assert _worker_request(FileWriteOperation(tmp_path, "x"))["kind"] == "write"  # type: ignore[index]
    assert _worker_request(FileEditOperation(tmp_path, "a", "b"))["kind"] == "edit"  # type: ignore[index]
    assert _worker_request(FileSearchOperation("x", tmp_path))["kind"] == "search"  # type: ignore[index]
    assert _worker_request(CommandOperation("true", tmp_path)) is None


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "reason"),
    [
        (2, b"", b"worker boom", "worker boom"),
        (0, b"not-json", b"", "invalid sandbox worker response"),
    ],
)
async def test_worker_process_failures_are_structured(
    tmp_path: Path,
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    reason: str,
) -> None:
    process = AsyncMock()
    process.returncode = returncode
    process.communicate.return_value = (stdout, stderr)
    with patch(
        "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    ):
        result = await _session(tmp_path).execute(FileReadOperation(tmp_path / "x"))

    assert isinstance(result, ExecutionFailed)
    assert reason in result.reason


async def test_worker_permission_payload_becomes_typed_boundary_violation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "outside.txt"
    payload = {
        "output": "denied",
        "is_error": True,
        "metadata": {
            "boundary_violation": {
                "dimension": "filesystem.read",
                "requested": str(target),
                "evidence": "OS sandbox denied the filesystem operation",
                "hard_deny": False,
            }
        },
    }
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (json.dumps(payload).encode(), b"")
    with patch(
        "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    ):
        result = await _session(tmp_path).execute(FileReadOperation(target))

    assert result == BoundaryViolation(
        dimension="filesystem.read",
        requested=str(target),
        evidence="OS sandbox denied the filesystem operation",
    )


async def test_command_timeout_kills_process_group(tmp_path: Path) -> None:
    process = AsyncMock()
    process.pid = 123
    process.communicate.side_effect = [asyncio.TimeoutError, (b"late", None)]
    with (
        patch(
            "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ),
        patch("openharness.execution.seatbelt.os.killpg") as killpg,
    ):
        result = await _session(tmp_path).execute(
            CommandOperation("sleep 10", tmp_path, timeout=0.01)
        )

    assert isinstance(result, TimedOut)
    killpg.assert_called_once()


async def test_proxy_denial_becomes_a_typed_boundary_violation_without_stderr_parsing(
    tmp_path: Path,
) -> None:
    violation = BoundaryViolation(
        dimension="network.domain",
        requested="blocked.example:443",
        evidence="domain is not in the active allowlist",
    )

    class _Proxy:
        def url_for(self, request_id: str) -> str:
            assert request_id
            return "http://request:x@127.0.0.1:43123"

        def violations_for(self, request_id: str) -> tuple[BoundaryViolation, ...]:
            assert request_id
            return (violation,)

    process = AsyncMock()
    process.returncode = 56
    process.communicate.return_value = (b"ordinary curl text that is not parsed", None)
    session = SeatbeltSession(
        executable="/usr/bin/sandbox-exec",
        profile_text="(version 1)\n",
        environment={},
        boundary=_boundary(),
        boundary_root=tmp_path,
        network_proxy=_Proxy(),  # type: ignore[arg-type]
    )
    with patch(
        "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    ) as create:
        result = await session.execute(CommandOperation("curl https://blocked.example", tmp_path))

    assert result == violation
    child_env = create.await_args.kwargs["env"]
    assert child_env["HTTPS_PROXY"] == "http://request:x@127.0.0.1:43123"
    assert child_env["HTTP_PROXY"] == child_env["HTTPS_PROXY"]
    assert child_env["NO_PROXY"] == ""


async def test_ordinary_command_failure_is_not_mislabeled_as_boundary_violation(
    tmp_path: Path,
) -> None:
    class _Proxy:
        def url_for(self, request_id: str) -> str:
            return "http://request:x@127.0.0.1:43123"

        def violations_for(self, request_id: str) -> tuple[BoundaryViolation, ...]:
            return ()

    process = AsyncMock()
    process.returncode = 6
    process.communicate.return_value = (b"could not resolve host", None)
    session = SeatbeltSession(
        executable="/usr/bin/sandbox-exec",
        profile_text="(version 1)\n",
        environment={},
        boundary=_boundary(),
        boundary_root=tmp_path,
        network_proxy=_Proxy(),  # type: ignore[arg-type]
    )
    with patch(
        "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    ):
        result = await session.execute(CommandOperation("curl https://missing.invalid", tmp_path))

    assert isinstance(result, ProcessCompleted)
    assert result.exit_code == 6
    assert result.output == "could not resolve host"


async def test_profile_timeout_caps_a_longer_operation_timeout(tmp_path: Path) -> None:
    process = AsyncMock()
    process.pid = 123
    process.communicate.side_effect = [asyncio.TimeoutError, (b"late", None)]
    session = SeatbeltSession(
        executable="/usr/bin/sandbox-exec",
        profile_text="(version 1)\n(allow default)\n",
        environment={},
        boundary=_boundary(),
        boundary_root=tmp_path,
        default_timeout=0.01,
    )
    real_wait_for = asyncio.wait_for
    with (
        patch(
            "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ),
        patch("openharness.execution.seatbelt.asyncio.wait_for", wraps=real_wait_for) as wait_for,
        patch("openharness.execution.seatbelt.os.killpg"),
    ):
        result = await session.execute(CommandOperation("sleep 10", tmp_path, timeout=600))

    assert isinstance(result, TimedOut)
    assert wait_for.await_args.kwargs["timeout"] == 0.01


async def test_profile_timeout_kills_the_file_worker_process_group(tmp_path: Path) -> None:
    process = AsyncMock()
    process.pid = 321
    process.communicate.side_effect = [asyncio.TimeoutError, (b"", b"")]
    session = SeatbeltSession(
        executable="/usr/bin/sandbox-exec",
        profile_text="(version 1)\n(allow default)\n",
        environment={},
        boundary=_boundary(),
        boundary_root=tmp_path,
        default_timeout=0.01,
    )
    with (
        patch(
            "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ),
        patch("openharness.execution.seatbelt.os.killpg") as killpg,
    ):
        result = await session.execute(FileReadOperation(tmp_path / "x"))

    assert isinstance(result, TimedOut)
    killpg.assert_called_once_with(321, signal.SIGKILL)


async def test_backend_rejects_failed_profile_probe(tmp_path: Path) -> None:
    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (b"bad profile", None)
    backend = SeatbeltBackend(cwd=tmp_path)
    with (
        patch("openharness.execution.seatbelt.sys.platform", "darwin"),
        patch("openharness.execution.seatbelt.os.path.isfile", return_value=True),
        patch("openharness.execution.seatbelt.os.access", return_value=True),
        patch(
            "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ),
        pytest.raises(SandboxUnavailableError, match="bad profile"),
    ):
        await backend.open(RuntimePermissionProfile(name="empty"))


async def test_backend_installs_managed_proxy_and_reports_network_coverage(
    tmp_path: Path,
) -> None:
    profile = RuntimePermissionProfile(
        name="networked",
        network=NetworkPolicy(enabled=True, allow_domains=("pypi.org",)),
    )
    probe = AsyncMock()
    probe.returncode = 0
    probe.communicate.return_value = (b"", None)
    proxy = AsyncMock()
    proxy.port = 43123
    proxy.close = AsyncMock()
    backend = SeatbeltBackend(cwd=tmp_path)
    with (
        patch("openharness.execution.seatbelt.sys.platform", "darwin"),
        patch("openharness.execution.seatbelt.os.path.isfile", return_value=True),
        patch("openharness.execution.seatbelt.os.access", return_value=True),
        patch(
            "openharness.execution.seatbelt.asyncio.create_subprocess_exec",
            AsyncMock(return_value=probe),
        ) as create,
        patch(
            "openharness.execution.seatbelt.ManagedNetworkProxy.open",
            AsyncMock(return_value=proxy),
        ) as open_proxy,
    ):
        session = await backend.open(profile)

    open_proxy.assert_awaited_once_with(profile.network)
    compiled_profile = create.await_args.args[2]
    assert '(allow network-outbound (remote ip "localhost:43123"))' in compiled_profile
    assert session.boundary.covers(ExecutionEffect.NETWORK)
    assert "proxy:127.0.0.1:43123" in session.boundary.network_rules
    await session.close()
    proxy.close.assert_awaited_once()
