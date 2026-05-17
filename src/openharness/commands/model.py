"""``Command`` dataclass + ``parse_command`` frontmatter parser — P5b-T1.

Per ``decisions/14`` C1 / C2: a slash command is a markdown file with a
YAML frontmatter block fenced by ``---``. Required fields are ``name``
and ``description``; other fields are tolerated (forward-compat — Phase
5d may add ``mode:`` / ``hooks:`` / ``permission:`` field semantics).

::

    ---
    name: review
    description: Review pending changes for correctness + security
    ---
    Please review the following changes:

    {args}

    Focus on edge cases and security implications.

Design choices(mirror ``skills/model.py``):

- **Frozen dataclass**:commands are read-only after construction. The
  store discovers them at bootstrap and never mutates.
- **``name`` validation regex matches ``Skill.name`` / ``McpServerConfig.name``**:
  same safe-identifier discipline; the value flows into a CLI argument
  position(``oh ask "/<name> args"``)so reject characters that would
  confuse the parser.
- **``parse_command`` returns ``Command | None``, NEVER raises**:
  bootstrap scans the entire directory;one malformed file must not
  prevent other commands from loading. Same never-raise contract as
  ``parse_skill``(see ``learnings/phase-5c-skills.md`` §3.4).
- **No ``version`` field**:commands are simpler than skills — they're
  templates,not curated expertise. Forward-compat: Phase 5d may add it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

# Same as ``Skill._NAME_PATTERN`` / ``McpServerConfig._NAME_PATTERN``.
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

_FRONTMATTER_FENCE = "---"

_logger = get_logger("commands")


@dataclass(frozen=True)
class Command:
    """A single slash command discovered from the filesystem.

    Constructed by :func:`parse_command`;the dataclass trusts its inputs
    (``__post_init__`` only re-asserts the regex)so tests can build
    fixtures without round-tripping through markdown.

    P5d-T4: ``mode`` is an optional reference to a ModeBundle by name. When
    set,``cli._run_ask`` loads the named bundle from
    :class:`~openharness.bundles.FilesystemBundleStore` and applies its
    overrides via :func:`~openharness.bundles.apply_bundle_to_context` —
    the bundle is what couples this slash command to the cross-layer
    composition (Layer 0 → 1/2/3 trigger).
    """

    name: str
    description: str
    body: str
    source_path: Path
    mode: str | None = None

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid command name {self.name!r}:must match "
                f"{_NAME_PATTERN.pattern}"
                " (alphanumeric + ``_-``, starts with letter)"
            )
        if not self.description.strip():
            raise ValueError(f"command {self.name!r}:description must be non-empty")
        if self.mode is not None and not _NAME_PATTERN.match(self.mode):
            raise ValueError(
                f"command {self.name!r}:invalid mode {self.mode!r} — must match "
                f"{_NAME_PATTERN.pattern}"
            )


def parse_command(path: Path) -> Command | None:
    """Read a markdown file with YAML frontmatter and build a :class:`Command`.

    Returns ``None`` and emits a warning log on any error(file read,
    YAML parse, missing required field, invalid name). The store's
    discovery loop treats ``None`` as "skip" so one bad command never
    prevents other commands from loading.

    Same never-raise discipline as :func:`openharness.skills.model.parse_skill`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning(
            "command_read_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None

    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        _logger.warning(
            "command_missing_frontmatter",
            source_path=str(path),
        )
        return None

    try:
        parsed: Any = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        _logger.warning(
            "command_yaml_parse_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None

    if not isinstance(parsed, dict):
        _logger.warning(
            "command_frontmatter_not_mapping",
            source_path=str(path),
            got=type(parsed).__name__,
        )
        return None

    name = parsed.get("name")
    description = parsed.get("description")

    if not isinstance(name, str) or not name:
        _logger.warning(
            "command_missing_name",
            source_path=str(path),
        )
        return None
    if not isinstance(description, str) or not description.strip():
        _logger.warning(
            "command_missing_description",
            source_path=str(path),
            name=name,
        )
        return None

    # P5d-T4: optional ``mode:`` field links a slash command to a ModeBundle.
    # Skip-not-fail: a mode value with the wrong type is logged + dropped
    # (the command still loads with mode=None); only invalid identifier
    # syntax is caught by ``Command.__post_init__`` ValueError below.
    mode_raw = parsed.get("mode")
    mode: str | None
    if mode_raw is None:
        mode = None
    elif isinstance(mode_raw, str):
        mode = mode_raw
    else:
        _logger.warning(
            "command_mode_wrong_type",
            source_path=str(path),
            name=name,
            got=type(mode_raw).__name__,
        )
        mode = None

    try:
        return Command(
            name=name,
            description=description,
            body=body,
            source_path=path,
            mode=mode,
        )
    except ValueError as exc:
        _logger.warning(
            "command_validation_failed",
            source_path=str(path),
            name=name,
            error=str(exc),
        )
        return None


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split ``text`` into ``(frontmatter_yaml, body)``.

    Returns ``(None, text)`` if no well-formed frontmatter is found ——
    caller treats this as "skip command" rather than "body-only command",
    because commands WITHOUT name/description can't be invoked and are
    therefore useless.

    ``read_text`` applies universal-newlines translation so CRLF files
    arrive here as LF-only — no special CRLF handling needed (same
    rationale as ``skills/model.py``).
    """
    if not text.startswith(_FRONTMATTER_FENCE + "\n"):
        return None, text

    after_open = text[len(_FRONTMATTER_FENCE) + 1 :]

    fence_with_newline = "\n" + _FRONTMATTER_FENCE + "\n"
    idx = after_open.find(fence_with_newline)
    if idx != -1:
        yaml_block = after_open[:idx]
        body = after_open[idx + len(fence_with_newline) :]
        return yaml_block, body

    closing_at_eof = "\n" + _FRONTMATTER_FENCE
    if after_open.endswith(closing_at_eof):
        yaml_block = after_open[: -len(closing_at_eof)]
        return yaml_block, ""

    return None, text
