"""Canonical intent model for the session runtime boundary.

The profile says what should be allowed.  It is deliberately separate from
``EnforcedBoundary``, which records what a backend actually installed and
verified.  Permission resolution must consume the latter.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FilesystemAccess(str, Enum):
    READ = "read"
    WRITE = "write"
    DENY_READ = "deny_read"
    DENY_WRITE = "deny_write"
    DENY = "deny"


class FilesystemScope(str, Enum):
    """Whether a filesystem rule targets one path or an entire subtree."""

    EXACT = "exact"
    SUBTREE = "subtree"


def _normalize_policy_path(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized == "":
        raise ValueError("filesystem rule path cannot be empty")
    return normalized


class FilesystemRule(_FrozenModel):
    path: str = Field(min_length=1)
    access: FilesystemAccess
    scope: FilesystemScope = FilesystemScope.SUBTREE

    def normalized_path(self) -> str:
        return _normalize_policy_path(self.path)


class FilesystemPolicy(_FrozenModel):
    rules: tuple[FilesystemRule, ...] = ()
    protect_symlinks: bool = True

    @model_validator(mode="after")
    def reject_conflicting_rules(self) -> FilesystemPolicy:
        seen: dict[tuple[str, FilesystemScope], FilesystemAccess] = {}
        for rule in self.rules:
            path = rule.normalized_path()
            key = (path, rule.scope)
            previous = seen.get(key)
            if previous is not None and previous is not rule.access:
                raise ValueError(
                    f"conflicting filesystem rules for {path!r} ({rule.scope.value}): "
                    f"{previous.value} and {rule.access.value}"
                )
            seen[key] = rule.access
        return self


class NetworkPolicy(_FrozenModel):
    enabled: bool = False
    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
    allow_loopback: bool = False
    allow_private: bool = False
    allow_link_local: bool = False
    allow_unix_sockets: tuple[str, ...] = ()

    @model_validator(mode="after")
    def disabled_has_no_allow_rules(self) -> NetworkPolicy:
        if not self.enabled and (
            self.allow_domains
            or self.allow_loopback
            or self.allow_private
            or self.allow_link_local
            or self.allow_unix_sockets
        ):
            raise ValueError("network is disabled but allow rules were configured")
        for raw_domain in (*self.allow_domains, *self.deny_domains):
            domain = raw_domain.removeprefix("*.").rstrip(".")
            if not domain or any(character in raw_domain for character in "/:@ ") or ".." in domain:
                raise ValueError(f"invalid network domain rule: {raw_domain!r}")
            try:
                ascii_domain = domain.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError(f"invalid network domain rule: {raw_domain!r}") from exc
            if any(not label or len(label) > 63 for label in ascii_domain.split(".")):
                raise ValueError(f"invalid network domain rule: {raw_domain!r}")
        for socket_path in self.allow_unix_sockets:
            if not posixpath.isabs(socket_path):
                raise ValueError("network Unix socket paths must be absolute")
        return self


class EnvironmentInheritance(str, Enum):
    NONE = "none"
    MINIMAL = "minimal"
    ALL = "all"


class EnvironmentPolicy(_FrozenModel):
    inherit: EnvironmentInheritance = EnvironmentInheritance.MINIMAL
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    set_values: dict[str, str] = Field(default_factory=dict)
    exclude_credential_patterns: bool = True

    @model_validator(mode="after")
    def include_and_exclude_are_disjoint(self) -> EnvironmentPolicy:
        overlap = set(self.include) & set(self.exclude)
        if overlap:
            joined = ", ".join(sorted(overlap))
            raise ValueError(f"environment variables both included and excluded: {joined}")
        return self


class ProcessPolicy(_FrozenModel):
    login_shell: bool = False
    run_as_uid: int | None = Field(default=None, ge=0)
    run_as_gid: int | None = Field(default=None, ge=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    memory_bytes: int | None = Field(default=None, gt=0)
    cpu_count: float | None = Field(default=None, gt=0)
    pids_limit: int | None = Field(default=None, gt=0)
    no_new_privileges: bool = True


class ExternalToolMode(str, Enum):
    DENY = "deny"
    ASK = "ask"
    ALLOW = "allow"


class ExternalToolPolicy(_FrozenModel):
    mcp: ExternalToolMode = ExternalToolMode.ASK
    web: ExternalToolMode = ExternalToolMode.ASK
    browser: ExternalToolMode = ExternalToolMode.ASK
    computer_use: ExternalToolMode = ExternalToolMode.ASK


def _sorted_unique(values: tuple[str, ...], *, lower: bool = False) -> list[str]:
    normalized = (value.lower() if lower else value for value in values)
    return sorted(set(normalized))


class RuntimePermissionProfile(_FrozenModel):
    """One canonical source of configured permission intent."""

    name: str = Field(min_length=1)
    filesystem: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    environment: EnvironmentPolicy = Field(default_factory=EnvironmentPolicy)
    process: ProcessPolicy = Field(default_factory=ProcessPolicy)
    external_tools: ExternalToolPolicy = Field(default_factory=ExternalToolPolicy)

    def normalized(self) -> dict[str, Any]:
        """Return deterministic, order-insensitive policy data."""
        data = self.model_dump(mode="json")
        normalized_rules = {
            (rule.normalized_path(), rule.access.value, rule.scope.value)
            for rule in self.filesystem.rules
        }
        data["filesystem"]["rules"] = [
            {"path": path, "access": access, "scope": scope}
            for path, access, scope in sorted(normalized_rules)
        ]
        data["network"]["allow_domains"] = _sorted_unique(self.network.allow_domains, lower=True)
        data["network"]["deny_domains"] = _sorted_unique(self.network.deny_domains, lower=True)
        data["network"]["allow_unix_sockets"] = _sorted_unique(self.network.allow_unix_sockets)
        data["environment"]["include"] = _sorted_unique(self.environment.include)
        data["environment"]["exclude"] = _sorted_unique(self.environment.exclude)
        data["environment"]["set_values"] = dict(sorted(self.environment.set_values.items()))
        return data

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.normalized(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


def workspace_runtime_profile() -> RuntimePermissionProfile:
    """Codex-style local posture: workspace write, protected control paths,
    default-deny network, minimal environment, and bounded child processes."""
    return RuntimePermissionProfile(
        name="workspace",
        filesystem=FilesystemPolicy(
            rules=(
                FilesystemRule(path=".", access=FilesystemAccess.WRITE),
                FilesystemRule(path=".git", access=FilesystemAccess.DENY_WRITE),
                FilesystemRule(path=".codex", access=FilesystemAccess.DENY_WRITE),
                FilesystemRule(path=".agents", access=FilesystemAccess.DENY_WRITE),
            )
        ),
        process=ProcessPolicy(timeout_seconds=600.0),
    )
