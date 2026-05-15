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

Design choices:

- **Frozen dataclass**:Skills are read-only after construction. The
  store discovers them at bootstrap and never mutates.
- **``name`` validation regex matches ``McpServerConfig.name``**:both
  are user-controllable identifiers consumed by the LLM as tool argument
  values;same safe-identifier discipline.
- **``parse_skill`` returns ``Skill | None``, NEVER raises**:bootstrap
  scans the entire skill directory, and one malformed file must not
  prevent other skills from loading. Errors emit a warning via the
  observability logger and return ``None``;caller treats ``None`` as
  "skip this entry."
- **``body`` is stored verbatim** including leading whitespace after the
  closing ``---`` fence:the LLM sees exactly what the author wrote.
- **``source_path`` carries the file path**:useful for diagnostics
  (warning log includes it)and for the L2 override-detection logic in
  the store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

# Same as ``McpServerConfig._NAME_PATTERN`` — both are user-supplied
# identifiers passed as tool arguments. Keeping the regex identical
# keeps the surprise surface uniform.
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

_FRONTMATTER_FENCE = "---"

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
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid skill name {self.name!r}:must match "
                f"{_NAME_PATTERN.pattern}"
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

    Frontmatter delimiter is exactly ``---`` on its own line(both
    opening and closing). Anything before the opening fence is ignored;
    anything after the closing fence is the body.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning(
            "skill_read_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None

    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        _logger.warning(
            "skill_missing_frontmatter",
            source_path=str(path),
        )
        return None

    try:
        parsed: Any = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        _logger.warning(
            "skill_yaml_parse_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None

    if not isinstance(parsed, dict):
        _logger.warning(
            "skill_frontmatter_not_mapping",
            source_path=str(path),
            got=type(parsed).__name__,
        )
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


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split ``text`` into ``(frontmatter_yaml, body)``.

    Returns ``(None, text)`` if no well-formed frontmatter is found ——
    caller treats this as "skip skill" rather than "body-only skill",
    because skills without name / description can't appear in the
    catalog and are therefore useless.

    Algorithm:

    1. File must start with ``---\\n`` (the opening fence). ``read_text``
       applies universal-newlines translation so CRLF files arrive here
       as LF-only — no special CRLF handling needed.
    2. Find the next line that is exactly ``---``.
    3. YAML block = text between fences; body = text after closing fence
       (may be empty if file ends at the closing fence with no trailing
       newline).
    """
    if not text.startswith(_FRONTMATTER_FENCE + "\n"):
        return None, text

    after_open = text[len(_FRONTMATTER_FENCE) + 1 :]  # past ``---\n``

    fence_with_newline = "\n" + _FRONTMATTER_FENCE + "\n"
    idx = after_open.find(fence_with_newline)
    if idx != -1:
        yaml_block = after_open[:idx]
        body = after_open[idx + len(fence_with_newline) :]
        return yaml_block, body

    # Closing fence may be the last line with no trailing newline.
    closing_at_eof = "\n" + _FRONTMATTER_FENCE
    if after_open.endswith(closing_at_eof):
        yaml_block = after_open[: -len(closing_at_eof)]
        return yaml_block, ""

    # No closing fence found — malformed.
    return None, text
