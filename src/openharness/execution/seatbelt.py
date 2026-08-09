"""Native macOS Seatbelt backend for model-controlled local commands."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import os
import secrets
import shutil
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from openharness.execution.boundary import (
    BackendSupport,
    BoundaryVerification,
    BoundaryViolation,
    CommandOperation,
    DataPlaneOperation,
    EnforcedBoundary,
    ExecutionEffect,
    ExecutionFailed,
    FileEditOperation,
    FileReadOperation,
    FileSearchOperation,
    FileWriteOperation,
    OperationCompleted,
    ProcessCompleted,
    SandboxUnavailableError,
    TimedOut,
)
from openharness.execution.network_proxy import ManagedNetworkProxy
from openharness.permissions.profile import (
    EnvironmentInheritance,
    FilesystemAccess,
    FilesystemRule,
    FilesystemScope,
)

if TYPE_CHECKING:
    from openharness.execution.network_proxy import NetworkProxySession
    from openharness.permissions.profile import RuntimePermissionProfile


_RUNTIME_SYSCTL_READ_RULES = (
    ("sysctl-name", "hw.activecpu"),
    ("sysctl-name", "hw.logicalcpu"),
    ("sysctl-name", "hw.memsize"),
    ("sysctl-name", "hw.ncpu"),
    ("sysctl-name", "hw.pagesize_compat"),
    ("sysctl-name", "kern.argmax"),
    ("sysctl-name", "kern.hostname"),
    ("sysctl-name", "kern.osrelease"),
    ("sysctl-name", "kern.ostype"),
    ("sysctl-name", "kern.osversion"),
    ("sysctl-name", "kern.secure_kernel"),
    ("sysctl-name", "kern.usrstack64"),
    ("sysctl-name-prefix", "hw.optional."),
    ("sysctl-name-prefix", "kern.proc.pid."),
    ("sysctl-name-prefix", "sysctl.proc_cputype"),
)
_RUNTIME_WRITABLE_PATHS = (Path("/dev/null"),)


def _seatbelt_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _absolute_policy_path(raw: str, cwd: Path) -> Path:
    expanded = os.path.expanduser(raw)
    path = cwd / expanded if not os.path.isabs(expanded) else type(cwd)(expanded)
    return path.resolve(strict=False)


def _filesystem_filter(rule: FilesystemRule, cwd: Path) -> str:
    selector = "literal" if rule.scope is FilesystemScope.EXACT else "subpath"
    path = _seatbelt_string(str(_absolute_policy_path(rule.path, cwd)))
    return f'({selector} "{path}")'


def _runtime_read_rules() -> tuple[FilesystemRule, ...]:
    """Host paths needed to launch the trusted toolchain under Seatbelt.

    These paths are an explicit part of the backend's effective read boundary.
    Keep them limited to executable/library trees and individual runtime files;
    in particular, do not expose broad configuration or device directories.
    """
    subtree_candidates = (
        Path("/System/Library"),
        Path("/usr/bin"),
        Path("/usr/lib"),
        Path("/usr/libexec"),
        Path("/usr/share"),
        Path("/bin"),
        Path("/sbin"),
        Path("/private/var/select"),
        Path("/private/var/db/dyld"),
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(__file__).resolve().parent,
    )
    exact_candidates = {
        Path("/dev/null"),
        Path("/dev/random"),
        Path("/dev/urandom"),
        Path("/dev/tty"),
        Path("/dev/ptmx"),
        Path(sys.executable),
    }
    for executable in ("rg", "uv"):
        found = shutil.which(executable)
        if found is not None:
            exact_candidates.add(Path(found))
            exact_candidates.add(Path(found).resolve(strict=False))
    rules = {
        FilesystemRule(
            path=str(path.resolve(strict=False)),
            access=FilesystemAccess.READ,
            scope=FilesystemScope.SUBTREE,
        )
        for path in subtree_candidates
    }
    rules.update(
        FilesystemRule(
            path=str(path),
            access=FilesystemAccess.READ,
            scope=FilesystemScope.EXACT,
        )
        for path in exact_candidates
    )
    return tuple(sorted(rules, key=lambda rule: (rule.path, rule.scope.value)))


def compile_seatbelt_profile(
    profile: RuntimePermissionProfile,
    *,
    cwd: Path,
    network_proxy_port: int | None = None,
) -> str:
    """Lower the filesystem/network subset into deterministic SBPL."""
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow process-info* (target same-sandbox))",
        "(allow ipc-posix-sem)",
        "(allow pseudo-tty)",
    ]
    # Common read-only kernel queries needed by the dynamic loader and
    # language runtimes. These grant no filesystem, network, or cross-sandbox
    # process authority.
    lines.extend(
        f'(allow sysctl-read ({selector} "{_seatbelt_string(value)}"))'
        for selector, value in _RUNTIME_SYSCTL_READ_RULES
    )
    readable_rules = tuple(
        rule
        for rule in profile.filesystem.rules
        if rule.access in (FilesystemAccess.READ, FilesystemAccess.WRITE)
    )
    readable_paths = {str(_absolute_policy_path(rule.path, cwd)) for rule in readable_rules}
    runtime_read_rules = _runtime_read_rules()
    runtime_read_paths = {rule.path for rule in runtime_read_rules}
    traversal_sources = readable_paths | runtime_read_paths
    traversal_paths = {
        str(parent)
        for raw_path in traversal_sources
        for parent in Path(raw_path).parents
        if str(parent) not in traversal_sources
    }
    for path in sorted(traversal_paths):
        lines.append(f'(allow file-read* (literal "{_seatbelt_string(path)}"))')
    for rule in runtime_read_rules:
        lines.append(f"(allow file-read* {_filesystem_filter(rule, cwd)})")
    for rule in sorted(readable_rules, key=lambda item: (item.normalized_path(), item.scope.value)):
        lines.append(f"(allow file-read* {_filesystem_filter(rule, cwd)})")
    writable_rules = [
        rule for rule in profile.filesystem.rules if rule.access is FilesystemAccess.WRITE
    ]
    runtime_writable_paths = tuple(_seatbelt_string(str(path)) for path in _RUNTIME_WRITABLE_PATHS)
    write_exclusions = [
        *(f"(require-not {_filesystem_filter(rule, cwd)})" for rule in writable_rules),
        *(f'(require-not (literal "{path}"))' for path in runtime_writable_paths),
    ]
    exclusions = " ".join(write_exclusions)
    lines.append(f"(deny file-write* (require-all {exclusions}))")
    for path in runtime_writable_paths:
        lines.append(f'(allow file-write* (literal "{path}"))')
    for rule in sorted(
        profile.filesystem.rules,
        key=lambda item: (item.normalized_path(), item.access.value, item.scope.value),
    ):
        path_filter = _filesystem_filter(rule, cwd)
        if rule.access is FilesystemAccess.READ:
            lines.append(f"(allow file-read* {path_filter})")
        elif rule.access is FilesystemAccess.WRITE:
            lines.append(f"(allow file-read* {path_filter})")
            lines.append(f"(allow file-write* {path_filter})")
        elif rule.access is FilesystemAccess.DENY:
            lines.append(f"(deny file-read* file-write* {path_filter})")
        elif rule.access is FilesystemAccess.DENY_READ:
            lines.append(f"(deny file-read* {path_filter})")
        elif rule.access is FilesystemAccess.DENY_WRITE:
            lines.append(f"(deny file-write* {path_filter})")
    if not profile.network.enabled:
        lines.append("(deny network*)")
    elif network_proxy_port is not None:
        lines.append("(deny network*)")
        lines.append(f'(allow network-outbound (remote ip "localhost:{network_proxy_port}"))')
        for socket_path in profile.network.allow_unix_sockets:
            escaped_socket = _seatbelt_string(socket_path)
            lines.append(
                f'(allow network-outbound (remote unix-socket (subpath "{escaped_socket}")))'
            )
    return "\n".join(lines) + "\n"


_MINIMAL_ENVIRONMENT = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TMPDIR")
_CREDENTIAL_PATTERNS = (
    "*_API_KEY",
    "*_SECRET",
    "*_TOKEN",
    "*_PASSWORD",
    "*_PRIVATE_KEY",
    "*_ACCESS_KEY",
    "*_CREDENTIAL*",
    "AWS_*",
    "GITHUB_TOKEN",
    "SSH_AUTH_SOCK",
)
_PROXY_ENVIRONMENT_NAMES = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)
_MAX_RETAINED_OUTPUT_BYTES = 8 * 1024 * 1024
_OUTPUT_TRUNCATED_MARKER = b"[... output truncated; retained tail ...]\n"


async def _read_bounded_output(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int = _MAX_RETAINED_OUTPUT_BYTES,
) -> tuple[bytes, bool]:
    """Drain a pipe fully while retaining only its bounded tail."""
    retained = bytearray()
    truncated = False
    while chunk := await reader.read(64 * 1024):
        if len(chunk) >= max_bytes:
            had_retained = bool(retained)
            retained[:] = chunk[-max_bytes:]
            truncated = truncated or had_retained or len(chunk) > max_bytes
            continue
        overflow = len(retained) + len(chunk) - max_bytes
        if overflow > 0:
            del retained[:overflow]
            truncated = True
        retained.extend(chunk)
    return bytes(retained), truncated


async def _collect_process_output(
    process: asyncio.subprocess.Process,
) -> tuple[bytes, bool]:
    if isinstance(process.stdout, asyncio.StreamReader):
        output, truncated = await _read_bounded_output(process.stdout)
        await process.wait()
        return output, truncated
    output, _ = await process.communicate()
    return output, False


def build_sandbox_environment(profile: RuntimePermissionProfile) -> dict[str, str]:
    policy = profile.environment
    if policy.inherit is EnvironmentInheritance.ALL:
        environment = dict(os.environ)
    elif policy.inherit is EnvironmentInheritance.MINIMAL:
        environment = {
            name: os.environ[name] for name in _MINIMAL_ENVIRONMENT if name in os.environ
        }
    else:
        environment = {}
    for name in policy.include:
        if name in os.environ:
            environment[name] = os.environ[name]
    for name in policy.exclude:
        environment.pop(name, None)
    if policy.exclude_credential_patterns:
        for name in tuple(environment):
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in _CREDENTIAL_PATTERNS):
                environment.pop(name, None)
    environment.update(policy.set_values)
    for name in _PROXY_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    return environment


class SeatbeltSession:
    def __init__(
        self,
        *,
        executable: str,
        profile_text: str,
        environment: dict[str, str],
        boundary: EnforcedBoundary,
        boundary_root: Path,
        default_timeout: float | None = None,
        network_proxy: NetworkProxySession | None = None,
        hard_deny_rules: tuple[tuple[FilesystemAccess, Path, FilesystemScope], ...] = (),
    ) -> None:
        self._executable = executable
        self._profile_text = profile_text
        self._environment = environment
        self._boundary = boundary
        self._boundary_root = boundary_root
        self._default_timeout = default_timeout
        self._network_proxy = network_proxy
        self._hard_deny_rules = hard_deny_rules
        self._closed = False

    @property
    def boundary(self) -> EnforcedBoundary:
        return self._boundary

    async def execute(
        self, operation: DataPlaneOperation
    ) -> ProcessCompleted | OperationCompleted | TimedOut | ExecutionFailed | BoundaryViolation:
        if not isinstance(operation, CommandOperation):
            return await self._execute_file_operation(operation)
        request_id: str | None = None
        environment = self._environment
        if self._network_proxy is not None:
            request_id = secrets.token_hex(16)
            proxy_url = self._network_proxy.url_for(request_id)
            environment = {
                **self._environment,
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "NO_PROXY": "",
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "no_proxy": "",
            }
        process = await asyncio.create_subprocess_exec(
            self._executable,
            "-p",
            self._profile_text,
            "/bin/sh",
            "-c",
            operation.command,
            cwd=operation.cwd,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        timeout = _bounded_timeout(operation.timeout, self._default_timeout)
        try:
            output, truncated = await asyncio.wait_for(
                _collect_process_output(process),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            with _ignore_process_lookup():
                os.killpg(process.pid, signal.SIGKILL)
            output, truncated = await _collect_process_output(process)
            result: ProcessCompleted | TimedOut = TimedOut(
                output=_render_bounded_output(output, truncated)
            )
        else:
            result = ProcessCompleted(
                output=_render_bounded_output(output, truncated),
                exit_code=int(process.returncode or 0),
            )
        if request_id is not None and self._network_proxy is not None:
            violations = self._network_proxy.violations_for(request_id)
            if violations:
                return violations[0]
        return result

    async def _execute_file_operation(
        self, operation: DataPlaneOperation
    ) -> OperationCompleted | TimedOut | ExecutionFailed | BoundaryViolation:
        request = _worker_request(operation)
        if request is None:
            return ExecutionFailed(reason="Seatbelt session received an unknown operation")
        worker = Path(__file__).with_name("worker.py")
        process = await asyncio.create_subprocess_exec(
            self._executable,
            "-p",
            self._profile_text,
            sys.executable,
            str(worker),
            cwd=self._boundary_root,
            env=self._environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        request_bytes = json.dumps(request, ensure_ascii=False).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request_bytes),
                timeout=self._default_timeout,
            )
        except asyncio.TimeoutError:
            with _ignore_process_lookup():
                os.killpg(process.pid, signal.SIGKILL)
            await process.communicate()
            return TimedOut()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            return ExecutionFailed(reason=f"sandbox worker failed: {detail or process.returncode}")
        try:
            response = json.loads(stdout)
            metadata = dict(response["metadata"])
            raw_violation = metadata.get("boundary_violation")
            if isinstance(raw_violation, dict):
                dimension = raw_violation.get("dimension")
                requested = raw_violation.get("requested")
                evidence = raw_violation.get("evidence")
                hard_deny = raw_violation.get("hard_deny", False)
                if not (
                    isinstance(dimension, str)
                    and isinstance(requested, str)
                    and isinstance(evidence, str)
                    and isinstance(hard_deny, bool)
                ):
                    raise ValueError("invalid boundary violation payload")
                return BoundaryViolation(
                    dimension=dimension,
                    requested=requested,
                    evidence=evidence,
                    hard_deny=hard_deny
                    or _matches_hard_deny(
                        operation,
                        requested=requested,
                        rules=self._hard_deny_rules,
                    ),
                )
            return OperationCompleted(
                output=str(response["output"]),
                is_error=bool(response["is_error"]),
                metadata=metadata,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ExecutionFailed(reason=f"invalid sandbox worker response: {exc}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._network_proxy is not None:
            await self._network_proxy.close()


def _worker_request(operation: DataPlaneOperation) -> dict[str, object] | None:
    if isinstance(operation, FileReadOperation):
        return {
            "kind": "read",
            "path": str(operation.path),
            "offset": operation.offset,
            "limit": operation.limit,
        }
    if isinstance(operation, FileWriteOperation):
        return {"kind": "write", "path": str(operation.path), "content": operation.content}
    if isinstance(operation, FileEditOperation):
        request: dict[str, object] = {
            "kind": "edit",
            "path": str(operation.path),
            "old_str": operation.old_str,
            "new_str": operation.new_str,
            "replace_all": operation.replace_all,
        }
        if operation.temp_path is not None:
            request["temp_path"] = str(operation.temp_path)
        return request
    if isinstance(operation, FileSearchOperation):
        return {
            "kind": "search",
            "path": str(operation.path),
            "pattern": operation.pattern,
            "glob": operation.glob,
            "ignore_case": operation.ignore_case,
            "hidden": operation.hidden,
            "line_cap": operation.line_cap,
        }
    return None


def _render_bounded_output(output: bytes, truncated: bool) -> str:
    if truncated:
        output = _OUTPUT_TRUNCATED_MARKER + output
    return output.decode("utf-8", errors="replace")


class _ignore_process_lookup:
    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool:
        return exc_type is ProcessLookupError


class SeatbeltBackend:
    name = "macos-seatbelt"

    def __init__(self, *, cwd: Path, executable: str = "/usr/bin/sandbox-exec") -> None:
        self._cwd = cwd
        self._executable = executable

    def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
        unsupported: list[str] = []
        if sys.platform != "darwin":
            unsupported.append("platform.macos")
        if not os.path.isfile(self._executable) or not os.access(self._executable, os.X_OK):
            unsupported.append("sandbox-exec")
        if profile.process.run_as_uid is not None or profile.process.run_as_gid is not None:
            unsupported.append("process.identity")
        if profile.process.login_shell:
            unsupported.append("process.login_shell")
        if any(
            value is not None
            for value in (
                profile.process.memory_bytes,
                profile.process.cpu_count,
                profile.process.pids_limit,
            )
        ):
            unsupported.append("process.resources")
        if unsupported:
            return BackendSupport.unsupported(
                backend=self.name,
                features=tuple(unsupported),
                reason="Seatbelt cannot install every requested profile dimension",
            )
        return BackendSupport.available(backend=self.name)

    async def open(self, profile: RuntimePermissionProfile) -> SeatbeltSession:
        support = self.preflight(profile)
        if not support.supported:
            features = ", ".join(support.unsupported_features)
            raise SandboxUnavailableError(
                f"sandbox-exec cannot install profile features: {features}"
            )
        network_proxy: NetworkProxySession | None = None
        try:
            if profile.network.enabled:
                network_proxy = await ManagedNetworkProxy.open(profile.network)
            profile_text = compile_seatbelt_profile(
                profile,
                cwd=self._cwd,
                network_proxy_port=(network_proxy.port if network_proxy is not None else None),
            )
            environment = build_sandbox_environment(profile)
            probe = await asyncio.create_subprocess_exec(
                self._executable,
                "-p",
                profile_text,
                "/usr/bin/true",
                cwd=self._cwd,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await probe.communicate()
            if probe.returncode != 0:
                detail = output.decode("utf-8", errors="replace").strip()
                raise SandboxUnavailableError(
                    f"sandbox-exec rejected the compiled profile: {detail or probe.returncode}"
                )
        except Exception as exc:
            if network_proxy is not None:
                with contextlib.suppress(Exception):
                    await network_proxy.close()
            if isinstance(exc, SandboxUnavailableError):
                raise
            raise SandboxUnavailableError(
                f"sandbox-exec failed to install the managed boundary: {exc}"
            ) from exc
        filesystem_rules = tuple(
            f"{rule.access.value}:{rule.scope.value}:{_absolute_policy_path(rule.path, self._cwd)}"
            for rule in profile.filesystem.rules
        )
        runtime_read_rules = tuple(
            f"runtime_read:{rule.scope.value}:{rule.path}" for rule in _runtime_read_rules()
        )
        runtime_write_rules = tuple(f"runtime_write:{path}" for path in _RUNTIME_WRITABLE_PATHS)
        process_rules = [
            "deny-default",
            "non-login-shell",
            "child-process-sandbox-inheritance",
            "signal-and-process-info:target-same-sandbox",
            *(
                f"runtime_sysctl_read:{value}"
                if selector == "sysctl-name"
                else f"runtime_sysctl_read_prefix:{value}"
                for selector, value in _RUNTIME_SYSCTL_READ_RULES
            ),
        ]
        if profile.process.timeout_seconds is not None:
            process_rules.append(f"timeout<={profile.process.timeout_seconds}s")
        covered_effects = [
            ExecutionEffect.COMMAND,
            ExecutionEffect.FILE_READ,
            ExecutionEffect.FILE_WRITE,
            ExecutionEffect.FILE_SEARCH,
        ]
        if network_proxy is not None:
            covered_effects.append(ExecutionEffect.NETWORK)
        network_rules: tuple[str, ...]
        if network_proxy is None:
            network_rules = ("deny-all",)
        else:
            network_rules = (
                f"proxy:127.0.0.1:{network_proxy.port}",
                *(f"allow-domain:{domain}" for domain in profile.network.allow_domains),
                *(f"deny-domain:{domain}" for domain in profile.network.deny_domains),
                *(
                    f"allow-unix-socket:{socket_path}"
                    for socket_path in profile.network.allow_unix_sockets
                ),
                f"allow-loopback:{profile.network.allow_loopback}",
                f"allow-private:{profile.network.allow_private}",
                f"allow-link-local:{profile.network.allow_link_local}",
            )
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend=self.name,
            backend_version="sandbox-exec",
            covered_effects=tuple(covered_effects),
            verification=BoundaryVerification.VERIFIED,
            filesystem_rules=(
                *filesystem_rules,
                *runtime_read_rules,
                *runtime_write_rules,
            ),
            network_rules=network_rules,
            environment_rules=(profile.environment.inherit.value,),
            process_rules=tuple(process_rules),
            unsupported_features=("external_tools",),
        )
        return SeatbeltSession(
            executable=self._executable,
            profile_text=profile_text,
            environment=environment,
            boundary=boundary,
            boundary_root=self._cwd,
            default_timeout=profile.process.timeout_seconds,
            network_proxy=network_proxy,
            hard_deny_rules=tuple(
                (rule.access, _absolute_policy_path(rule.path, self._cwd), rule.scope)
                for rule in profile.filesystem.rules
                if rule.access
                in {
                    FilesystemAccess.DENY,
                    FilesystemAccess.DENY_READ,
                    FilesystemAccess.DENY_WRITE,
                }
            ),
        )


def _matches_hard_deny(
    operation: DataPlaneOperation,
    *,
    requested: str,
    rules: tuple[tuple[FilesystemAccess, Path, FilesystemScope], ...],
) -> bool:
    requested_path = Path(requested).resolve(strict=False)
    is_read = isinstance(operation, (FileReadOperation, FileSearchOperation))
    is_write = isinstance(operation, (FileWriteOperation, FileEditOperation))
    for access, root, scope in rules:
        applies = (
            access is FilesystemAccess.DENY
            or (is_read and access is FilesystemAccess.DENY_READ)
            or (is_write and access is FilesystemAccess.DENY_WRITE)
        )
        within_scope = requested_path == root or (
            scope is FilesystemScope.SUBTREE and requested_path.is_relative_to(root)
        )
        if applies and within_scope:
            return True
    return False


def _bounded_timeout(requested: float | None, policy_limit: float | None) -> float | None:
    if requested is None:
        return policy_limit
    if policy_limit is None:
        return requested
    return min(requested, policy_limit)
