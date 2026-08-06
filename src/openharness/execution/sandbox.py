"""``SandboxExecution`` — Docker container substrate — P7b-T1.

Per ``decisions/16-phase-7b-boundary.md`` D18.1-D18.7:
:class:`SandboxExecution` is the second :class:`ExecutionEnvironment`
implementation (after :class:`HostExecution` from Phase 7a). It runs
Bash commands inside a Docker container with:

- cwd bind-mounted as ``/workspace`` (D18.4)
- substrate-translated cwd path (LLM sees host paths; D18.3)
- ``--network none`` default (D18.5)
- cgroup limits (1GB / 1 CPU / 256 pid by default; D18.6)
- stock ``python:3.12-slim`` image by default (D18.7); user can override

Per-query lifecycle (D18.2): :class:`SandboxExecution` is an async
context manager. ``__aenter__`` pulls the image if needed + spawns the
container; ``__aexit__`` stops + removes. The CLI ``_run_ask`` enters
the context when ``--sandbox`` is set, then constructs ``QueryContext``
with ``execution_env`` pointing at the active sandbox.

aiodocker is async-native (D18.1) so the substrate's ``run_command``
maps cleanly onto the ``ExecutionEnvironment`` Protocol's ``await``
shape. All aiodocker imports are confined to this module — callers
import :class:`SandboxExecution` only, with the bounded untyped
surface documented in ``pyproject.toml`` ``[[tool.mypy.overrides]]``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from typing import TYPE_CHECKING, Any

import aiodocker
import aiodocker.exceptions

from openharness.execution.base import ProcessResult

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


# Per D18.3: container-side cwd is always ``/workspace`` (the bind
# mount target). Host paths are translated to this constant.
_CONTAINER_CWD = "/workspace"
_MAX_RETAINED_OUTPUT_BYTES = 8 * 1024 * 1024
_OUTPUT_TRUNCATED_MARKER = b"[... output truncated; retained tail ...]\n"

# Grace period before escalating SIGTERM → SIGKILL inside the container.
# Mirrors :class:`HostExecution` behavior so timeout UX is uniform.
_KILL_GRACE_PERIOD_SECONDS = 2.0

# Sentinel exit code returned when the container exec is killed by
# timeout — matches :class:`HostExecution` for consistency.
_TIMEOUT_EXIT_CODE = -1


def _parse_memory(value: str) -> int:
    """Parse a Docker-style memory string (``"1g"`` / ``"512m"`` /
    ``"1024b"`` / bare bytes) into byte count.

    Accepts: ``b`` (bytes) / ``k`` (KiB) / ``m`` (MiB) / ``g`` (GiB);
    case-insensitive suffix. No suffix means bytes.
    """
    match = re.fullmatch(r"\s*(\d+)\s*([bkmgBKMG]?)\s*", value)
    if not match:
        raise ValueError(f"invalid memory spec {value!r} (expected '1g' / '512m' / '1024b' / etc.)")
    n = int(match.group(1))
    suffix = match.group(2).lower()
    multipliers = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
    return n * multipliers[suffix]


class SandboxExecution:
    """Docker-container substrate satisfying the ``ExecutionEnvironment``
    Protocol.

    Per-query lifecycle:construct → ``async with`` → ``run_command``
    one or more times → context exit cleans up. Not re-entrant —
    enter once per sandbox instance.
    """

    def __init__(
        self,
        *,
        cwd: Path,
        image: str = "python:3.12-slim",
        network: str = "none",
        memory: str = "1g",
        cpus: float = 1.0,
        pids: int = 256,
        runtime: str = "runc",
        environment: dict[str, str] | None = None,
        uid: int | None = None,
        gid: int | None = None,
    ) -> None:
        self._cwd_host = cwd
        self._image = image
        self._network = network
        self._memory_bytes = _parse_memory(memory)
        # Docker uses CpuQuota / CpuPeriod (default period 100ms = 100_000us;
        # quota of 100_000 = 1 CPU equivalent; 50_000 = 0.5 CPU, etc.)
        self._cpu_quota = int(cpus * 100_000)
        self._pids_limit = pids
        # P7c-T1 (D23.1/D23.2): OCI runtime — "runc" (default; shared
        # host kernel) or "runsc" (gVisor user-space syscall
        # interposition). Any string passes through to Docker; daemon
        # validates and rejects unknown runtimes when the container is
        # created (D23.3). No client-side allowlist so users with custom
        # OCI runtimes (kata, sysbox, ...) work without framework
        # changes.
        self._runtime = runtime
        self._environment = None if environment is None else dict(environment)
        self._uid = os.getuid() if uid is None else uid
        self._gid = os.getgid() if gid is None else gid

        # Lifecycle state (set by __aenter__, used by run_command, torn
        # down by __aexit__):
        self._docker: aiodocker.Docker | None = None
        self._container: Any | None = None  # aiodocker.containers.DockerContainer

    async def __aenter__(self) -> SandboxExecution:
        self._docker = aiodocker.Docker()
        try:
            await self._ensure_image()
            self._container = await self._spawn_container()
            await self._container.start()
        except Exception:
            # Best-effort cleanup if spawn fails partway through.
            await self._cleanup_quietly()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        await self._cleanup_quietly()

    async def _ensure_image(self) -> None:
        assert self._docker is not None
        try:
            await self._docker.images.inspect(self._image)
            return  # image present
        except aiodocker.exceptions.DockerError as exc:
            # 404 = not found — pull it. Other errors propagate.
            if exc.status != 404:
                raise
        # Auto-pull. ``stream=False`` materializes the pull as a list
        # of progress dicts — we don't surface progress in Phase 7b
        # (just block until done); could plumb to observability later.
        await self._docker.images.pull(self._image)

    async def _spawn_container(self) -> Any:
        assert self._docker is not None
        protected_names = (".git", ".codex", ".agents")
        protected_binds = [
            f"{path}:{_CONTAINER_CWD}/{name}:ro"
            for name in protected_names
            if (path := self._cwd_host / name).exists()
        ]
        protected_tmpfs = {
            f"{_CONTAINER_CWD}/{name}": "ro,noexec,nosuid,nodev,size=4096"
            for name in protected_names
            if not (self._cwd_host / name).exists()
        }
        config = {
            "Image": self._image,
            "User": f"{self._uid}:{self._gid}",
            # Keep the container alive so we can ``exec`` into it for
            # each ``run_command`` call. ``sleep infinity`` is the
            # idiomatic POSIX way; works in any image with coreutils.
            "Cmd": ["sleep", "infinity"],
            "WorkingDir": _CONTAINER_CWD,
            "HostConfig": {
                "Binds": [
                    f"{self._cwd_host}:{_CONTAINER_CWD}:rw",
                    *protected_binds,
                ],
                "NetworkMode": self._network,
                "Memory": self._memory_bytes,
                "CpuQuota": self._cpu_quota,
                "CpuPeriod": 100_000,  # standard 100ms period
                "PidsLimit": self._pids_limit,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {
                    "/tmp": "rw,noexec,nosuid,nodev,size=67108864",
                    **protected_tmpfs,
                },
                # P7c: OCI runtime — "runc" default, "runsc" for gVisor.
                "Runtime": self._runtime,
                # Don't bind-mount any host paths beyond cwd (D18.4)
                "AutoRemove": False,  # we explicitly delete in __aexit__
            },
        }
        if self._environment is not None:
            config["Env"] = [f"{name}={value}" for name, value in sorted(self._environment.items())]
        return await self._docker.containers.create(config=config)

    async def _cleanup_quietly(self) -> None:
        """Stop + remove container + close daemon connection,swallowing
        errors so cleanup never masks the original exception.
        """
        if self._container is not None:
            with contextlib.suppress(Exception):
                await self._container.stop(timeout=5)
            with contextlib.suppress(Exception):
                await self._container.delete(force=True)
            self._container = None
        if self._docker is not None:
            with contextlib.suppress(Exception):
                await self._docker.close()
            self._docker = None

    async def run_command(
        self,
        command: str,
        cwd: Path,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run ``command`` inside the running container.

        Per D18.3:``cwd`` is the host path the engine passed through;
        we translate it to the container's bind-mount target
        ``/workspace``. If the LLM emitted a host-style absolute path
        in ``command`` itself,it will fail inside the container —
        that's a defense feature(LLM can't reach outside ``/workspace``
        even if it tries).
        """
        del cwd  # always translated to _CONTAINER_CWD per D18.3
        if self._container is None:
            raise RuntimeError(
                "SandboxExecution.run_command called outside an active "
                "`async with` context — enter the context first"
            )

        exec_obj = await self._container.exec(
            cmd=["sh", "-c", command],
            workdir=_CONTAINER_CWD,
            # Merge stderr into stdout (same pipe-level merge as
            # HostExecution) — preserves chronological ordering.
            stdout=True,
            stderr=True,
        )

        try:
            output_bytes, output_truncated = await asyncio.wait_for(
                _collect_exec_output(exec_obj),
                timeout=timeout,
            )
            timed_out = False
        except asyncio.TimeoutError:
            await _terminate_exec(exec_obj, self._container)
            return ProcessResult(
                output="",
                exit_code=_TIMEOUT_EXIT_CODE,
                timed_out=True,
            )

        inspect = await exec_obj.inspect()
        exit_code = inspect.get("ExitCode", _TIMEOUT_EXIT_CODE)
        if exit_code is None:
            exit_code = _TIMEOUT_EXIT_CODE

        return ProcessResult(
            output=((_OUTPUT_TRUNCATED_MARKER if output_truncated else b"") + output_bytes).decode(
                "utf-8", errors="replace"
            ),
            exit_code=int(exit_code),
            timed_out=timed_out,
        )


async def _collect_exec_output(exec_obj: Any) -> tuple[bytes, bool]:
    """Drain an aiodocker exec's output stream into a single ``bytes``
    blob. ``aiodocker``'s exec stream yields ``Message`` objects with
    ``.data`` (the raw bytes) and ``.stream`` (1 = stdout, 2 = stderr,
    both merged here per D18.4).
    """
    retained = bytearray()
    truncated = False
    async with exec_obj.start(detach=False) as stream:
        while True:
            msg = await stream.read_out()
            if msg is None:
                break
            chunk = msg.data
            if len(chunk) >= _MAX_RETAINED_OUTPUT_BYTES:
                had_retained = bool(retained)
                retained[:] = chunk[-_MAX_RETAINED_OUTPUT_BYTES:]
                truncated = truncated or had_retained or len(chunk) > _MAX_RETAINED_OUTPUT_BYTES
                continue
            overflow = len(retained) + len(chunk) - _MAX_RETAINED_OUTPUT_BYTES
            if overflow > 0:
                del retained[:overflow]
                truncated = True
            retained.extend(chunk)
    return bytes(retained), truncated


async def _terminate_exec(exec_obj: Any, container: Any) -> None:
    """Terminate the entire container process namespace, then restart it.

    A Docker exec PID is not guaranteed to be a process-group leader, so
    killing that PID can leave grandchildren alive. Stopping the container is
    the backend's deterministic whole-tree primitive; restarting preserves the
    per-session container after the timed-out command has been reaped.
    """
    del exec_obj
    timeout = max(0, round(_KILL_GRACE_PERIOD_SECONDS))
    try:
        await container.stop(timeout=timeout)
    except Exception:
        with contextlib.suppress(Exception):
            await container.kill(signal="SIGKILL")
    with contextlib.suppress(Exception):
        await container.start()
