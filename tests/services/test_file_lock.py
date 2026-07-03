"""Tests for the shared ``exclusive_file_lock`` helper -- extracted out of
``autopilot.py``'s ``_queue_lock`` when ``run_journal.py`` needed the
identical idiom (code-review finding, Track B Wave 1)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from openharness.services.file_lock import exclusive_file_lock

if TYPE_CHECKING:
    from pathlib import Path


class TestExclusiveFileLock:
    def test_serializes_concurrent_critical_sections(self, tmp_path: Path) -> None:
        target = tmp_path / "target.json"
        counter = {"value": 0}

        def _increment() -> None:
            with exclusive_file_lock(target):
                current = counter["value"]
                counter["value"] = current + 1

        threads = [threading.Thread(target=_increment) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter["value"] == 20

    def test_creates_sibling_lock_file(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "target.json"
        with exclusive_file_lock(target):
            pass
        assert (tmp_path / "nested" / "target.json.lock").exists()
