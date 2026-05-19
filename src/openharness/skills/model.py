"""``Skill`` dataclass + ``parse_skill`` frontmatter parser — P5c-T1.

Per ``decisions/12`` L1 / L6:a skill is a markdown file with a YAML
frontmatter block fenced by ``---``. Required fields are ``name`` and
``description``;``version`` is optional;other fields are tolerated
(forward-compat).

::

    ---
    name: react-testing
    description: When to write React component tests
    version: 1
    ---

    When writing tests for React components, follow these principles:
    1. ...

P8 refactor: the duplicated outer scaffolding (file read / frontmatter
split / YAML parse / mapping check) lives in
:mod:`openharness.markdown_store`. This module keeps the
:class:`Skill` dataclass + the Skill-specific field extraction
(``version`` coercion) only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.markdown_store import NAME_PATTERN, read_frontmatter_dict
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("skills")


@dataclass(frozen=True)
class Skill:
    """A single skill discovered from the filesystem.

    Constructed by :func:`parse_skill`;the dataclass itself trusts its
    inputs(``__post_init__`` only re-asserts the regex)so tests can
    build fixtures without round-tripping through markdown.
    """

    name: str
    description: str
    body: str
    version: str | None
    source_path: Path

    def __post_init__(self) -> None:
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid skill name {self.name!r}:must match "
                f"{NAME_PATTERN.pattern}"
                " (alphanumeric + ``_-``, starts with letter)"
            )
        if not self.description.strip():
            raise ValueError(f"skill {self.name!r}:description must be non-empty")


def parse_skill(path: Path) -> Skill | None:
    """Read a markdown file with YAML frontmatter and build a :class:`Skill`.

    Returns ``None`` and emits a warning log on any error(file read,
    YAML parse, missing required field, invalid name). The store's
    discovery loop treats ``None`` as "skip" so one bad skill never
    prevents other skills from loading(see ``decisions/12`` acceptance:
    "Invalid frontmatter → warning log at bootstrap, skill skipped").
    """
    parsed, body = read_frontmatter_dict(path, logger_name="skill")
    if parsed is None:
        return None

    name = parsed.get("name")
    description = parsed.get("description")
    version = parsed.get("version")

    if not isinstance(name, str) or not name:
        _logger.warning(
            "skill_missing_name",
            source_path=str(path),
        )
        return None
    if not isinstance(description, str) or not description.strip():
        _logger.warning(
            "skill_missing_description",
            source_path=str(path),
            name=name,
        )
        return None

    # ``version`` is optional. If present, coerce to str so callers don't
    # have to handle int / float (YAML "version: 1" parses as int).
    version_str: str | None = None if version is None else str(version)

    try:
        return Skill(
            name=name,
            description=description,
            body=body,
            version=version_str,
            source_path=path,
        )
    except ValueError as exc:
        # ``Skill.__post_init__`` rejected name regex / empty description
        # — log + skip, never crash bootstrap.
        _logger.warning(
            "skill_validation_failed",
            source_path=str(path),
            name=name,
            error=str(exc),
        )
        return None
