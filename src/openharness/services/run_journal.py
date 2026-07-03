"""Per-run append-only journal + atomic state snapshot — loop-runtime Track B T2.

Append-only ``journal.jsonl`` (audit source of truth) + atomic ``state.json``
(read cache), mirroring ``services/autopilot.py``'s atomic-write + ``fcntl``
lock idiom -- but keyed one-directory-per-run rather than one shared queue
file.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from openharness.services.file_lock import exclusive_file_lock


def generate_run_id() -> str:
    """Return ``<YYYYMMDDThhmmssZ>-<6 lowercase hex chars>``.

    The hex suffix comes from ``os.urandom`` (not a counter), so two calls
    within the same second still differ.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"{timestamp}-{suffix}"


def get_run_dir(cwd: Path, run_id: str) -> Path:
    """Resolve the per-run journal directory for ``cwd`` + ``run_id``.

    Same hash algorithm as :func:`openharness.services.snapshot.get_snapshot_dir`
    (basename + sha1[:12] of resolved cwd), under a sibling ``runs/`` tree.
    ``Path.home()`` evaluated at call time (NOT module scope) so the
    HOME-isolation fixture in ``tests/conftest.py`` takes effect.

    Review fix (Track B T8): ``run_id`` is user-controlled (CLI flags like
    ``--resume-run``/``oh run show`` pass it through directly) -- reject
    any value containing a path separator or ``..`` segment rather than
    joining it in unsanitized, which would let a crafted run_id escape the
    intended ``runs/`` tree entirely.
    """
    if "/" in run_id or "\\" in run_id or run_id in ("..", "."):
        raise ValueError(f"invalid run_id {run_id!r}: must not contain path separators")
    resolved = Path(cwd).resolve()
    digest = sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".openharness" / "runs" / f"{resolved.name}-{digest}" / run_id


@dataclass(frozen=True)
class RunState:
    """Atomic point-in-time snapshot of a repair-loop run."""

    run_id: str
    goal: str
    status: str
    attempt: int
    max_iter: int
    worktree_path: str | None
    branch_name: str | None
    created_at: str
    updated_at: str


class RunJournal:
    """Append-only event journal + atomic state snapshot for one run."""

    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_dir = run_dir
        self.run_id = run_id

    def append(self, event: str, *, attempt: int | None = None, **data: object) -> None:
        """Append one JSON line to ``run_dir / "journal.jsonl"``."""
        journal_path = self.run_dir / "journal.jsonl"
        entry = {"event": event, "attempt": attempt, **data}
        line = json.dumps(entry)

        with exclusive_file_lock(journal_path), open(journal_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def write_state(self, state: RunState) -> None:
        """Atomically overwrite ``run_dir / "state.json"``.

        Locked (same sibling ``.lock`` file ``append`` uses on
        ``journal.jsonl``'s path -- state.json's own lock path, held for
        the duration of this write) so a concurrent ``write_state`` call
        can't silently lose an update via last-``os.replace``-wins.
        """
        self.run_dir.mkdir(parents=True, exist_ok=True)
        state_path = self.run_dir / "state.json"
        content = json.dumps(asdict(state), indent=2)

        with exclusive_file_lock(state_path):
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.run_dir,
                    delete=False,
                    suffix=".tmp",
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(content)
                os.replace(tmp_path, state_path)
                tmp_path = None
            finally:
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)


_STATE_STR_FIELDS = ("run_id", "goal", "status", "created_at", "updated_at")
_STATE_INT_FIELDS = ("attempt", "max_iter")
_STATE_OPTIONAL_STR_FIELDS = ("worktree_path", "branch_name")


def _validate_state_field_types(raw: dict[str, Any]) -> None:
    """Type-check every ``RunState`` field -- ``dict.__getitem__`` on a
    present key never raises ``TypeError``, so field-presence checks alone
    let a wrong-typed value (e.g. ``attempt`` as a string) through
    silently. Explicit ``isinstance`` checks close that gap."""
    for field in _STATE_STR_FIELDS:
        if not isinstance(raw[field], str):
            raise TypeError(f"{field!r} must be a string, got {type(raw[field]).__name__}")
    for field in _STATE_INT_FIELDS:
        if not isinstance(raw[field], int) or isinstance(raw[field], bool):
            raise TypeError(f"{field!r} must be an int, got {type(raw[field]).__name__}")
    for field in _STATE_OPTIONAL_STR_FIELDS:
        if raw[field] is not None and not isinstance(raw[field], str):
            raise TypeError(f"{field!r} must be a string or null, got {type(raw[field]).__name__}")


def load_state(run_dir: Path) -> RunState:
    """Read ``run_dir / "state.json"``.

    Raises ``ValueError`` with an actionable message if the file doesn't
    exist, if the JSON is malformed, or if required fields are
    missing/wrong type.
    """
    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise ValueError(f"no state.json found at {state_path}")
    try:
        raw: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
        _validate_state_field_types(raw)
        return RunState(
            run_id=raw["run_id"],
            goal=raw["goal"],
            status=raw["status"],
            attempt=raw["attempt"],
            max_iter=raw["max_iter"],
            worktree_path=raw["worktree_path"],
            branch_name=raw["branch_name"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            f"state.json at {state_path} is malformed: {exc}. "
            "Fix or delete it manually to continue."
        ) from exc


def load_journal(run_dir: Path) -> list[dict[str, Any]]:
    """Read ``run_dir / "journal.jsonl"`` line by line.

    Returns ``[]`` if the file doesn't exist -- a run that hasn't
    appended anything yet is not malformed. Raises ``ValueError`` if any
    line fails to parse as JSON or doesn't parse to a JSON object.
    """
    journal_path = run_dir / "journal.jsonl"
    if not journal_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"journal.jsonl at {journal_path} has a malformed line: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"journal.jsonl at {journal_path} has a line that is not a JSON "
                f"object (got {type(parsed).__name__}): {line!r}"
            )
        events.append(parsed)
    return events


def get_run_started_event(events: list[dict[str, Any]], *, run_id: str) -> dict[str, Any]:
    """Find and validate a run's original ``run_started`` event -- the
    single source of truth for ``goal``/``worktree_path``/``branch_name``,
    shared by every reader of the journal (``--resume-run``, ``oh run
    show``) so there's exactly one fail-closed reconstruction to keep in
    sync with the journal schema, not one copy per caller.

    Raises ``ValueError`` (fail-closed) if no ``run_started`` event exists
    at all, or if it's missing a required field -- never silently
    substitutes a placeholder for genuinely corrupted/truncated input.
    """
    started = next((e for e in events if e.get("event") == "run_started"), None)
    if started is None:
        raise ValueError(
            f"no 'run_started' event found for run {run_id!r} -- journal may be "
            "corrupted or truncated"
        )
    for field in ("goal", "worktree_path", "branch_name"):
        if field not in started:
            raise ValueError(
                f"'run_started' event for run {run_id!r} is missing required field "
                f"{field!r} -- journal may be corrupted or schema-drifted"
            )
    return started


def get_run_status(events: list[dict[str, Any]]) -> str:
    """Derive the run's current status from journal event ORDER (list
    position = chronological order, since the journal is append-only).

    A ``run_resumed`` event that occurs AFTER the last ``run_finished``
    means the run is actively executing again right now -- reports
    "running", not the stale terminal status left over from the PRIOR
    session (which --resume-run does not overwrite; it only appends new
    events).

    Known limitation: ``open_run_session``'s cleanup wraps its own
    ``run_finished`` append in ``contextlib.suppress(Exception)`` (a
    deliberate best-effort choice -- one cleanup step failing must not
    block the others). If that specific write fails (disk full, lock
    contention), a run that actually terminated will report "running"
    forever, since there's no other signal a journal reader can use to
    detect termination. Accepted for MVP; the write failure mode itself
    is rare and this module has no way to distinguish it from a
    genuinely still-running process without adding liveness-probing
    machinery (e.g. a PID/heartbeat file) that isn't built yet.
    """
    run_finished_indices = [i for i, e in enumerate(events) if e.get("event") == "run_finished"]
    run_resumed_indices = [i for i, e in enumerate(events) if e.get("event") == "run_resumed"]
    if run_resumed_indices and (
        not run_finished_indices or run_resumed_indices[-1] > run_finished_indices[-1]
    ):
        return "running"
    if run_finished_indices:
        last_finished = events[run_finished_indices[-1]]
        status = last_finished.get("status")
        return status if isinstance(status, str) else "unknown"
    return "running"
