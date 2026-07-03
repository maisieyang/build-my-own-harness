"""Shared exclusive-lock helper for atomic read-modify-write cycles.

Extracted out of ``services/autopilot.py``'s ``_queue_lock`` (L6) when
``services/run_journal.py`` (Track B T2) needed the identical idiom -- a
single implementation now backs both, so a future correctness fix (e.g. a
non-blocking flock with a timeout) only has to land in one place.
"""

from __future__ import annotations

import contextlib
import fcntl
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextlib.contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Exclusive ``fcntl`` lock on a sibling ``<path>.lock`` file.

    Serializes concurrent read-modify-write cycles across threads/processes
    around the (unrelated) file at ``path`` -- callers hold the lock for
    the duration of their own load-mutate-save sequence.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
