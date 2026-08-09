"""Fail-closed translation helpers for the retired permission surfaces.

The translator exists to make a migration explicit and reviewable. It only
accepts legacy rules whose match set is exactly representable by the canonical
profile. It never participates in runtime authorization and never turns a
legacy ASK or command-prefix allow into authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from openharness.permissions.profile import (
    ExternalToolPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    FilesystemScope,
    NetworkPolicy,
    RuntimePermissionProfile,
)


class LegacyPermissionMigrationError(ValueError):
    """A legacy permission input cannot be translated without widening it."""


@dataclass(frozen=True)
class LegacyPermissionInputs:
    """Typed snapshot of the legacy configuration accepted by Release A."""

    deny_paths: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    sandbox_network_policy: NetworkPolicy | None = None
    sandbox_external_tool_policy: ExternalToolPolicy | None = None


@dataclass(frozen=True)
class LegacyPermissionTranslation:
    """Canonical result plus user-facing migration warnings."""

    profile: RuntimePermissionProfile
    warnings: tuple[str, ...]


_RULE = re.compile(r"^(?P<tool>[A-Za-z][A-Za-z0-9_-]*)\((?P<path>.*)\)$")
_GLOB_META = frozenset("*?[")
_READ_TOOLS = frozenset({"Read", "Grep"})
_WRITE_TOOLS = frozenset({"Write", "Edit"})


def _path_and_scope(raw: str, *, source: str) -> tuple[str, FilesystemScope]:
    value = raw.strip()
    if not value:
        raise LegacyPermissionMigrationError(f"cannot migrate empty {source} path")
    if value.endswith("/**") and not any(char in value[:-3] for char in _GLOB_META):
        root = value[:-3]
        if not root:
            raise LegacyPermissionMigrationError(f"cannot migrate root-wide {source} rule")
        return root, FilesystemScope.SUBTREE
    if any(char in value for char in _GLOB_META):
        raise LegacyPermissionMigrationError(
            f"cannot migrate {source} glob {value!r}; use canonical exact/subtree rules"
        )
    return value, FilesystemScope.EXACT


def _translate_tool_rule(spec: str, *, deny: bool) -> FilesystemRule:
    match = _RULE.fullmatch(spec.strip())
    if match is None:
        raise LegacyPermissionMigrationError(
            f"cannot migrate legacy rule {spec!r}; expected Tool(path)"
        )
    tool = match.group("tool")
    if tool not in _READ_TOOLS | _WRITE_TOOLS:
        raise LegacyPermissionMigrationError(
            f"cannot migrate {tool} rule {spec!r}; command/control rules are not containment"
        )
    path, scope = _path_and_scope(match.group("path"), source=spec)
    if tool in _READ_TOOLS:
        access = FilesystemAccess.DENY_READ if deny else FilesystemAccess.READ
    else:
        access = FilesystemAccess.DENY_WRITE if deny else FilesystemAccess.WRITE
    return FilesystemRule(path=path, access=access, scope=scope)


def translate_legacy_permission_config(
    base: RuntimePermissionProfile,
    legacy: LegacyPermissionInputs,
) -> LegacyPermissionTranslation:
    """Translate only legacy inputs with an equivalent canonical meaning.

    Any ASK, command rule, general glob, conflict, or validation failure is an
    explicit migration error. A failure never returns a partially widened
    profile.
    """
    if legacy.ask:
        raise LegacyPermissionMigrationError(
            "cannot migrate permissions.ask; ungranted effects use exact review"
        )

    additions = [
        FilesystemRule(path=path, access=FilesystemAccess.DENY, scope=scope)
        for raw in legacy.deny_paths
        for path, scope in (_path_and_scope(raw, source="deny_paths"),)
    ]
    additions.extend(_translate_tool_rule(spec, deny=False) for spec in legacy.allow)
    additions.extend(_translate_tool_rule(spec, deny=True) for spec in legacy.deny)

    try:
        filesystem = FilesystemPolicy(
            rules=(*base.filesystem.rules, *additions),
            protect_symlinks=base.filesystem.protect_symlinks,
        )
        profile = base.model_copy(
            update={
                "filesystem": filesystem,
                "network": legacy.sandbox_network_policy or base.network,
                "external_tools": (legacy.sandbox_external_tool_policy or base.external_tools),
            }
        )
        # model_copy does not revalidate nested updates in Pydantic v2.
        profile = RuntimePermissionProfile.model_validate(profile.model_dump())
    except ValidationError as exc:
        raise LegacyPermissionMigrationError(
            f"cannot migrate conflicting legacy permission rules: {exc}"
        ) from exc

    warnings = tuple(
        ["legacy permission configuration was translated; write permission_profile instead"]
        if additions
        or legacy.sandbox_network_policy is not None
        or legacy.sandbox_external_tool_policy is not None
        else []
    )
    return LegacyPermissionTranslation(profile=profile, warnings=warnings)
