"""Unit tests for ``SandboxExecution`` — P7b-T1.

All tests mock ``aiodocker`` so they run cross-platform with no Docker
daemon needed. Real-Docker integration coverage lives in
``test_sandbox_integration.py`` (P7b-T3) — gated on ``docker info``.

Test surfaces:

1. **Memory parsing** — Docker-style suffixes (b/k/m/g) → bytes.
2. **Lifecycle** — `__aenter__` pulls + spawns; `__aexit__` cleans up.
3. **Spawn config** — image / Binds / WorkingDir / NetworkMode /
   cgroup limits all reach the container create call correctly.
4. **`run_command`** — exec dispatch, output collection, exit code,
   timeout SIGTERM/SIGKILL escalation.
5. **Error paths** — `run_command` outside context raises; spawn
   failure triggers cleanup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openharness.execution.sandbox import (
    _CONTAINER_CWD,
    SandboxExecution,
    _parse_memory,
)

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Memory parser                                                                #
# --------------------------------------------------------------------------- #


class TestParseMemory:
    @pytest.mark.parametrize(
        ("spec", "expected_bytes"),
        [
            ("1024", 1024),
            ("1024b", 1024),
            ("1k", 1024),
            ("512m", 512 * 1024**2),
            ("1g", 1024**3),
            ("2G", 2 * 1024**3),  # case-insensitive suffix
            (" 100m ", 100 * 1024**2),  # whitespace tolerated
        ],
    )
    def test_parse(self, spec: str, expected_bytes: int) -> None:
        assert _parse_memory(spec) == expected_bytes

    @pytest.mark.parametrize("bad", ["", "abc", "100tb", "1.5g", "g"])
    def test_invalid_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="invalid memory spec"):
            _parse_memory(bad)


# --------------------------------------------------------------------------- #
# Mock helpers                                                                 #
# --------------------------------------------------------------------------- #


def _make_mock_docker() -> tuple[Any, Any, Any]:
    """Build a (docker_class_mock, container_mock, exec_mock) triple
    wired together so SandboxExecution sees a coherent fake aiodocker.

    - docker_class_mock: patches ``aiodocker.Docker`` to return a mock
      instance with ``.containers`` and ``.images`` namespaces.
    - container_mock: returned by ``containers.create()``; has
      ``start`` / ``stop`` / ``delete`` / ``exec`` methods.
    - exec_mock: returned by ``container.exec()``; has ``start`` (async
      context manager) + ``inspect()``.
    """
    # Exec object: ``.start(detach=False)`` is an async context manager
    # yielding a stream with ``.read_out()`` returning Message-shaped
    # objects until None. ``.inspect()`` returns dict with ExitCode/Pid.
    stream_mock = MagicMock()
    stream_mock.read_out = AsyncMock(side_effect=[None])  # empty output by default

    exec_mock = MagicMock()
    start_ctx = AsyncMock()
    start_ctx.__aenter__ = AsyncMock(return_value=stream_mock)
    start_ctx.__aexit__ = AsyncMock(return_value=None)
    exec_mock.start = MagicMock(return_value=start_ctx)
    exec_mock.inspect = AsyncMock(return_value={"ExitCode": 0, "Pid": 12345})

    # Container: ``.start()`` / ``.stop()`` / ``.delete()`` async; ``.exec(...)``
    # is async and returns the exec_mock.
    container_mock = MagicMock()
    container_mock.start = AsyncMock()
    container_mock.stop = AsyncMock()
    container_mock.delete = AsyncMock()
    container_mock.exec = AsyncMock(return_value=exec_mock)

    # Docker: ``.containers.create()`` returns container_mock; ``.images.inspect`` /
    # ``.images.pull`` async; ``.close()`` async.
    docker_mock = MagicMock()
    docker_mock.containers.create = AsyncMock(return_value=container_mock)
    docker_mock.images.inspect = AsyncMock()  # default: image present
    docker_mock.images.pull = AsyncMock()
    docker_mock.close = AsyncMock()

    # The class itself returns the instance when called.
    docker_class_mock = MagicMock(return_value=docker_mock)

    return docker_class_mock, container_mock, exec_mock


def _set_stream_output(exec_mock: Any, chunks: list[bytes]) -> None:
    """Wire the exec's stream to yield the given byte chunks (then None)."""
    messages = [MagicMock(data=c) for c in chunks] + [None]
    stream_mock = exec_mock.start.return_value.__aenter__.return_value
    stream_mock.read_out = AsyncMock(side_effect=messages)


# --------------------------------------------------------------------------- #
# Lifecycle                                                                   #
# --------------------------------------------------------------------------- #


class TestSandboxLifecycle:
    async def test_aenter_pulls_image_if_missing(self, tmp_path: Path) -> None:
        docker_cls, container, _exec = _make_mock_docker()
        # Simulate image not present → first inspect raises 404
        from aiodocker.exceptions import DockerError

        docker_inst = docker_cls.return_value
        docker_inst.images.inspect.side_effect = DockerError(404, {"message": "not found"})

        with patch("aiodocker.Docker", docker_cls):
            sb = SandboxExecution(cwd=tmp_path)
            async with sb:
                pass

        # Pull was called once with the configured image.
        docker_inst.images.pull.assert_awaited_once_with("python:3.12-slim")
        # Container was created + started.
        docker_inst.containers.create.assert_awaited_once()
        container.start.assert_awaited_once()
        # Cleanup happened.
        container.stop.assert_awaited()
        container.delete.assert_awaited()
        docker_inst.close.assert_awaited()

    async def test_aenter_skips_pull_when_image_present(self, tmp_path: Path) -> None:
        docker_cls, _container, _exec = _make_mock_docker()
        # Default: inspect succeeds → no pull.
        with patch("aiodocker.Docker", docker_cls):
            sb = SandboxExecution(cwd=tmp_path)
            async with sb:
                pass

        docker_inst = docker_cls.return_value
        docker_inst.images.inspect.assert_awaited_once_with("python:3.12-slim")
        docker_inst.images.pull.assert_not_awaited()

    async def test_aenter_propagates_non_404_image_errors(self, tmp_path: Path) -> None:
        from aiodocker.exceptions import DockerError

        docker_cls, _container, _exec = _make_mock_docker()
        docker_inst = docker_cls.return_value
        # 500 → not "image missing"; propagate.
        docker_inst.images.inspect.side_effect = DockerError(500, {"message": "server error"})

        with patch("aiodocker.Docker", docker_cls):
            sb = SandboxExecution(cwd=tmp_path)
            with pytest.raises(DockerError):
                async with sb:
                    pass

        # Cleanup still ran (best-effort).
        docker_inst.close.assert_awaited()

    async def test_spawn_failure_triggers_cleanup(self, tmp_path: Path) -> None:
        docker_cls, _container, _exec = _make_mock_docker()
        docker_inst = docker_cls.return_value
        # Container creation fails AFTER image check passed.
        docker_inst.containers.create.side_effect = RuntimeError("create failed")

        with patch("aiodocker.Docker", docker_cls):
            sb = SandboxExecution(cwd=tmp_path)
            with pytest.raises(RuntimeError, match="create failed"):
                async with sb:
                    pass

        # Daemon was still closed cleanly.
        docker_inst.close.assert_awaited()


# --------------------------------------------------------------------------- #
# Spawn config                                                                #
# --------------------------------------------------------------------------- #


class TestSandboxSpawnConfig:
    async def test_default_spawn_args(self, tmp_path: Path) -> None:
        docker_cls, _container, _exec = _make_mock_docker()
        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path):
                pass

        docker_inst = docker_cls.return_value
        create_kwargs = docker_inst.containers.create.call_args.kwargs
        config = create_kwargs["config"]
        assert config["Image"] == "python:3.12-slim"
        assert config["WorkingDir"] == _CONTAINER_CWD
        assert config["Cmd"] == ["sleep", "infinity"]
        host = config["HostConfig"]
        assert host["Binds"] == [f"{tmp_path}:{_CONTAINER_CWD}:rw"]
        assert host["NetworkMode"] == "none"
        assert host["Memory"] == 1024**3  # 1GB default
        assert host["CpuQuota"] == 100_000  # 1 CPU default
        assert host["PidsLimit"] == 256
        assert host["ReadonlyRootfs"] is True
        assert host["CapDrop"] == ["ALL"]
        assert host["SecurityOpt"] == ["no-new-privileges:true"]
        assert "/tmp" in host["Tmpfs"]
        assert config["User"] != ""

    async def test_protected_workspace_paths_are_mounted_read_only(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".codex").mkdir()
        docker_cls, _container, _exec = _make_mock_docker()

        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path):
                pass

        config = docker_cls.return_value.containers.create.call_args.kwargs["config"]
        assert f"{tmp_path / '.git'}:{_CONTAINER_CWD}/.git:ro" in config["HostConfig"]["Binds"]
        assert f"{tmp_path / '.codex'}:{_CONTAINER_CWD}/.codex:ro" in config["HostConfig"]["Binds"]

    async def test_custom_image_and_network(self, tmp_path: Path) -> None:
        docker_cls, _container, _exec = _make_mock_docker()
        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path, image="ubuntu:latest", network="bridge"):
                pass

        config = docker_cls.return_value.containers.create.call_args.kwargs["config"]
        assert config["Image"] == "ubuntu:latest"
        assert config["HostConfig"]["NetworkMode"] == "bridge"

    async def test_custom_cgroup_limits(self, tmp_path: Path) -> None:
        docker_cls, _container, _exec = _make_mock_docker()
        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path, memory="512m", cpus=0.5, pids=128):
                pass

        host = docker_cls.return_value.containers.create.call_args.kwargs["config"]["HostConfig"]
        assert host["Memory"] == 512 * 1024**2
        assert host["CpuQuota"] == 50_000  # 0.5 CPU
        assert host["PidsLimit"] == 128

    async def test_default_runtime_is_runc(self, tmp_path: Path) -> None:
        # P7c-T1 (D23.1/D23.4): default runtime preserves Phase 7b
        # behavior — runc (Docker's own default).
        docker_cls, _container, _exec = _make_mock_docker()
        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path):
                pass

        host = docker_cls.return_value.containers.create.call_args.kwargs["config"]["HostConfig"]
        assert host["Runtime"] == "runc"

    async def test_gvisor_runtime_passes_through(self, tmp_path: Path) -> None:
        # P7c-T1: runtime="runsc" lands as HostConfig.Runtime so the
        # Docker daemon dispatches to gVisor (assuming runsc is
        # installed on the host).
        docker_cls, _container, _exec = _make_mock_docker()
        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path, runtime="runsc"):
                pass

        host = docker_cls.return_value.containers.create.call_args.kwargs["config"]["HostConfig"]
        assert host["Runtime"] == "runsc"

    async def test_arbitrary_runtime_passes_through(self, tmp_path: Path) -> None:
        # P7c-T1 (D23.2): no client-side validation. Users can specify
        # custom OCI runtimes (kata, sysbox, etc.) — Docker daemon
        # rejects unknown runtimes at container-create time.
        docker_cls, _container, _exec = _make_mock_docker()
        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path, runtime="kata-runtime"):
                pass

        host = docker_cls.return_value.containers.create.call_args.kwargs["config"]["HostConfig"]
        assert host["Runtime"] == "kata-runtime"


# --------------------------------------------------------------------------- #
# run_command                                                                 #
# --------------------------------------------------------------------------- #


class TestSandboxRunCommand:
    async def test_run_command_returns_process_result(self, tmp_path: Path) -> None:
        docker_cls, container, exec_mock = _make_mock_docker()
        _set_stream_output(exec_mock, [b"hello\n"])
        exec_mock.inspect = AsyncMock(return_value={"ExitCode": 0, "Pid": 1})

        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path) as sb:
                result = await sb.run_command("echo hello", cwd=tmp_path)

        # Exec was called with sh -c + WorkingDir /workspace per D18.3.
        container.exec.assert_awaited_once()
        exec_kwargs = container.exec.call_args.kwargs
        assert exec_kwargs["cmd"] == ["sh", "-c", "echo hello"]
        assert exec_kwargs["workdir"] == _CONTAINER_CWD
        assert exec_kwargs["stdout"] is True
        assert exec_kwargs["stderr"] is True
        # Result reflects the stream's output.
        assert result.output == "hello\n"
        assert result.exit_code == 0
        assert result.timed_out is False

    async def test_run_command_propagates_non_zero_exit(self, tmp_path: Path) -> None:
        docker_cls, _container, exec_mock = _make_mock_docker()
        _set_stream_output(exec_mock, [b"oops\n"])
        exec_mock.inspect = AsyncMock(return_value={"ExitCode": 42, "Pid": 1})

        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path) as sb:
                result = await sb.run_command("false", cwd=tmp_path)

        assert result.exit_code == 42
        assert result.output == "oops\n"

    async def test_run_command_concatenates_multiple_chunks(self, tmp_path: Path) -> None:
        docker_cls, _container, exec_mock = _make_mock_docker()
        _set_stream_output(exec_mock, [b"first", b" ", b"second"])

        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path) as sb:
                result = await sb.run_command("anything", cwd=tmp_path)

        assert result.output == "first second"

    async def test_run_command_drains_but_bounds_retained_output(self, tmp_path: Path) -> None:
        docker_cls, _container, exec_mock = _make_mock_docker()
        _set_stream_output(exec_mock, [b"0123", b"456789"])

        with (
            patch("aiodocker.Docker", docker_cls),
            patch("openharness.execution.sandbox._MAX_RETAINED_OUTPUT_BYTES", 4),
        ):
            async with SandboxExecution(cwd=tmp_path) as sb:
                result = await sb.run_command("anything", cwd=tmp_path)

        assert "output truncated" in result.output
        assert result.output.endswith("6789")

    async def test_run_command_outside_context_raises(self, tmp_path: Path) -> None:
        sb = SandboxExecution(cwd=tmp_path)
        with pytest.raises(RuntimeError, match="outside an active"):
            await sb.run_command("anything", cwd=tmp_path)

    async def test_run_command_timeout_returns_sentinel(self, tmp_path: Path) -> None:
        docker_cls, container, exec_mock = _make_mock_docker()
        # Hang the stream forever to trigger timeout.
        stream_mock = exec_mock.start.return_value.__aenter__.return_value

        async def _never() -> None:
            import asyncio

            await asyncio.sleep(10)

        stream_mock.read_out = AsyncMock(side_effect=_never)

        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path) as sb:
                # Patch the grace period to be near-zero so this test
                # doesn't actually sleep 2s.
                with patch("openharness.execution.sandbox._KILL_GRACE_PERIOD_SECONDS", 0.01):
                    result = await sb.run_command("sleep 5", cwd=tmp_path, timeout=0.05)

        assert result.timed_out is True
        assert result.exit_code == -1
        assert result.output == ""
        container.stop.assert_any_await(timeout=0)
        assert container.start.await_count == 2

    async def test_run_command_handles_none_exit_code(self, tmp_path: Path) -> None:
        # Edge case: aiodocker can return ExitCode=None for some races.
        # Substrate normalizes to sentinel -1.
        docker_cls, _container, exec_mock = _make_mock_docker()
        _set_stream_output(exec_mock, [b"x"])
        exec_mock.inspect = AsyncMock(return_value={"ExitCode": None, "Pid": 1})

        with patch("aiodocker.Docker", docker_cls):
            async with SandboxExecution(cwd=tmp_path) as sb:
                result = await sb.run_command("x", cwd=tmp_path)
        assert result.exit_code == -1
