"""Docker command-only implementation of the verified backend contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.execution.boundary import (
    BackendSupport,
    BoundaryVerification,
    CommandOperation,
    DataPlaneOperation,
    EnforcedBoundary,
    ExecutionEffect,
    ExecutionFailed,
    ProcessCompleted,
    SandboxUnavailableError,
    TimedOut,
)
from openharness.execution.sandbox import SandboxExecution
from openharness.execution.seatbelt import build_sandbox_environment
from openharness.permissions.profile import workspace_runtime_profile

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.permissions.profile import RuntimePermissionProfile


class DockerCommandSession:
    """Active Docker container whose verified coverage is only commands."""

    def __init__(
        self,
        *,
        sandbox: SandboxExecution,
        boundary: EnforcedBoundary,
        default_timeout: float | None,
    ) -> None:
        self._sandbox = sandbox
        self._boundary = boundary
        self._default_timeout = default_timeout
        self._closed = False

    @property
    def boundary(self) -> EnforcedBoundary:
        return self._boundary

    async def execute(
        self, operation: DataPlaneOperation
    ) -> ProcessCompleted | TimedOut | ExecutionFailed:
        if not isinstance(operation, CommandOperation):
            return ExecutionFailed(reason="docker command backend only accepts command operations")
        result = await self._sandbox.run_command(
            operation.command,
            cwd=operation.cwd,
            timeout=_bounded_timeout(operation.timeout, self._default_timeout),
        )
        if result.timed_out:
            return TimedOut(output=result.output)
        return ProcessCompleted(output=result.output, exit_code=result.exit_code)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._sandbox.__aexit__(None, None, None)


class DockerCommandBackend:
    """Compile the subset of a profile expressible by Docker commands.

    It never advertises file-tool, MCP, Web, or session-wide coverage.
    Domain-filtered networking is rejected because Docker's ``none`` / bridge
    switch cannot implement it faithfully.
    """

    name = "docker-command"

    def __init__(
        self,
        *,
        cwd: Path,
        image: str = "python:3.12-slim",
        memory: str = "1g",
        cpus: float = 1.0,
        pids: int = 256,
        runtime: str = "runc",
    ) -> None:
        self._cwd = cwd
        self._image = image
        self._memory = memory
        self._cpus = cpus
        self._pids = pids
        self._runtime = runtime

    def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
        unsupported: list[str] = []
        if profile.filesystem != workspace_runtime_profile().filesystem:
            unsupported.append("filesystem.fixed_workspace_policy")
        if profile.network.enabled and (
            profile.network.allow_domains
            or profile.network.deny_domains
            or not profile.network.allow_private
            or not profile.network.allow_loopback
            or not profile.network.allow_link_local
        ):
            unsupported.append("network.domain_allowlist")
        if profile.network.allow_unix_sockets:
            unsupported.append("network.unix_socket_allowlist")
        if profile.process.login_shell:
            unsupported.append("process.login_shell")
        if unsupported:
            return BackendSupport.unsupported(
                backend=self.name,
                features=tuple(unsupported),
                reason="Docker command backend cannot install every requested profile dimension",
            )
        return BackendSupport.available(backend=self.name)

    async def open(self, profile: RuntimePermissionProfile) -> DockerCommandSession:
        support = self.preflight(profile)
        if not support.supported:
            features = ", ".join(support.unsupported_features)
            raise SandboxUnavailableError(
                f"{self.name} cannot enforce profile features: {features}"
            )
        sandbox = SandboxExecution(
            cwd=self._cwd,
            image=self._image,
            network="bridge" if profile.network.enabled else "none",
            memory=(
                f"{profile.process.memory_bytes}b"
                if profile.process.memory_bytes is not None
                else self._memory
            ),
            cpus=(
                profile.process.cpu_count if profile.process.cpu_count is not None else self._cpus
            ),
            pids=(
                profile.process.pids_limit if profile.process.pids_limit is not None else self._pids
            ),
            runtime=self._runtime,
            environment=build_sandbox_environment(profile),
            uid=profile.process.run_as_uid,
            gid=profile.process.run_as_gid,
        )
        try:
            await sandbox.__aenter__()
        except Exception as exc:
            raise SandboxUnavailableError(f"{self.name} failed to install boundary: {exc}") from exc
        process_rules = ["cap-drop=ALL", "no-new-privileges", "readonly-rootfs"]
        if profile.process.timeout_seconds is not None:
            process_rules.append(f"timeout<={profile.process.timeout_seconds}s")
        if profile.process.memory_bytes is not None:
            process_rules.append(f"memory<={profile.process.memory_bytes}")
        if profile.process.cpu_count is not None:
            process_rules.append(f"cpus<={profile.process.cpu_count}")
        if profile.process.pids_limit is not None:
            process_rules.append(f"pids<={profile.process.pids_limit}")
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend=self.name,
            backend_version=f"image={self._image};runtime={self._runtime}",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
            filesystem_rules=(
                f"{self._cwd} -> /workspace:rw",
                "/workspace/.git:deny-write",
                "/workspace/.codex:deny-write",
                "/workspace/.agents:deny-write",
            ),
            network_rules=("none" if not profile.network.enabled else "bridge",),
            environment_rules=(profile.environment.inherit.value,),
            process_rules=tuple(process_rules),
            unsupported_features=("file_tools", "external_tools"),
        )
        return DockerCommandSession(
            sandbox=sandbox,
            boundary=boundary,
            default_timeout=profile.process.timeout_seconds,
        )


def _bounded_timeout(requested: float | None, policy_limit: float | None) -> float | None:
    if requested is None:
        return policy_limit
    if policy_limit is None:
        return requested
    return min(requested, policy_limit)
