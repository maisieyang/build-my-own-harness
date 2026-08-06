"""Contracts for compiling policy intent into verified runtime facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, TypeAlias

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.permissions.profile import RuntimePermissionProfile


class ExecutionEffect(str, Enum):
    COMMAND = "command"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_SEARCH = "file_search"
    NETWORK = "network"
    EXTERNAL_TOOL = "external_tool"


class BoundaryVerification(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class EnforcedBoundary:
    """Backend-reported facts; never synthesized from configured intent."""

    profile_fingerprint: str
    backend: str
    backend_version: str
    covered_effects: tuple[ExecutionEffect, ...]
    verification: BoundaryVerification
    filesystem_rules: tuple[str, ...] = ()
    network_rules: tuple[str, ...] = ()
    environment_rules: tuple[str, ...] = ()
    process_rules: tuple[str, ...] = ()
    unsupported_features: tuple[str, ...] = ()

    @property
    def is_verified(self) -> bool:
        return self.verification is BoundaryVerification.VERIFIED

    def covers(self, effect: ExecutionEffect) -> bool:
        return self.is_verified and effect in self.covered_effects

    def normalized(self) -> dict[str, object]:
        """Return the complete, deterministic facts a resolver may inspect."""
        return {
            "profile_fingerprint": self.profile_fingerprint,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "covered_effects": sorted(effect.value for effect in self.covered_effects),
            "verification": self.verification.value,
            "filesystem_rules": sorted(self.filesystem_rules),
            "network_rules": sorted(self.network_rules),
            "environment_rules": sorted(self.environment_rules),
            "process_rules": sorted(self.process_rules),
            "unsupported_features": sorted(self.unsupported_features),
        }

    @property
    def fingerprint(self) -> str:
        """Stable identity of the facts the backend actually enforced."""
        payload = self.normalized()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def backend_fingerprint(self) -> str:
        """Stable identity of the sandbox implementation enforcing the facts."""
        encoded = json.dumps(
            {"backend": self.backend, "backend_version": self.backend_version},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class BackendSupport:
    backend: str
    supported: bool
    unsupported_features: tuple[str, ...] = ()
    reason: str | None = None

    @classmethod
    def available(cls, *, backend: str) -> BackendSupport:
        return cls(backend=backend, supported=True)

    @classmethod
    def unsupported(
        cls,
        *,
        backend: str,
        features: tuple[str, ...],
        reason: str,
    ) -> BackendSupport:
        return cls(
            backend=backend,
            supported=False,
            unsupported_features=tuple(sorted(set(features))),
            reason=reason,
        )


@dataclass(frozen=True)
class CommandOperation:
    command: str
    cwd: Path
    timeout: float | None = None

    @property
    def required_effect(self) -> ExecutionEffect:
        return ExecutionEffect.COMMAND


@dataclass(frozen=True)
class FileReadOperation:
    path: Path
    offset: int | None = None
    limit: int | None = None

    @property
    def required_effect(self) -> ExecutionEffect:
        return ExecutionEffect.FILE_READ


@dataclass(frozen=True)
class FileWriteOperation:
    path: Path
    content: str

    @property
    def required_effect(self) -> ExecutionEffect:
        return ExecutionEffect.FILE_WRITE


@dataclass(frozen=True)
class FileEditOperation:
    path: Path
    old_str: str
    new_str: str
    replace_all: bool = False

    @property
    def required_effect(self) -> ExecutionEffect:
        return ExecutionEffect.FILE_WRITE


@dataclass(frozen=True)
class FileSearchOperation:
    pattern: str
    path: Path
    glob: str | None = None
    ignore_case: bool = False
    hidden: bool = False
    line_cap: int = 200

    @property
    def required_effect(self) -> ExecutionEffect:
        return ExecutionEffect.FILE_SEARCH


class DataPlaneOperation(Protocol):
    """Marker protocol for operations accepted by a sandbox session."""

    @property
    def required_effect(self) -> ExecutionEffect: ...


@dataclass(frozen=True)
class ProcessCompleted:
    output: str
    exit_code: int


@dataclass(frozen=True)
class OperationCompleted:
    output: str
    metadata: dict[str, object]
    is_error: bool = False


@dataclass(frozen=True)
class TimedOut:
    output: str = ""


@dataclass(frozen=True)
class ExecutionFailed:
    reason: str


@dataclass(frozen=True)
class BoundaryViolation:
    dimension: str
    requested: str
    evidence: str
    hard_deny: bool = False


ExecutionResult: TypeAlias = (
    ProcessCompleted | OperationCompleted | TimedOut | ExecutionFailed | BoundaryViolation
)


class SandboxUnavailableError(RuntimeError):
    """The selected backend could not install its requested boundary."""


class SandboxSession(Protocol):
    @property
    def boundary(self) -> EnforcedBoundary: ...

    async def execute(self, operation: DataPlaneOperation) -> ExecutionResult: ...

    async def close(self) -> None: ...


class SandboxBackend(Protocol):
    def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport: ...

    async def open(self, profile: RuntimePermissionProfile) -> SandboxSession: ...
