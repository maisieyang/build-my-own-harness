"""Tests for ``services/run_journal.py`` — loop-runtime Track B T2 (L9 state
machine + journal).

Append-only ``journal.jsonl`` (audit source of truth) + atomic ``state.json``
(read cache), mirroring ``services/autopilot.py``'s atomic-write + ``fcntl``
lock idiom -- but keyed one-directory-per-run rather than one shared queue
file. Fail-closed: a malformed ``state.json`` or a torn/malformed journal
line raises ``ValueError`` with an actionable message (mirrors
``autopilot.load_queue``), never silently returns a default/empty state.
"""

from __future__ import annotations

import json
import re
import threading
from typing import TYPE_CHECKING

import pytest

from openharness.services.run_journal import (
    RunJournal,
    RunState,
    generate_run_id,
    get_run_dir,
    load_journal,
    load_state,
)

if TYPE_CHECKING:
    from pathlib import Path

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")


def _state(run_id: str, **overrides: object) -> RunState:
    base: dict[str, object] = {
        "run_id": run_id,
        "goal": "fix the tests",
        "status": "running",
        "attempt": 1,
        "max_iter": 3,
        "worktree_path": None,
        "branch_name": None,
        "created_at": "2026-07-03T00:00:00+00:00",
        "updated_at": "2026-07-03T00:00:00+00:00",
    }
    base.update(overrides)
    return RunState(**base)  # type: ignore[arg-type]


class TestGenerateRunId:
    def test_matches_expected_shape(self) -> None:
        run_id = generate_run_id()
        assert _RUN_ID_RE.match(run_id), run_id

    def test_two_calls_differ(self) -> None:
        # Different hex suffix even within the same second.
        ids = {generate_run_id() for _ in range(20)}
        assert len(ids) == 20


class TestGetRunDir:
    def test_same_cwd_and_run_id_is_stable(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        a = get_run_dir(cwd, "run-1")
        b = get_run_dir(cwd, "run-1")
        assert a == b

    def test_different_run_ids_are_distinct_dirs(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        a = get_run_dir(cwd, "run-1")
        b = get_run_dir(cwd, "run-2")
        assert a != b

    def test_lives_under_openharness_runs_not_inside_cwd(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        run_dir = get_run_dir(cwd, "run-1")
        assert ".openharness" in run_dir.parts
        assert "runs" in run_dir.parts
        assert not run_dir.is_relative_to(cwd)


class TestRunJournalAppendAndState:
    def test_append_writes_a_journal_line(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-dir"
        journal = RunJournal(run_dir, "run-1")

        journal.append("run_started", attempt=None, goal="do the thing")

        events = load_journal(run_dir)
        assert len(events) == 1
        assert events[0]["event"] == "run_started"
        assert events[0]["goal"] == "do the thing"
        assert events[0]["attempt"] is None

    def test_multiple_appends_are_all_preserved_in_order(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-dir"
        journal = RunJournal(run_dir, "run-1")

        journal.append("run_started", attempt=None)
        journal.append("attempt_started", attempt=1)
        journal.append("attempt_finished", attempt=1, stop_reason="end_turn")
        journal.append("run_finished", attempt=None, status="completed")

        events = load_journal(run_dir)
        assert [e["event"] for e in events] == [
            "run_started",
            "attempt_started",
            "attempt_finished",
            "run_finished",
        ]

    def test_write_state_then_load_state_round_trips(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-dir"
        journal = RunJournal(run_dir, "run-1")
        state = _state("run-1", attempt=2, status="running")

        journal.write_state(state)

        loaded = load_state(run_dir)
        assert loaded == state

    def test_write_state_overwrites_atomically(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-dir"
        journal = RunJournal(run_dir, "run-1")
        journal.write_state(_state("run-1", attempt=1))
        journal.write_state(_state("run-1", attempt=2, status="completed"))

        loaded = load_state(run_dir)
        assert loaded.attempt == 2
        assert loaded.status == "completed"


class TestLoadStateFailClosed:
    def test_missing_state_file_raises_value_error(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "nonexistent-run-dir"
        with pytest.raises(ValueError, match="state"):
            load_state(run_dir)

    def test_malformed_state_json_raises_value_error(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-dir"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text("not json at all", encoding="utf-8")
        with pytest.raises(ValueError):
            load_state(run_dir)

    def test_schema_drifted_state_json_raises_value_error(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-dir"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_state(run_dir)

    def test_wrong_typed_field_raises_value_error(self, tmp_path: Path) -> None:
        """Review fix: field PRESENCE alone isn't enough -- a present but
        wrong-typed field (dict.__getitem__ never raises TypeError on its
        own) must still be rejected."""
        run_dir = tmp_path / "run-dir"
        run_dir.mkdir(parents=True)
        payload = {
            "run_id": "run-1",
            "goal": "fix tests",
            "status": "running",
            "attempt": "not-an-int",
            "max_iter": 3,
            "worktree_path": None,
            "branch_name": None,
            "created_at": "2026-07-03T00:00:00+00:00",
            "updated_at": "2026-07-03T00:00:00+00:00",
        }
        (run_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            load_state(run_dir)

    def test_bool_attempt_raises_value_error(self, tmp_path: Path) -> None:
        """bool is a subclass of int in Python -- must not sneak past an
        ``isinstance(x, int)`` check."""
        run_dir = tmp_path / "run-dir"
        run_dir.mkdir(parents=True)
        payload = {
            "run_id": "run-1",
            "goal": "fix tests",
            "status": "running",
            "attempt": True,
            "max_iter": 3,
            "worktree_path": None,
            "branch_name": None,
            "created_at": "2026-07-03T00:00:00+00:00",
            "updated_at": "2026-07-03T00:00:00+00:00",
        }
        (run_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            load_state(run_dir)


class TestLoadJournalFailClosed:
    def test_missing_journal_returns_empty_list(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "nonexistent-run-dir"
        assert load_journal(run_dir) == []

    def test_malformed_journal_line_raises_value_error(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-dir"
        run_dir.mkdir(parents=True)
        (run_dir / "journal.jsonl").write_text("not json\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_journal(run_dir)

    def test_non_object_json_line_raises_value_error(self, tmp_path: Path) -> None:
        """Review fix: a line that's valid JSON but not an object (e.g. a
        bare int/string/array) must be rejected, not silently returned."""
        run_dir = tmp_path / "run-dir"
        run_dir.mkdir(parents=True)
        (run_dir / "journal.jsonl").write_text("42\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_journal(run_dir)


class TestRunJournalConcurrency:
    def test_concurrent_appends_all_survive(self, tmp_path: Path) -> None:
        """Real multi-threaded test (mirrors the L6 autopilot concurrency
        test that proved data loss without an fcntl lock): 8 threads
        appending concurrently must all land, not last-write-wins."""
        run_dir = tmp_path / "run-dir"
        journal = RunJournal(run_dir, "run-1")

        def _append(i: int) -> None:
            journal.append("attempt_started", attempt=i)

        threads = [threading.Thread(target=_append, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events = load_journal(run_dir)
        assert len(events) == 8
        assert sorted(e["attempt"] for e in events) == list(range(8))

    def test_concurrent_write_state_never_produces_a_torn_read(self, tmp_path: Path) -> None:
        """Review fix: write_state now takes a lock (previously none at
        all). 20 threads each writing a distinct state concurrently must
        always leave state.json fully valid -- readable as ONE complete
        RunState, never a torn/partial file from two interleaved writes."""
        run_dir = tmp_path / "run-dir"
        journal = RunJournal(run_dir, "run-1")

        def _write(i: int) -> None:
            journal.write_state(_state("run-1", attempt=i))

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        loaded = load_state(run_dir)
        assert 0 <= loaded.attempt < 20


class TestRunStateIsFrozen:
    def test_is_frozen(self) -> None:
        import dataclasses

        state = _state("run-1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            state.attempt = 5  # type: ignore[misc]
