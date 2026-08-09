"""``Bundle`` dataclass + ``parse_bundle`` frontmatter parser — P5d-T1.

Per ``decisions/17-phase-5d-boundary.md`` D19.1 / D19.2:a bundle is a
markdown file with YAML frontmatter declaring up to 3 cross-layer
overrides. All override fields are optional — a bundle with only
``system_prompt`` is valid.

::

    ---
    name: code-review
    description: Read-only code review mode with audit logging
    system_prompt: |
      You are a code reviewer. Focus on correctness, readability, security.
      Never modify files.
    tools:
      whitelist: [Read, Grep, LoadSkill]
    hooks:
      - audit_log
      - deny_writes
    ---

P8 refactor: the duplicated outer scaffolding lives in
:mod:`openharness.markdown_store`. This module keeps the
:class:`Bundle` dataclass + the bundle-specific 3-layer field
extraction only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.markdown_store import NAME_PATTERN, read_frontmatter_dict
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("bundles")


@dataclass(frozen=True)
class Bundle:
    """A 3-layer composition unit discovered from the filesystem.

    Field semantics(per D19.1):

    - ``system_prompt``:override ``QueryContext.system_prompt`` (Layer 1)
    - ``tools_whitelist``:if non-empty,wrap registry to expose only
      these tool names (Layer 2)
    - ``hook_names``:names of hooks from ``BUILTIN_HOOKS`` to register
      on this query (Layer 3)
    """

    name: str
    description: str
    system_prompt: str | None
    tools_whitelist: tuple[str, ...] | None
    hook_names: tuple[str, ...]
    source_path: Path

    def __post_init__(self) -> None:
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid bundle name {self.name!r}:must match "
                f"{NAME_PATTERN.pattern}"
                " (alphanumeric + ``_-``, starts with letter)"
            )
        if not self.description.strip():
            raise ValueError(f"bundle {self.name!r}:description must be non-empty")


def parse_bundle(path: Path) -> Bundle | None:
    """Read a markdown file with YAML frontmatter and build a :class:`Bundle`.

    Returns ``None`` and emits a warning log on any error(file read,
    YAML parse, missing required field, invalid name, wrong field
    types). Same never-raise discipline as ``parse_skill`` /
    ``parse_command``.
    """
    parsed, _body = read_frontmatter_dict(path, logger_name="bundle")
    if parsed is None:
        return None

    name = parsed.get("name")
    description = parsed.get("description")
    if not isinstance(name, str) or not name:
        _logger.warning("bundle_missing_name", source_path=str(path))
        return None
    if not isinstance(description, str) or not description.strip():
        _logger.warning("bundle_missing_description", source_path=str(path), name=name)
        return None

    # Layer 1: optional system_prompt (string or absent).
    sp_raw = parsed.get("system_prompt")
    if sp_raw is not None and not isinstance(sp_raw, str):
        _logger.warning("bundle_invalid_system_prompt", source_path=str(path), name=name)
        return None
    system_prompt = sp_raw if isinstance(sp_raw, str) else None

    # Layer 2: optional tools.whitelist (list of strings).
    tools_block = parsed.get("tools")
    whitelist: tuple[str, ...] | None = None
    if tools_block is not None:
        if not isinstance(tools_block, dict):
            _logger.warning("bundle_invalid_tools_block", source_path=str(path), name=name)
            return None
        wl_raw = tools_block.get("whitelist")
        if wl_raw is not None:
            if not isinstance(wl_raw, list) or not all(isinstance(t, str) for t in wl_raw):
                _logger.warning(
                    "bundle_invalid_tools_whitelist",
                    source_path=str(path),
                    name=name,
                )
                return None
            whitelist = tuple(wl_raw)

    # Removed authorization overlays must fail visibly. Ignoring this field
    # would silently widen a bundle that its author believed was constrained.
    if "deny_paths" in parsed:
        _logger.warning(
            "bundle_legacy_deny_paths",
            source_path=str(path),
            name=name,
            replacement="permission_profile.filesystem.rules",
        )
        return None

    # Layer 3: optional hooks (list of strings).
    hooks_raw = parsed.get("hooks", [])
    if not isinstance(hooks_raw, list) or not all(isinstance(h, str) for h in hooks_raw):
        _logger.warning("bundle_invalid_hooks", source_path=str(path), name=name)
        return None
    hook_names = tuple(hooks_raw)

    try:
        return Bundle(
            name=name,
            description=description,
            system_prompt=system_prompt,
            tools_whitelist=whitelist,
            hook_names=hook_names,
            source_path=path,
        )
    except ValueError as exc:
        _logger.warning(
            "bundle_validation_failed", source_path=str(path), name=name, error=str(exc)
        )
        return None
