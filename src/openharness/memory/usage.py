"""Usage tracking via atomic frontmatter rewrite — P10-T3.3b.

Per ``decisions/25-phase-10-boundary.md`` D28.8: every time a memory
is picked by :func:`openharness.memory.relevance.select_relevant_memories`
and injected into the system prompt, its frontmatter is rewritten
to bump ``use_count`` and stamp ``last_used_at``. This is the closed
loop that lets Phase 13's stale-memory GC distinguish "valuable
memory the relevance scorer keeps surfacing" from "noise the user
never queries about" — without usage tracking, Phase 13 has no
signal to GC against.

D28.3 deviation from HKUDS: usage fields live in the same frontmatter
as the rest of the memory metadata (single source of truth) rather
than a separate ``.usage_index.json``. Trade-off: every relevance
injection writes the .md file (write amplification), accepted because
expected memory count per project is <100 in Phase 10.

Atomicity: ``tempfile`` in the same directory + :func:`os.replace`.
POSIX guarantees rename within a filesystem is atomic — concurrent
readers see either the old file or the new file, never a half-written
one. No explicit lock; Phase 11's extraction may add file locking when
it can write concurrently with relevance injection.

Failure handling: **never raises**. If the file is gone, the
filesystem is read-only, or the YAML can't round-trip,
:func:`mark_memory_used` logs ``memory_usage_update_failed`` and
returns. Prompt assembly must not block on usage updates — the user's
request is what matters.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openharness.markdown_store import FRONTMATTER_FENCE, split_frontmatter
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from openharness.memory.model import Memory

_logger = get_logger("memory")


def mark_memory_used(memory: Memory) -> None:
    """Atomically increment ``use_count`` + set ``last_used_at = now(UTC)``.

    Reads :attr:`Memory.source_path`, parses its frontmatter, mutates
    only the two usage fields, and writes the whole file back via
    a same-directory tempfile + :func:`os.replace`. All other fields
    (id / name / created_at / tags / body / ...) round-trip
    unchanged.

    Failures at any phase (read / parse / serialize / write) log a
    ``memory_usage_update_failed`` warning with the ``phase`` tag and
    return without raising. The :class:`Memory` instance in memory is
    NOT mutated — it's a frozen dataclass and the next discover() will
    pick up the new use_count from the rewritten file.
    """
    text = _read_source(memory)
    if text is None:
        return

    data = _parse_frontmatter(memory, text)
    if data is None:
        return

    data["use_count"] = int(data.get("use_count", 0)) + 1
    data["last_used_at"] = datetime.now(timezone.utc).isoformat()

    new_text = _serialize(memory, data, text)
    if new_text is None:
        return

    _atomic_write(memory, new_text)


# ---------------------------------------------------------------------------
# Helpers — each phase isolated so failure mode + log tag are localized
# ---------------------------------------------------------------------------


def _read_source(memory: Memory) -> str | None:
    try:
        return memory.source_path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning(
            "memory_usage_update_failed",
            source_path=str(memory.source_path),
            name=memory.name,
            phase="read",
            error=str(exc),
        )
        return None


def _parse_frontmatter(memory: Memory, text: str) -> dict[str, Any] | None:
    frontmatter_yaml, _body = split_frontmatter(text)
    if frontmatter_yaml is None:
        _logger.warning(
            "memory_usage_update_failed",
            source_path=str(memory.source_path),
            name=memory.name,
            phase="split",
            error="frontmatter missing or unclosed",
        )
        return None
    try:
        data = yaml.safe_load(frontmatter_yaml)
    except yaml.YAMLError as exc:
        _logger.warning(
            "memory_usage_update_failed",
            source_path=str(memory.source_path),
            name=memory.name,
            phase="parse",
            error=str(exc),
        )
        return None
    if not isinstance(data, dict):
        _logger.warning(
            "memory_usage_update_failed",
            source_path=str(memory.source_path),
            name=memory.name,
            phase="parse",
            error="frontmatter is not a mapping",
        )
        return None
    return data


def _serialize(memory: Memory, data: dict[str, Any], original_text: str) -> str | None:
    """Reassemble ``<fence>\\n<yaml>\\n<fence>\\n<body>``.

    Preserves the body **byte-exactly** by extracting it from the
    original text via :func:`split_frontmatter` (same primitive
    :func:`parse_memory` uses, so the split is identical to what the
    parser saw). YAML round-trip on the frontmatter dict may reformat
    keys (key order, quoting style); we accept that drift because
    semantic content is preserved and parse_memory is robust to all
    YAML-valid shapes.
    """
    _frontmatter_yaml, body = split_frontmatter(original_text)
    # ``frontmatter_yaml is None`` was already short-circuited in
    # _parse_frontmatter — if we got here, body is the real body.
    try:
        new_frontmatter = yaml.safe_dump(
            data,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    except yaml.YAMLError as exc:
        _logger.warning(
            "memory_usage_update_failed",
            source_path=str(memory.source_path),
            name=memory.name,
            phase="serialize",
            error=str(exc),
        )
        return None
    # ``new_frontmatter`` already ends with ``\n`` from yaml.dump,
    # so the closing fence + body assembly is straightforward.
    return f"{FRONTMATTER_FENCE}\n{new_frontmatter}{FRONTMATTER_FENCE}\n{body}"


def _atomic_write(memory: Memory, new_text: str) -> None:
    """Write ``new_text`` via same-dir tempfile + :func:`os.replace`.

    Same-directory tempfile guarantees the rename is within a single
    filesystem (cross-fs rename is non-atomic). On failure, we clean
    up the orphan tempfile so disk-full / permission errors don't
    leak ``.tmp`` files into the memory dir.
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=memory.source_path.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(new_text)
        os.replace(tmp_path, memory.source_path)
        tmp_path = None  # ownership transferred; finally won't unlink
    except OSError as exc:
        _logger.warning(
            "memory_usage_update_failed",
            source_path=str(memory.source_path),
            name=memory.name,
            phase="write",
            error=str(exc),
        )
    finally:
        if tmp_path is not None and tmp_path.exists():
            # Best-effort cleanup; if even unlink fails, leave the
            # tempfile and let the user clean up manually. We've
            # already failed loudly via the warning above.
            with contextlib.suppress(OSError):
                tmp_path.unlink()
