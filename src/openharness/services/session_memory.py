"""Session memory 5-slot checkpoint writer/reader — P11-T2.

Per ``decisions/26-phase-11-boundary.md`` D29.4: deterministic
per-turn checkpoint that compact L3 (P11-T3) reads to skip the
LLM-driven L4 summary entirely. **No LLM call** here — the file
is built from existing :class:`tool_metadata` fields the engine
maintains, plus message one-liners.

Storage layout (mirrors :func:`openharness.memory.paths.get_project_memory_dir`):

.. code-block:: text

    ~/.openharness/session-memory/<basename(cwd)>-<sha1(cwd)[:12]>/
    └── checkpoint.md                # SINGLE file per project, overwritten

Why a single file (not append-only): the 5-slot schema is a
**snapshot of "where the conversation is right now"**, not an audit
log. Compact L3 wants "the current task focus + recent context",
not "all checkpoints ever". Atomic overwrite via ``tempfile + os.replace``.

5-slot schema (verbatim section names so L3 can splice predictably):

1. ``## Current State`` — ``tool_metadata["task_focus_state"]["goal"]``
2. ``## Next Step`` — ``tool_metadata["task_focus_state"]["next_step"]``
3. ``## Verified Work`` — last 10 ``tool_metadata["verified_work"]``
4. ``## Active Artifacts`` — last 10 ``tool_metadata["recent_files"]``
5. ``## Recent Conversation`` — last 80 message one-liners

Each slot has a placeholder when its source data is absent — the
section structure stays stable even when the engine hasn't populated
the corresponding metadata field. Phase 11 ships the WRITER + the
SCHEMA; engine writing into ``task_focus_state`` / ``verified_work``
/ ``recent_files`` is a future extension (P11-T3 compact integration
or beyond) — checkpoint will be mostly placeholder until then.

12,000-char cap (matches HKUDS upstream). When exceeded, truncate
``Recent Conversation`` slot oldest-first, then ``Active Artifacts``
oldest-first, then hard-truncate the rendered markdown.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openharness.protocols.messages import ConversationMessage


_MAX_CHECKPOINT_CHARS = 12_000
_MAX_VERIFIED_WORK = 10
_MAX_ACTIVE_ARTIFACTS = 10
_MAX_RECENT_CONVERSATION = 80
_MESSAGE_ONELINER_CHAR_CAP = 80


def get_session_memory_dir(cwd: str | Path) -> Path:
    """Resolve the per-project session-memory directory for ``cwd``.

    Same shape + properties as
    :func:`openharness.memory.paths.get_project_memory_dir`:
    cwd-hashed, outside the repo, under user home.

    ``Path.home()`` evaluated at call time (NOT module scope) so the
    HOME-isolation fixture in ``tests/conftest.py`` takes effect. Same
    lesson as Phase 10 T1-1c (see ``learnings/phase-10.md`` §4.2).
    """
    resolved = Path(cwd).resolve()
    digest = sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".openharness" / "session-memory" / f"{resolved.name}-{digest}"


def update_session_memory_file(
    cwd: str | Path,
    tool_metadata: dict[str, Any],
    messages: list[ConversationMessage],
) -> Path:
    """Atomically write the 5-slot checkpoint for ``cwd``. Returns
    the path written to.

    Lazy ``mkdir`` of parent dir (matches the lazy storage pattern
    Phase 10 D28.1 established for memory dir). Atomic rewrite via
    same-directory ``tempfile + os.replace`` — concurrent compact L3
    reads either see the previous file or the new file, never a
    half-written one.

    The function is called from the engine's per-turn-end finally
    block; failures here should NOT block the turn from returning
    successfully. **However**, Phase 11 chooses to let exceptions
    propagate from this function (caller wraps if it wants to be
    non-blocking). Rationale: the engine's existing memory-extract
    machinery is already async-non-blocking; if checkpoint write
    fails, the user wants to know (it indicates disk / permission
    issues that will bite the rest of memory too).
    """
    storage_dir = get_session_memory_dir(cwd)
    storage_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = storage_dir / "checkpoint.md"

    content = _render_5_slot(tool_metadata, messages)

    # Atomic write: tempfile in same dir + os.replace
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=storage_dir,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, checkpoint_path)
        tmp_path = None  # ownership transferred
    finally:
        if tmp_path is not None and tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
    return checkpoint_path


def read_session_memory(cwd: str | Path) -> str | None:
    """Read the checkpoint for ``cwd``. Returns ``None`` if missing
    (no checkpoint yet — first turn of a project).

    Single ``read_text`` call grabs the file as a string at the OS
    level atomically — concurrent ``update_session_memory_file``
    cannot interleave a half-written file (``os.replace`` is atomic
    within a filesystem). Worst case: read happens between two
    overwrites and sees the previous version. That's fine — compact
    L3 just gets a slightly-stale checkpoint, not a corrupted one.
    """
    storage_dir = get_session_memory_dir(cwd)
    checkpoint_path = storage_dir / "checkpoint.md"
    if not checkpoint_path.exists():
        return None
    try:
        return checkpoint_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _render_5_slot(
    tool_metadata: dict[str, Any],
    messages: list[ConversationMessage],
) -> str:
    """Build the 5-slot markdown. Pure function — same inputs, same
    output (no I/O, no time-dependent state).

    Each slot rendered with a placeholder when its source data is
    absent — section structure stays stable across "engine hasn't
    populated metadata yet" / "engine wrote partial metadata" /
    "fully populated" states.

    12k char cap enforced post-render: truncate Recent Conversation
    first (most expendable — older one-liners aren't load-bearing),
    then Active Artifacts (less load-bearing than goals), then hard
    cap.
    """
    sections = [
        "# Session Memory",
        "",
        "## Current State",
        _get_focus_field(tool_metadata, "goal", default="(none)"),
        "",
        "## Next Step",
        _get_focus_field(tool_metadata, "next_step", default="(awaiting user direction)"),
        "",
        "## Verified Work",
    ]
    verified_items = _last_n_strings(tool_metadata.get("verified_work"), _MAX_VERIFIED_WORK)
    if verified_items:
        sections.extend(f"- {item}" for item in verified_items)
    else:
        sections.append("(none yet)")

    sections.extend(["", "## Active Artifacts"])
    artifact_items = _last_n_strings(tool_metadata.get("recent_files"), _MAX_ACTIVE_ARTIFACTS)
    if artifact_items:
        sections.extend(f"- {item}" for item in artifact_items)
    else:
        sections.append("(none touched yet)")

    sections.extend(["", "## Recent Conversation"])
    conversation_lines = _render_message_oneliners(messages)
    if conversation_lines:
        sections.extend(conversation_lines)
    else:
        sections.append("(no turns yet)")

    rendered = "\n".join(sections) + "\n"

    if len(rendered) <= _MAX_CHECKPOINT_CHARS:
        return rendered

    # Over cap. Truncate Recent Conversation oldest-first.
    rendered = _truncate_with_priority(sections, conversation_lines, artifact_items)
    return rendered


def _get_focus_field(tool_metadata: dict[str, Any], key: str, *, default: str) -> str:
    """Extract ``task_focus_state[key]`` defensively.

    ``task_focus_state`` may be absent entirely (early Phase 11 — engine
    hasn't started writing it), present-but-empty, or present-with-key.
    All three flow to the same string return — caller doesn't need to
    branch.
    """
    focus = tool_metadata.get("task_focus_state")
    if not isinstance(focus, dict):
        return default
    value = focus.get(key)
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip()


def _last_n_strings(value: Any, limit: int) -> list[str]:
    """Take last ``limit`` string items from ``value`` (if it's a list).

    Non-list inputs → empty list (defensive — engine might
    accidentally write a different type).
    """
    if not isinstance(value, list):
        return []
    strings = [item for item in value if isinstance(item, str)]
    return strings[-limit:]


def _render_message_oneliners(messages: list[ConversationMessage]) -> list[str]:
    """Render last 80 messages as ``- [role] first-80-chars`` lines.

    For each block in the message, take the first text-like
    representation:
    - :class:`TextBlock` → its text
    - :class:`ToolUseBlock` → ``[tool] {name}``
    - :class:`ToolResultBlock` → ``[tool_result] {first 80 chars}``
    - :class:`ImageBlock` → ``[image]``

    A multi-block message produces ONE line (first block wins) — the
    checkpoint is meant to be scannable, not exhaustive.
    """
    # Local imports — avoid circular when this module loads at
    # engine import time
    from openharness.protocols.content import (
        ImageBlock,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    recent = messages[-_MAX_RECENT_CONVERSATION:]
    lines: list[str] = []
    for msg in recent:
        if not msg.content:
            continue
        block = msg.content[0]
        if isinstance(block, TextBlock):
            text = block.text.strip().replace("\n", " ")
            text = text[:_MESSAGE_ONELINER_CHAR_CAP]
            lines.append(f"- [{msg.role}] {text}")
        elif isinstance(block, ToolUseBlock):
            lines.append(f"- [{msg.role}/tool] {block.name}")
        elif isinstance(block, ToolResultBlock):
            content = block.content.strip().replace("\n", " ")
            content = content[:_MESSAGE_ONELINER_CHAR_CAP]
            lines.append(f"- [{msg.role}/tool_result] {content}")
        elif isinstance(block, ImageBlock):
            lines.append(f"- [{msg.role}/image]")
    return lines


def _truncate_with_priority(
    base_sections: list[str],
    conversation_lines: list[str],
    artifact_items: list[str],
) -> str:
    """Re-render with progressively-truncated low-priority slots.

    Strategy:
    1. Drop oldest Recent Conversation entries until fit
    2. If still over, drop oldest Active Artifacts entries
    3. If still over (very rare), hard truncate the rendered string
    """
    # We need to rebuild from scratch with adjusted slot contents.
    # ``base_sections`` already contains the rendered structure, but
    # we can't easily mutate specific slots — so re-render via
    # _render_5_slot-style assembly with truncated inputs.
    #
    # Implementation: binary-search the truncation point. Cap is
    # only exceeded by ~2x in worst case, so linear shrink is fine.
    conv = list(conversation_lines)
    arts = list(artifact_items)
    while conv:
        conv.pop(0)  # drop oldest
        candidate = _assemble(base_sections, conv, arts)
        if len(candidate) <= _MAX_CHECKPOINT_CHARS:
            return candidate
    # Recent Conversation fully drained — try shrinking artifacts
    while arts:
        arts.pop(0)
        candidate = _assemble(base_sections, conv, arts)
        if len(candidate) <= _MAX_CHECKPOINT_CHARS:
            return candidate
    # Both fully drained still over (huge Verified Work or
    # focus_state). Hard truncate the rendered text + marker.
    candidate = _assemble(base_sections, [], [])
    if len(candidate) > _MAX_CHECKPOINT_CHARS:
        cap = _MAX_CHECKPOINT_CHARS - 40  # leave room for marker
        candidate = candidate[:cap] + "\n\n[... truncated to fit cap]\n"
    return candidate


def _assemble(
    base_sections: list[str],
    conversation_lines: list[str],
    artifact_items: list[str],
) -> str:
    """Rebuild the markdown with the given (possibly truncated)
    Recent Conversation + Active Artifacts. The other slots come
    from ``base_sections`` (untouched)."""
    out = list(base_sections[: base_sections.index("## Active Artifacts") + 1])
    if artifact_items:
        out.extend(f"- {item}" for item in artifact_items)
    else:
        out.append("(none touched yet)")
    out.extend(["", "## Recent Conversation"])
    if conversation_lines:
        out.extend(conversation_lines)
    else:
        out.append("(no turns yet)")
    return "\n".join(out) + "\n"
