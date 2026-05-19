"""Shared markdown+YAML-frontmatter parsing — P8-T1.

Two helpers replace the duplicated outer scaffolding from the three
consuming modules:

- :func:`split_frontmatter` — pure string operation; identical
  behavior to the three private ``_split_frontmatter`` copies it
  replaces.
- :func:`read_frontmatter_dict` — file read + split + YAML parse +
  mapping check, with per-domain warning logs on each failure mode.
  Returns ``(dict | None, body)``;``None`` signals "skip; warning
  already logged."

Domain-specific field extraction stays in each domain's parser. This
module owns only the failure-mode-uniform outer logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from openharness.markdown_store.constants import FRONTMATTER_FENCE
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split ``text`` into ``(frontmatter_yaml, body)``.

    Returns ``(None, text)`` if no well-formed frontmatter is found
    — caller treats this as "skip document" rather than "body-only
    document".

    ``read_text`` applies universal-newlines translation so CRLF
    files arrive here as LF-only — no special CRLF handling needed.
    """
    if not text.startswith(FRONTMATTER_FENCE + "\n"):
        return None, text

    after_open = text[len(FRONTMATTER_FENCE) + 1 :]

    fence_with_newline = "\n" + FRONTMATTER_FENCE + "\n"
    idx = after_open.find(fence_with_newline)
    if idx != -1:
        yaml_block = after_open[:idx]
        body = after_open[idx + len(fence_with_newline) :]
        return yaml_block, body

    closing_at_eof = "\n" + FRONTMATTER_FENCE
    if after_open.endswith(closing_at_eof):
        yaml_block = after_open[: -len(closing_at_eof)]
        return yaml_block, ""

    return None, text


def read_frontmatter_dict(
    path: Path,
    *,
    logger_name: str,
) -> tuple[dict[str, Any] | None, str]:
    """Read a markdown file and return its parsed frontmatter dict + body.

    Returns ``(None, "")`` on any failure (file read, missing
    frontmatter, malformed YAML, non-mapping frontmatter), with a
    warning logged using ``logger_name`` as both the logger and the
    event prefix. Returns ``(dict, body)`` on success.

    Never raises. The four failure modes log:

    - ``<logger_name>_read_failed`` — :class:`OSError` reading the file
    - ``<logger_name>_missing_frontmatter`` — no ``---`` fence
    - ``<logger_name>_yaml_parse_failed`` — invalid YAML
    - ``<logger_name>_frontmatter_not_mapping`` — YAML root isn't a dict

    Domain-specific field validation (e.g. "missing name") lives in
    each domain's parser after this returns.
    """
    log = get_logger(logger_name)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning(
            f"{logger_name}_read_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None, ""

    frontmatter, body = split_frontmatter(text)
    if frontmatter is None:
        log.warning(
            f"{logger_name}_missing_frontmatter",
            source_path=str(path),
        )
        return None, ""

    try:
        parsed: Any = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        log.warning(
            f"{logger_name}_yaml_parse_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None, ""

    if not isinstance(parsed, dict):
        log.warning(
            f"{logger_name}_frontmatter_not_mapping",
            source_path=str(path),
            got=type(parsed).__name__,
        )
        return None, ""

    return parsed, body
