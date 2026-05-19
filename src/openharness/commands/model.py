"""``Command`` dataclass + ``parse_command`` frontmatter parser — P5b-T1.

Per ``decisions/14`` C1 / C2: a slash command is a markdown file with a
YAML frontmatter block fenced by ``---``. Required fields are ``name``
and ``description``; ``mode`` (Phase 5d) is optional.

::

    ---
    name: review
    description: Review pending changes for correctness + security
    ---
    Please review the following changes:

    {args}

    Focus on edge cases and security implications.

P8 refactor: the duplicated outer scaffolding (file read / frontmatter
split / YAML parse / mapping check) lives in
:mod:`openharness.markdown_store`. This module keeps the
:class:`Command` dataclass + the Command-specific field extraction
(``mode``) only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.markdown_store import NAME_PATTERN, read_frontmatter_dict
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

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
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid command name {self.name!r}:must match "
                f"{NAME_PATTERN.pattern}"
                " (alphanumeric + ``_-``, starts with letter)"
            )
        if not self.description.strip():
            raise ValueError(f"command {self.name!r}:description must be non-empty")
        if self.mode is not None and not NAME_PATTERN.match(self.mode):
            raise ValueError(
                f"command {self.name!r}:invalid mode {self.mode!r} — must match "
                f"{NAME_PATTERN.pattern}"
            )


def parse_command(path: Path) -> Command | None:
    """Read a markdown file with YAML frontmatter and build a :class:`Command`.

    Returns ``None`` and emits a warning log on any error(file read,
    YAML parse, missing required field, invalid name). The store's
    discovery loop treats ``None`` as "skip" so one bad command never
    prevents other commands from loading.

    Same never-raise discipline as :func:`openharness.skills.model.parse_skill`.
    """
    parsed, body = read_frontmatter_dict(path, logger_name="command")
    if parsed is None:
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
