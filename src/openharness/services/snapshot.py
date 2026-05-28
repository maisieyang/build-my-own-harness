"""Session snapshot writer/loader — P12-T2.

Per ``decisions/27-phase-12-boundary.md`` D30.2: deterministic
per-turn JSON snapshot that ``oh ask --resume`` / ``oh chat --resume``
loads back into a fresh ``QueryContext``. Parallel to (NOT replacing)
Phase 11's 5-slot markdown checkpoint at
``services/session_memory.py`` — two consumers (L3 compact reads the
markdown, resume reads the JSON), one ``tool_metadata`` producer
in the engine (P12-T1).

Storage layout (mirrors :func:`openharness.services.session_memory.get_session_memory_dir`
hash algorithm, but under a sibling ``snapshots/`` directory so the
two file families don't collide):

.. code-block:: text

    ~/.openharness/snapshots/<basename(cwd)>-<sha1(cwd)[:12]>/
    ├── current.json          # SINGLE file per project, overwritten
    └── history/              # reserved for Phase 13+ rotation

JSON schema (v1, locked verbatim — see ``SNAPSHOT_SCHEMA``):

.. code-block:: json

    {
      "version": 1,
      "schema": "openharness.snapshot.v1",
      "created_at": "ISO 8601",
      "git_head": "abc1234" or null,
      "cwd": "/absolute/path",
      "model": "...",
      "permission_mode": "default",
      "system_prompt": "...",
      "max_tokens": 1024,
      "messages": [...pydantic-serialized ConversationMessage...],
      "tool_metadata": {...},
      "extra": {}
    }

Why JSON not markdown (D30.2): ``messages`` is a discriminated union
of typed content blocks (tool_use / tool_result / image / text);
round-tripping through markdown loses fidelity. Resume needs
byte-identical message history so the agent's reasoning chain
stays consistent.

Three staleness signals when loading (D30.5):

- ``git_head`` drift → log warn, still load (let user decide)
- ``cwd`` mismatch → raise :class:`SnapshotCwdMismatch` (REFUSE)
- ``version`` > supported → raise :class:`SnapshotVersionMismatch` (REFUSE)
- ``version`` < supported → log info, still load (forward-compat)
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openharness.observability import get_logger

if TYPE_CHECKING:
    from openharness.engine.context import QueryContext
    from openharness.protocols.messages import ConversationMessage

_logger = get_logger("snapshot")


SNAPSHOT_VERSION = 1
SNAPSHOT_SCHEMA = "openharness.snapshot.v1"
_GIT_REV_PARSE_TIMEOUT_S = 1.0


# ---------------------------------------------------------------------------
# Error hierarchy (D30.5)
# ---------------------------------------------------------------------------


class SnapshotError(Exception):
    """Base class for snapshot load failures.

    Caller's ``except SnapshotError`` catches every failure mode.
    Specific subclasses let the CLI surface targeted error messages
    ("snapshot belongs to another project" vs "snapshot version is
    newer than this harness supports").
    """


class SnapshotNotFound(SnapshotError):
    """No snapshot exists at the resolved path for ``cwd``.

    Raised by :func:`load_snapshot` when ``current.json`` is missing.
    The CLI surfaces this as "no snapshot for cwd — starting fresh"
    when ``--resume`` was passed without ``--resume-id``.
    """


class SnapshotCwdMismatch(SnapshotError):
    """Snapshot's recorded cwd doesn't match the current cwd.

    Refuses to load (D30.5): silent loading would let same-basename
    hash collisions cross-pollute projects. The error includes both
    paths so the user can diagnose.
    """


class SnapshotVersionMismatch(SnapshotError):
    """Snapshot's ``version`` is newer than this harness supports.

    Refuses to load (D30.5): a forward-compat v1 reader cannot safely
    interpret v2 fields. Caller surfaces "upgrade openharness to read
    this snapshot".
    """


class SnapshotMalformed(SnapshotError):
    """Snapshot file is not valid JSON or missing required fields.

    Distinct from ``SnapshotCwdMismatch`` / ``SnapshotVersionMismatch``
    because those happen on STRUCTURALLY-valid snapshots. Malformed
    means parser-level failure (truncated file / hand-edited typo /
    completely-wrong-file in the dir).
    """


def load_snapshot(
    cwd: str | Path,
    *,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Load the snapshot for ``cwd`` and validate per D30.5.

    Parameters:
        cwd: project directory whose snapshot to load.
        snapshot_id: optional git-HEAD-prefix selector for Phase 13's
            ``history/`` rotation. Phase 12 ignores it (only
            ``current.json`` exists); raises ``SnapshotNotFound`` if
            a non-None ``snapshot_id`` is passed but doesn't match
            the current snapshot's ``git_head``.

    Returns:
        The parsed snapshot dict.

    Raises:
        SnapshotNotFound: no ``current.json`` for ``cwd``.
        SnapshotCwdMismatch: snapshot's ``cwd`` field ≠ resolved arg.
        SnapshotVersionMismatch: snapshot's ``version`` > supported.
        SnapshotMalformed: not valid JSON or missing required field.

    Staleness signals emitted as log events (not raised):

    - ``snapshot_git_head_drift`` at WARN when snapshot's ``git_head``
      differs from current HEAD.
    - ``snapshot_version_downgrade`` at INFO when snapshot's
      ``version`` < supported (forward-compat path).
    """
    resolved_cwd = Path(cwd).resolve()
    snapshot_path = get_snapshot_dir(resolved_cwd) / "current.json"

    if not snapshot_path.exists():
        raise SnapshotNotFound(f"no snapshot at {snapshot_path}")

    try:
        raw = snapshot_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotMalformed(f"unable to read snapshot: {exc}") from exc

    if not isinstance(data, dict):
        raise SnapshotMalformed(f"snapshot root is not a JSON object: {type(data).__name__}")

    # Version check FIRST — a v2 snapshot might have a totally
    # different field layout, so refuse before reading other fields.
    version = data.get("version")
    if not isinstance(version, int):
        raise SnapshotMalformed(f"snapshot missing/invalid ``version`` field: {version!r}")
    if version > SNAPSHOT_VERSION:
        raise SnapshotVersionMismatch(
            f"snapshot version {version} > supported {SNAPSHOT_VERSION}; "
            f"upgrade openharness to read this snapshot"
        )
    if version < SNAPSHOT_VERSION:
        _logger.info(
            "snapshot_version_downgrade",
            snapshot_version=version,
            supported_version=SNAPSHOT_VERSION,
        )

    # Cwd check — silent corruption guard per D30.5.
    snapshot_cwd = data.get("cwd")
    if not isinstance(snapshot_cwd, str):
        raise SnapshotMalformed(f"snapshot missing ``cwd`` field: {snapshot_cwd!r}")
    if Path(snapshot_cwd).resolve() != resolved_cwd:
        raise SnapshotCwdMismatch(
            f"snapshot cwd {snapshot_cwd!r} does not match current cwd {str(resolved_cwd)!r}"
        )

    # snapshot_id filter — Phase 12 only has current.json; if caller
    # asked for a specific git_head prefix, treat as no-match.
    if snapshot_id is not None:
        snapshot_head = data.get("git_head")
        if not isinstance(snapshot_head, str) or not snapshot_head.startswith(snapshot_id):
            raise SnapshotNotFound(
                f"snapshot for cwd has git_head {snapshot_head!r}, "
                f"does not match prefix {snapshot_id!r}"
            )

    # Git HEAD drift — warn, don't refuse (D30.5).
    current_head = _current_git_head(resolved_cwd)
    snapshot_head = data.get("git_head")
    if (
        current_head is not None
        and isinstance(snapshot_head, str)
        and current_head != snapshot_head
    ):
        _logger.warning(
            "snapshot_git_head_drift",
            snapshot_head=snapshot_head,
            current_head=current_head,
        )

    return data


def get_snapshot_dir(cwd: str | Path) -> Path:
    """Resolve the per-project snapshot directory for ``cwd``.

    Same hash algorithm as
    :func:`openharness.services.session_memory.get_session_memory_dir`
    (basename + sha1[:12] of resolved cwd) — so the snapshot dir is a
    sibling of the session-memory dir under
    ``~/.openharness/snapshots/`` vs ``~/.openharness/session-memory/``.

    ``Path.home()`` evaluated at call time (NOT module scope) so the
    HOME-isolation fixture in ``tests/conftest.py`` takes effect.
    Same lesson as Phase 10 T1-1c (``learnings/phase-10.md`` §4.2).
    """
    resolved = Path(cwd).resolve()
    digest = sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".openharness" / "snapshots" / f"{resolved.name}-{digest}"


def _current_git_head(cwd: str | Path) -> str | None:
    """Return short git HEAD SHA for ``cwd``, or None when unavailable.

    None on any of: not a git working tree / git not installed /
    detached HEAD with no parent / subprocess timeout / any other error.
    Used for D30.5's staleness warning — silent failure is fine because
    the snapshot just stores ``git_head: null`` and resume skips the
    drift check.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_REV_PARSE_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    head = result.stdout.strip()
    return head or None


def _serialize_snapshot(
    *,
    cwd: Path,
    tool_metadata: dict[str, Any],
    messages: list[ConversationMessage],
    context: QueryContext,
) -> dict[str, Any]:
    """Build the JSON-serializable snapshot dict.

    Pure function — given the same inputs, returns the same dict
    (except for ``created_at`` which is wall-clock now). Separated
    from the writer so tests can pin the schema without touching disk.

    ``messages`` round-trip via pydantic ``model_dump(mode="json")``;
    each ContentBlock subtype carries its ``type`` discriminator so
    the discriminated union reconstructs correctly on load.

    ``permission_mode`` serialized as enum **value** (string), not
    the enum repr — so JSON consumers (and a future v2 → v1 reader)
    don't need access to the Python enum class.
    """
    return {
        "version": SNAPSHOT_VERSION,
        "schema": SNAPSHOT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _current_git_head(cwd),
        "cwd": str(cwd),
        "model": context.model,
        "permission_mode": context.permission_mode.value,
        "system_prompt": context.system_prompt or "",
        "max_tokens": context.max_tokens,
        "messages": [m.model_dump(mode="json") for m in messages],
        # Deep copy so producer mutation after serialization can't
        # silently mutate the snapshot dict (nested lists like
        # ``recent_files`` would otherwise be shared by reference).
        "tool_metadata": copy.deepcopy(tool_metadata),
        "extra": {},
    }


def write_session_snapshot(
    *,
    cwd: str | Path,
    tool_metadata: dict[str, Any],
    messages: list[ConversationMessage],
    context: QueryContext,
    history_max_count: int | None = None,
    history_max_age_days: int | None = None,
) -> Path:
    """Atomically write the JSON snapshot for ``cwd``. Returns the
    path written (``<dir>/current.json``).

    Lazy ``mkdir`` of parent dir. Atomic rewrite via same-directory
    ``tempfile + os.replace`` — same pattern as
    :func:`openharness.services.session_memory.update_session_memory_file`,
    same atomicity guarantee (concurrent ``load_snapshot`` reads either
    the previous version or the new one, never partial).

    P13-T1 (D31.3): when a pre-existing ``current.json`` is being
    overwritten, it first gets rotated into ``history/<key>.json``
    via :func:`_rotate_current_to_history` BEFORE the new content
    lands. ``history_max_count`` + ``history_max_age_days`` arrive
    from ``settings.snapshot.history.*``; when None, rotation
    still happens but uses the default caps from
    :class:`SnapshotHistorySettings`. Pass 0 to disable that arm
    specifically.

    Per D30.9 the engine call site catches ``OSError`` from this
    function — failures don't fail the turn but DO emit a warning log
    so disk/permission issues surface in observability.
    """
    resolved_cwd = Path(cwd).resolve()
    storage_dir = get_snapshot_dir(resolved_cwd)
    storage_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = storage_dir / "current.json"

    payload = _serialize_snapshot(
        cwd=resolved_cwd,
        tool_metadata=tool_metadata,
        messages=messages,
        context=context,
    )
    content = json.dumps(payload, indent=2, ensure_ascii=False)

    # P13-T1 (D31.3): rotate pre-existing current.json into history/
    # BEFORE overwriting. Read first → write new → move old.
    # Reader sees old-or-new (never missing) per atomicity contract.
    rotation_source: dict[str, Any] | None = None
    if snapshot_path.exists():
        try:
            rotation_source = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Existing snapshot unreadable/malformed — don't block the
            # new write, but log so the corruption surfaces.
            _logger.warning(
                "snapshot_rotation_read_failed",
                snapshot=str(snapshot_path),
                error=str(exc),
            )
            rotation_source = None

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
        os.replace(tmp_path, snapshot_path)
        tmp_path = None  # ownership transferred to snapshot_path
    finally:
        if tmp_path is not None and tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()

    # P13-T1 (D31.3 + D31.5): rotation step. Failures here are
    # WARN-logged but don't fail the snapshot write (the new
    # current.json is already live).
    if rotation_source is not None:
        max_count = (
            history_max_count if history_max_count is not None else _DEFAULT_HISTORY_MAX_COUNT
        )
        max_age_days = (
            history_max_age_days
            if history_max_age_days is not None
            else _DEFAULT_HISTORY_MAX_AGE_DAYS
        )
        try:
            _rotate_current_to_history(
                storage_dir=storage_dir,
                rotation_source=rotation_source,
                max_count=max_count,
                max_age_days=max_age_days,
            )
        except OSError as exc:
            _logger.warning(
                "snapshot_rotation_failed",
                snapshot=str(snapshot_path),
                error=str(exc),
            )

    return snapshot_path


# ---------------------------------------------------------------------------
# Phase 13 (D31.2 + D31.3 + D31.4 + D31.5): rotation
# ---------------------------------------------------------------------------


_DEFAULT_HISTORY_MAX_COUNT = 100
_DEFAULT_HISTORY_MAX_AGE_DAYS = 90
_HISTORY_DIR_NAME = "history"
_HISTORY_COLLISION_SUFFIX_MAX = 99


def _compute_history_name(snapshot: dict[str, Any]) -> str:
    """Build the history/ filename for ``snapshot`` per D31.4.

    Format: ``<git_head>-<YYYYMMDDhhmmss>.json``. When ``git_head``
    is null/missing, the literal ``"nogit"`` substitutes. When
    ``created_at`` can't be parsed, the current wall-clock is used.

    Collision suffix (``-<n>``) is appended by caller — this
    function returns the BASE name; collision resolution is done
    against the destination directory.
    """
    git_head = snapshot.get("git_head")
    if not isinstance(git_head, str) or not git_head:
        git_head = "nogit"

    created_at = snapshot.get("created_at")
    iso_short: str
    if isinstance(created_at, str):
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            iso_short = dt.strftime("%Y%m%d%H%M%S")
        except ValueError:
            iso_short = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    else:
        iso_short = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    return f"{git_head}-{iso_short}.json"


def _resolve_history_path(history_dir: Path, base_name: str) -> Path:
    """Return a non-existent path in ``history_dir`` for ``base_name``.

    If ``history_dir/base_name`` exists, append ``-1``, ``-2``, etc.
    up to ``_HISTORY_COLLISION_SUFFIX_MAX`` per D31.4. Raises ``OSError``
    if all suffixes exhausted (extremely rare).
    """
    candidate = history_dir / base_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for n in range(1, _HISTORY_COLLISION_SUFFIX_MAX + 1):
        alt = history_dir / f"{stem}-{n}{suffix}"
        if not alt.exists():
            return alt
    raise OSError(
        f"history/ name collision exhausted: {base_name!r} + "
        f"{_HISTORY_COLLISION_SUFFIX_MAX} numeric suffixes all taken"
    )


def _rotate_current_to_history(
    *,
    storage_dir: Path,
    rotation_source: dict[str, Any],
    max_count: int,
    max_age_days: int,
) -> Path | None:
    """Move ``rotation_source`` content into ``storage_dir/history/``.

    The new current.json must already be in place (caller's
    responsibility). This function ONLY moves the previously-buffered
    old content to history/ then runs GC.

    Atomicity (D31.5): write the new history entry by re-serializing
    the buffered ``rotation_source`` dict atomically (tempfile +
    os.replace). We don't hardlink the live current.json because by
    the time this function runs, current.json holds the NEW content.
    The old content is in memory (``rotation_source`` arg), so the
    atomic-rewrite path is the natural fit — same pattern as the
    write_session_snapshot atomic write above.

    Returns the history/ path written (or None on no-op when
    max_count=0 → no history kept).
    """
    if max_count == 0:
        # max_count=0 means "keep no history" — still GC anything
        # already there but don't add a new entry.
        history_dir = storage_dir / _HISTORY_DIR_NAME
        if history_dir.exists():
            _gc_history(history_dir, max_count=0, max_age_days=max_age_days)
        return None

    history_dir = storage_dir / _HISTORY_DIR_NAME
    history_dir.mkdir(parents=True, exist_ok=True)

    base_name = _compute_history_name(rotation_source)
    history_path = _resolve_history_path(history_dir, base_name)

    # Re-serialize from the buffered dict and atomically write into
    # history/. Same tempfile + os.replace pattern as the main write.
    content = json.dumps(rotation_source, indent=2, ensure_ascii=False)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=history_dir,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, history_path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()

    # GC entries exceeding either threshold.
    _gc_history(history_dir, max_count=max_count, max_age_days=max_age_days)

    return history_path


def _gc_history(
    history_dir: Path,
    *,
    max_count: int,
    max_age_days: int,
) -> list[Path]:
    """Drop history/ entries exceeding ``max_count`` or ``max_age_days``.

    Returns the list of paths dropped (for ``oh snapshot gc``
    reporting). Drops oldest-first. Either arm at 0 skips that
    check (max_count=0 keeps no entries; max_age_days=0 disables
    the age arm).

    Failures unlinking individual entries are WARN-logged but
    don't abort the GC pass — best-effort cleanup.
    """
    if not history_dir.exists():
        return []

    entries: list[tuple[float, Path]] = []
    for path in history_dir.iterdir():
        if path.suffix != ".json":
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        entries.append((mtime, path))

    # Sort newest-first so we drop from the END
    entries.sort(key=lambda e: e[0], reverse=True)

    dropped: list[Path] = []

    # Count-based: drop everything past max_count (already in
    # newest-first order, so [max_count:] is the oldest).
    if max_count >= 0:
        to_drop_by_count = entries[max_count:] if max_count > 0 else entries[:]
        for _mtime, path in to_drop_by_count:
            try:
                path.unlink()
                dropped.append(path)
            except OSError as exc:
                _logger.warning(
                    "snapshot_gc_failed",
                    path=str(path),
                    error=str(exc),
                )
        # Remove dropped entries from the working list
        kept = entries[:max_count] if max_count > 0 else []
    else:
        kept = entries

    # Age-based: drop entries older than max_age_days.
    if max_age_days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        for mtime, path in kept:
            if mtime < cutoff:
                try:
                    path.unlink()
                    dropped.append(path)
                except OSError as exc:
                    _logger.warning(
                        "snapshot_gc_failed",
                        path=str(path),
                        error=str(exc),
                    )

    return dropped
