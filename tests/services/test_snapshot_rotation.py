"""Tests for snapshot history/ rotation — P13-T1 (D31.2 + D31.3 + D31.4 + D31.5).

Three surfaces:

1. ``_compute_history_name`` / ``_resolve_history_path`` — pure naming helpers
2. ``_gc_history`` — count + age threshold enforcement
3. ``write_session_snapshot`` extended with rotation step

Phase 12 backward compat: rotation works against pre-existing
``current.json`` written by Phase 12 (which doesn't know about
history/). The first Phase 13 write into a Phase-12 cwd rotates
the existing snapshot into a freshly-created history/.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from openharness.permissions import workspace_runtime_profile
from openharness.protocols import ConversationMessage, TextBlock
from openharness.services.snapshot import (
    SNAPSHOT_SCHEMA,
    _compute_history_name,
    _gc_history,
    _resolve_history_path,
    _rotate_current_to_history,
    get_snapshot_dir,
    write_session_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class _StubContext:
    model: str = "qwen-plus"
    system_prompt: str | None = "test"
    max_tokens: int = 1024
    runtime_permission_profile: object = field(default_factory=workspace_runtime_profile)


def _msg(role: str, text: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=[TextBlock(text=text)])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 1a/1b: pure naming helpers                                                  #
# --------------------------------------------------------------------------- #


class TestComputeHistoryName:
    def test_with_git_head_and_iso_created_at(self) -> None:
        snap = {
            "git_head": "abc1234",
            "created_at": "2026-05-28T14:30:12+00:00",
        }
        assert _compute_history_name(snap) == "abc1234-20260528143012.json"

    def test_zulu_z_suffix_parsed(self) -> None:
        snap = {
            "git_head": "abc1234",
            "created_at": "2026-05-28T14:30:12Z",
        }
        assert _compute_history_name(snap) == "abc1234-20260528143012.json"

    def test_none_git_head_uses_nogit_literal(self) -> None:
        snap = {"git_head": None, "created_at": "2026-05-28T14:30:12+00:00"}
        assert _compute_history_name(snap) == "nogit-20260528143012.json"

    def test_missing_git_head_uses_nogit_literal(self) -> None:
        snap = {"created_at": "2026-05-28T14:30:12+00:00"}
        assert _compute_history_name(snap) == "nogit-20260528143012.json"

    def test_empty_string_git_head_uses_nogit_literal(self) -> None:
        snap = {"git_head": "", "created_at": "2026-05-28T14:30:12+00:00"}
        assert _compute_history_name(snap) == "nogit-20260528143012.json"

    def test_invalid_created_at_falls_back_to_now(self) -> None:
        snap = {"git_head": "abc1234", "created_at": "not a real timestamp"}
        name = _compute_history_name(snap)
        # Must still produce a sortable 14-digit timestamp
        prefix, rest = name.split("-")
        assert prefix == "abc1234"
        digits = rest.removesuffix(".json")
        assert len(digits) == 14
        assert digits.isdigit()


class TestResolveHistoryPath:
    def test_no_collision_returns_direct_path(self, tmp_path: Path) -> None:
        target = _resolve_history_path(tmp_path, "abc1234-20260528.json")
        assert target == tmp_path / "abc1234-20260528.json"

    def test_collision_appends_dash_1(self, tmp_path: Path) -> None:
        (tmp_path / "abc1234-20260528.json").write_text("{}")
        target = _resolve_history_path(tmp_path, "abc1234-20260528.json")
        assert target == tmp_path / "abc1234-20260528-1.json"

    def test_double_collision_appends_dash_2(self, tmp_path: Path) -> None:
        (tmp_path / "abc1234-20260528.json").write_text("{}")
        (tmp_path / "abc1234-20260528-1.json").write_text("{}")
        target = _resolve_history_path(tmp_path, "abc1234-20260528.json")
        assert target == tmp_path / "abc1234-20260528-2.json"

    def test_99_collisions_exhausted_raises(self, tmp_path: Path) -> None:
        (tmp_path / "x-y.json").write_text("{}")
        for n in range(1, 100):  # -1 through -99
            (tmp_path / f"x-y-{n}.json").write_text("{}")
        with pytest.raises(OSError, match="collision exhausted"):
            _resolve_history_path(tmp_path, "x-y.json")


# --------------------------------------------------------------------------- #
# 1c: _gc_history threshold enforcement                                       #
# --------------------------------------------------------------------------- #


class TestGcHistory:
    def test_below_count_threshold_no_drops(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"e{i}.json").write_text("{}")
        dropped = _gc_history(tmp_path, max_count=10, max_age_days=90)
        assert dropped == []
        assert len(list(tmp_path.glob("*.json"))) == 5

    def test_count_threshold_drops_oldest(self, tmp_path: Path) -> None:
        # Create 10 entries, set mtimes so e0 is oldest
        for i in range(10):
            p = tmp_path / f"e{i}.json"
            p.write_text("{}")
            ts = time.time() - (10 - i) * 60  # e0 oldest, e9 newest
            import os as _os

            _os.utime(p, (ts, ts))

        dropped = _gc_history(tmp_path, max_count=5, max_age_days=90)
        # Expect 5 dropped (the oldest 5 = e0..e4)
        assert len(dropped) == 5
        remaining_names = sorted(p.name for p in tmp_path.glob("*.json"))
        assert remaining_names == [f"e{i}.json" for i in range(5, 10)]

    def test_age_threshold_drops_old(self, tmp_path: Path) -> None:
        # Create entries with mixed ages
        import os as _os

        now = time.time()
        for i, age_days in enumerate([1, 30, 60, 91, 200]):
            p = tmp_path / f"e{i}.json"
            p.write_text("{}")
            ts = now - age_days * 86400
            _os.utime(p, (ts, ts))

        dropped = _gc_history(tmp_path, max_count=100, max_age_days=90)
        # e3 (91d) and e4 (200d) should be dropped
        assert len(dropped) == 2
        remaining_names = sorted(p.name for p in tmp_path.glob("*.json"))
        assert remaining_names == ["e0.json", "e1.json", "e2.json"]

    def test_both_thresholds_count_takes_priority(self, tmp_path: Path) -> None:
        # 10 entries; oldest 5 also exceed age, but count drops them first
        import os as _os

        now = time.time()
        for i in range(10):
            p = tmp_path / f"e{i}.json"
            p.write_text("{}")
            age_days = 100 - i * 10  # e0=100d (old), e9=10d (new)
            ts = now - age_days * 86400
            _os.utime(p, (ts, ts))

        dropped = _gc_history(tmp_path, max_count=5, max_age_days=50)
        # Count drops e0..e4 (oldest 5); then age check on remaining
        # e5..e9 (ages 50, 40, 30, 20, 10) — none exceed 50d cutoff
        # (e5 mtime is 50 days exact which is at the boundary)
        assert len(dropped) >= 5

    def test_max_count_zero_drops_everything(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"e{i}.json").write_text("{}")
        dropped = _gc_history(tmp_path, max_count=0, max_age_days=90)
        assert len(dropped) == 5
        assert list(tmp_path.glob("*.json")) == []

    def test_max_age_zero_disables_age_arm(self, tmp_path: Path) -> None:
        # All entries 200 days old; max_age=0 → age arm disabled
        import os as _os

        now = time.time()
        for i in range(3):
            p = tmp_path / f"e{i}.json"
            p.write_text("{}")
            ts = now - 200 * 86400
            _os.utime(p, (ts, ts))

        dropped = _gc_history(tmp_path, max_count=100, max_age_days=0)
        assert dropped == []  # nothing exceeds count; age disabled

    def test_missing_history_dir_no_op(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        dropped = _gc_history(missing, max_count=10, max_age_days=90)
        assert dropped == []

    def test_non_json_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "e1.json").write_text("{}")
        (tmp_path / "README.md").write_text("not a snapshot")
        dropped = _gc_history(tmp_path, max_count=0, max_age_days=90)
        # Only the .json entry counts as a snapshot
        assert len(dropped) == 1
        # README.md untouched
        assert (tmp_path / "README.md").exists()


# --------------------------------------------------------------------------- #
# 1c: _rotate_current_to_history end-to-end                                   #
# --------------------------------------------------------------------------- #


class TestRotateCurrentToHistory:
    def test_first_rotation_creates_history_dir(self, tmp_path: Path) -> None:
        rotation_source = {
            "version": 1,
            "git_head": "abc1234",
            "created_at": "2026-05-28T14:30:12+00:00",
        }
        path = _rotate_current_to_history(
            storage_dir=tmp_path,
            rotation_source=rotation_source,
            max_count=10,
            max_age_days=90,
        )
        assert path is not None
        assert path == tmp_path / "history" / "abc1234-20260528143012.json"
        assert path.exists()

    def test_max_count_zero_skips_write_but_runs_gc(self, tmp_path: Path) -> None:
        # Pre-populate history/ — confirm GC still drops everything
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        (history_dir / "old.json").write_text("{}")

        path = _rotate_current_to_history(
            storage_dir=tmp_path,
            rotation_source={"git_head": "abc"},
            max_count=0,
            max_age_days=90,
        )
        assert path is None  # no new history entry
        assert list(history_dir.glob("*.json")) == []  # GC dropped old

    def test_rotation_content_round_trips(self, tmp_path: Path) -> None:
        source = {
            "version": 1,
            "schema": SNAPSHOT_SCHEMA,
            "git_head": "abc1234",
            "created_at": "2026-05-28T14:30:12+00:00",
            "messages": [],
            "model": "qwen-plus",
        }
        path = _rotate_current_to_history(
            storage_dir=tmp_path,
            rotation_source=source,
            max_count=10,
            max_age_days=90,
        )
        assert path is not None
        loaded = json.loads(path.read_text())
        # round-trip identity preserved
        assert loaded["git_head"] == "abc1234"
        assert loaded["model"] == "qwen-plus"
        assert loaded["schema"] == SNAPSHOT_SCHEMA


# --------------------------------------------------------------------------- #
# 1d: write_session_snapshot integration with rotation                        #
# --------------------------------------------------------------------------- #


class TestWriteSnapshotWithRotation:
    def test_first_write_no_rotation(self, tmp_path: Path) -> None:
        # No pre-existing current.json → no rotation needed
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[_msg("user", "hi")],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        snapshot_dir = get_snapshot_dir(tmp_path)
        assert (snapshot_dir / "current.json").exists()
        history_dir = snapshot_dir / "history"
        # history/ not created on first write
        assert not history_dir.exists()

    def test_second_write_rotates_old_to_history(self, tmp_path: Path) -> None:
        # First write
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[_msg("user", "first")],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        # Second write — old current should rotate
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[_msg("user", "second")],
            context=_StubContext(),  # type: ignore[arg-type]
        )

        snapshot_dir = get_snapshot_dir(tmp_path)
        current = json.loads((snapshot_dir / "current.json").read_text())
        first_block = current["messages"][0]["content"][0]
        assert first_block["text"] == "second"

        history_dir = snapshot_dir / "history"
        history_entries = list(history_dir.glob("*.json"))
        assert len(history_entries) == 1
        rotated = json.loads(history_entries[0].read_text())
        first_block_rotated = rotated["messages"][0]["content"][0]
        assert first_block_rotated["text"] == "first"

    def test_five_writes_history_accumulates(self, tmp_path: Path) -> None:
        for i in range(5):
            write_session_snapshot(
                cwd=tmp_path,
                tool_metadata={},
                messages=[_msg("user", f"turn{i}")],
                context=_StubContext(),  # type: ignore[arg-type]
            )

        snapshot_dir = get_snapshot_dir(tmp_path)
        history_entries = list((snapshot_dir / "history").glob("*.json"))
        # 5 writes → 4 in history (last one is current)
        assert len(history_entries) == 4

    def test_history_max_count_kwarg_respected(self, tmp_path: Path) -> None:
        # 5 writes with max_count=2 → only 2 in history
        for i in range(5):
            write_session_snapshot(
                cwd=tmp_path,
                tool_metadata={},
                messages=[_msg("user", f"turn{i}")],
                context=_StubContext(),  # type: ignore[arg-type]
                history_max_count=2,
            )

        snapshot_dir = get_snapshot_dir(tmp_path)
        history_entries = list((snapshot_dir / "history").glob("*.json"))
        assert len(history_entries) == 2

    def test_rotation_failure_does_not_break_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First write to populate current.json
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[_msg("user", "first")],
            context=_StubContext(),  # type: ignore[arg-type]
        )

        # Force _rotate_current_to_history to raise
        import openharness.services.snapshot as sn

        def _broken_rotation(**_kw: object) -> object:
            raise OSError("rotation simulation failure")

        monkeypatch.setattr(sn, "_rotate_current_to_history", _broken_rotation)

        # Second write must STILL land current.json, just warn-log
        # the rotation failure
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[_msg("user", "second")],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        snapshot_dir = get_snapshot_dir(tmp_path)
        current = json.loads((snapshot_dir / "current.json").read_text())
        first_block = current["messages"][0]["content"][0]
        assert first_block["text"] == "second"


# --------------------------------------------------------------------------- #
# 1e: Phase 12 backward compat                                                #
# --------------------------------------------------------------------------- #


class TestPhase12BackwardCompat:
    def test_phase12_snapshot_rotates_cleanly_on_first_p13_write(self, tmp_path: Path) -> None:
        # Synthesize a Phase 12-shape snapshot (no history/ dir,
        # current.json present with all Phase 12 fields). The first
        # Phase 13 write should rotate it into a freshly-created
        # history/ and put new content in current.json.
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        phase12_snap = {
            "version": 1,
            "schema": "openharness.snapshot.v1",
            "created_at": "2026-05-25T10:00:00+00:00",
            "git_head": "p12abc1",
            "cwd": str(tmp_path.resolve()),
            "model": "qwen-plus",
            "permission_mode": "default",
            "system_prompt": "phase 12 prompt",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "p12 message"}]}],
            "tool_metadata": {
                "recent_files": [],
                "verified_work": [],
                "task_focus_state": {"goal": None, "next_step": None},
            },
            "extra": {},
        }
        (snapshot_dir / "current.json").write_text(json.dumps(phase12_snap))

        # Now run a Phase 13 write
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[_msg("user", "p13 message")],
            context=_StubContext(),  # type: ignore[arg-type]
        )

        # current.json now holds the new Phase 13 content
        new_current = json.loads((snapshot_dir / "current.json").read_text())
        assert new_current["messages"][0]["content"][0]["text"] == "p13 message"

        # history/ now holds the rotated Phase 12 snapshot
        history_dir = snapshot_dir / "history"
        history_entries = list(history_dir.glob("*.json"))
        assert len(history_entries) == 1
        assert history_entries[0].name == "p12abc1-20260525100000.json"
        rotated = json.loads(history_entries[0].read_text())
        assert rotated["system_prompt"] == "phase 12 prompt"

    def test_malformed_current_json_logged_but_not_rotated(self, tmp_path: Path) -> None:
        # Existing current.json is unparseable → log warning, skip
        # rotation, new write still lands
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "current.json").write_text("{{ not valid json")

        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[_msg("user", "new")],
            context=_StubContext(),  # type: ignore[arg-type]
        )

        new_current = json.loads((snapshot_dir / "current.json").read_text())
        assert new_current["messages"][0]["content"][0]["text"] == "new"
        # history/ remains uncreated because rotation source was bad
        history_dir = snapshot_dir / "history"
        assert not history_dir.exists() or list(history_dir.iterdir()) == []


# --------------------------------------------------------------------------- #
# 1f: defaults match D31.2                                                    #
# --------------------------------------------------------------------------- #


class TestSnapshotHistorySettingsDefaults:
    def test_defaults_match_d31_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        from openharness.config.settings import Settings, SnapshotHistorySettings

        h = SnapshotHistorySettings()
        assert h.max_count == 100
        assert h.max_age_days == 90
        # Nested under snapshot
        s = Settings()
        assert s.snapshot.history.max_count == 100
        assert s.snapshot.history.max_age_days == 90

    def test_env_override_triple_nested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT", "200")
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__HISTORY__MAX_AGE_DAYS", "30")
        from openharness.config.settings import Settings

        s = Settings()
        assert s.snapshot.history.max_count == 200
        assert s.snapshot.history.max_age_days == 30


class TestGcFailureIsolation:
    """Coverage backfill — defensive OSError paths in _gc_history."""

    def test_unlink_failure_during_count_drop_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Make 3 entries; force unlink to fail on one of them.
        for i in range(3):
            (tmp_path / f"e{i}.json").write_text("{}")

        from pathlib import Path as _P

        real_unlink = _P.unlink

        def _selective_unlink(self: _P, *args: object, **kwargs: object) -> object:
            if self.name == "e1.json":
                raise OSError("simulated permission failure")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(_P, "unlink", _selective_unlink)

        # max_count=0 drops everything via the count arm
        dropped = _gc_history(tmp_path, max_count=0, max_age_days=90)
        # Only successfully-unlinked entries appear in dropped list
        dropped_names = sorted(p.name for p in dropped)
        assert "e1.json" not in dropped_names
        assert "e0.json" in dropped_names
        assert "e2.json" in dropped_names

    def test_unlink_failure_during_age_drop_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os as _os

        # 2 ancient entries
        now = time.time()
        for i in range(2):
            p = tmp_path / f"old{i}.json"
            p.write_text("{}")
            ts = now - 200 * 86400
            _os.utime(p, (ts, ts))

        from pathlib import Path as _P

        real_unlink = _P.unlink

        def _selective_unlink(self: _P, *args: object, **kwargs: object) -> object:
            if self.name == "old0.json":
                raise OSError("simulated permission failure")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(_P, "unlink", _selective_unlink)

        # Age arm fires; old0 fails to unlink, old1 succeeds
        dropped = _gc_history(tmp_path, max_count=100, max_age_days=90)
        dropped_names = sorted(p.name for p in dropped)
        assert dropped_names == ["old1.json"]


# Silence unused import warning on datetime
_ = (datetime, timedelta, timezone)
