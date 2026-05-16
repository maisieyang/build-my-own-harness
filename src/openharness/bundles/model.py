"""``Bundle`` dataclass + ``parse_bundle`` frontmatter parser — P5d-T1.

Per ``decisions/17-phase-5d-boundary.md`` D19.1 / D19.2:a bundle is a
markdown file with YAML frontmatter declaring up to 4 cross-layer
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
    deny_paths:
      - secrets/**
      - "*.env"
    hooks:
      - audit_log
      - deny_writes
    ---

Design choices(mirror ``commands/model.py`` + ``skills/model.py``):

- **Frozen dataclass**:bundles read-only after construction
- **``name`` regex matches** ``Skill.name`` / ``Command.name`` /
  ``McpServerConfig.name`` — same safe-identifier discipline
  (consumed as filesystem name + slash command ``mode:`` value)
- **``parse_bundle`` returns ``Bundle | None``, NEVER raises**:
  bootstrap discipline (same as ``parse_skill`` / ``parse_command``).
  One bad bundle must not crash other bundles' load.
- **All override fields optional**:bundle declaring only
  ``system_prompt`` produces a context modification limited to Layer
  1. Layer 2/3 overrides folded gracefully when absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

# Same regex as McpServerConfig / Skill / Command — uniform safe-
# identifier rules across user-supplied names that flow into argument
# positions or filesystem paths.
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_FRONTMATTER_FENCE = "---"

_logger = get_logger("bundles")


@dataclass(frozen=True)
class Bundle:
    """A 4-layer composition unit discovered from the filesystem.

    Field semantics(per D19.1):

    - ``system_prompt``:override ``QueryContext.system_prompt`` (Layer 1)
    - ``tools_whitelist``:if non-empty,wrap registry to expose only
      these tool names (Layer 2)
    - ``deny_paths``:additional patterns merged into ``Settings.deny_paths``
      for AuthZ Tier 2 (Layer 3)
    - ``hook_names``:names of hooks from ``BUILTIN_HOOKS`` to register
      on this query (Layer 3)
    """

    name: str
    description: str
    system_prompt: str | None
    tools_whitelist: tuple[str, ...] | None
    deny_paths: tuple[str, ...]
    hook_names: tuple[str, ...]
    source_path: Path

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid bundle name {self.name!r}:must match "
                f"{_NAME_PATTERN.pattern}"
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
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("bundle_read_failed", source_path=str(path), error=str(exc))
        return None

    frontmatter, _body = _split_frontmatter(text)
    if frontmatter is None:
        _logger.warning("bundle_missing_frontmatter", source_path=str(path))
        return None

    try:
        parsed: Any = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        _logger.warning("bundle_yaml_parse_failed", source_path=str(path), error=str(exc))
        return None

    if not isinstance(parsed, dict):
        _logger.warning(
            "bundle_frontmatter_not_mapping",
            source_path=str(path),
            got=type(parsed).__name__,
        )
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

    # Layer 3a: optional deny_paths (list of strings).
    dp_raw = parsed.get("deny_paths", [])
    if not isinstance(dp_raw, list) or not all(isinstance(p, str) for p in dp_raw):
        _logger.warning("bundle_invalid_deny_paths", source_path=str(path), name=name)
        return None
    deny_paths = tuple(dp_raw)

    # Layer 3b: optional hooks (list of strings).
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
            deny_paths=deny_paths,
            hook_names=hook_names,
            source_path=path,
        )
    except ValueError as exc:
        _logger.warning(
            "bundle_validation_failed", source_path=str(path), name=name, error=str(exc)
        )
        return None


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Same shape as ``skills/model._split_frontmatter`` and ``commands/
    model._split_frontmatter`` — would be a candidate for Phase 8
    polish ``markdown_store/`` extraction.
    """
    if not text.startswith(_FRONTMATTER_FENCE + "\n"):
        return None, text
    after_open = text[len(_FRONTMATTER_FENCE) + 1 :]
    fence_with_newline = "\n" + _FRONTMATTER_FENCE + "\n"
    idx = after_open.find(fence_with_newline)
    if idx != -1:
        return after_open[:idx], after_open[idx + len(fence_with_newline) :]
    closing_at_eof = "\n" + _FRONTMATTER_FENCE
    if after_open.endswith(closing_at_eof):
        return after_open[: -len(closing_at_eof)], ""
    return None, text


# Suppress unused-import warning — `field` reserved for future
# default_factory usage if we add list-type fields with defaults.
_ = field
