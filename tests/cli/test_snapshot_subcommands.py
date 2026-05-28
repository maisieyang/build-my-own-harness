"""Tests for ``oh snapshot list / show / gc`` — P13-T2 (D31.6).

Mirrors ``tests/cli/test_memory_subcommands.py`` structure. Three
subcommands x happy + error paths x text + json formats.

Tests construct snapshot files directly via Phase 12/13 writers
into the conftest-isolated cwd so the CLI subcommands see them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.services.snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    get_snapshot_dir,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _seed_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _write_synth_snapshot(
    cwd: Path,
    *,
    target: str = "current",  # "current" or "history"
    git_head: str | None = "abc1234",
    created_iso: str = "2026-05-28T14:30:12+00:00",
    name: str | None = None,
    messages: list[dict[str, object]] | None = None,
) -> Path:
    """Hand-author a snapshot file. ``target="history"`` writes under
    history/ with the D31.4 naming convention; ``target="current"``
    writes current.json.
    """
    snapshot_dir = get_snapshot_dir(cwd)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SNAPSHOT_VERSION,
        "schema": SNAPSHOT_SCHEMA,
        "created_at": created_iso,
        "git_head": git_head,
        "cwd": str(cwd.resolve()),
        "model": "qwen-plus",
        "permission_mode": "default",
        "system_prompt": "prior system prompt",
        "max_tokens": 1024,
        "messages": messages or [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "tool_metadata": {
            "recent_files": [],
            "verified_work": [],
            "task_focus_state": {"goal": None, "next_step": None},
        },
        "extra": {},
    }
    if target == "current":
        path = snapshot_dir / "current.json"
    else:
        history_dir = snapshot_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        # Match the D31.4 naming for predictable test IDs
        head_part = git_head or "nogit"
        from datetime import datetime as _dt

        ts = _dt.fromisoformat(created_iso.replace("Z", "+00:00")).strftime("%Y%m%d%H%M%S")
        path = history_dir / (name or f"{head_part}-{ts}.json")
    path.write_text(json.dumps(payload))
    return path


def _cwd_path() -> Path:
    """Return the conftest-isolated cwd as a Path."""
    import os as _os
    from pathlib import Path as _P

    return _P(_os.getcwd())


# --------------------------------------------------------------------------- #
# oh snapshot list                                                            #
# --------------------------------------------------------------------------- #


class TestSnapshotList:
    def test_no_snapshots_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "list"])
        assert result.exit_code == 0
        assert "no snapshots" in result.stdout.lower()

    def test_no_snapshots_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "list", "--format", "json"])
        assert result.exit_code == 0
        assert result.stdout.strip() == "[]"

    def test_current_only_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        _write_synth_snapshot(_cwd_path(), target="current")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "list"])
        assert result.exit_code == 0
        assert "current" in result.stdout
        assert "abc1234" in result.stdout

    def test_current_plus_history_sorted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        cwd = _cwd_path()
        _write_synth_snapshot(cwd, target="current")
        # Two history entries with different timestamps
        _write_synth_snapshot(
            cwd,
            target="history",
            git_head="old1234",
            created_iso="2026-05-26T10:00:00+00:00",
        )
        _write_synth_snapshot(
            cwd,
            target="history",
            git_head="new5678",
            created_iso="2026-05-27T12:00:00+00:00",
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "list"])
        assert result.exit_code == 0
        out = result.stdout
        # current first
        current_pos = out.find("current")
        new_pos = out.find("new5678")
        old_pos = out.find("old1234")
        assert current_pos != -1
        assert new_pos != -1
        assert old_pos != -1
        assert current_pos < new_pos < old_pos  # newest history first

    def test_format_json_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        _write_synth_snapshot(_cwd_path(), target="current")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "list", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        entry = data[0]
        assert entry["id"] == "current"
        assert entry["git_head"] == "abc1234"
        assert "created_at" in entry
        assert "message_count" in entry
        assert "path" in entry


# --------------------------------------------------------------------------- #
# oh snapshot show                                                            #
# --------------------------------------------------------------------------- #


class TestSnapshotShow:
    def test_show_current_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        _write_synth_snapshot(_cwd_path(), target="current")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "current"])
        assert result.exit_code == 0
        assert "id:" in result.stdout
        assert "abc1234" in result.stdout
        assert "messages:" in result.stdout

    def test_show_history_by_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        cwd = _cwd_path()
        _write_synth_snapshot(
            cwd,
            target="history",
            git_head="abc1234",
            created_iso="2026-05-27T12:00:00+00:00",
        )
        runner = CliRunner()
        # Prefix match against git_head
        result = runner.invoke(cli_module.app, ["snapshot", "show", "abc"])
        assert result.exit_code == 0
        assert "abc1234" in result.stdout

    def test_show_ambiguous_prefix_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        cwd = _cwd_path()
        _write_synth_snapshot(
            cwd,
            target="history",
            git_head="abc1111",
            created_iso="2026-05-26T10:00:00+00:00",
        )
        _write_synth_snapshot(
            cwd,
            target="history",
            git_head="abc2222",
            created_iso="2026-05-27T12:00:00+00:00",
        )
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "abc"])
        assert result.exit_code == 1
        assert "ambiguous" in result.stderr.lower()

    def test_show_not_found_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "deadbee"])
        assert result.exit_code == 1
        assert "no snapshot" in result.stderr.lower()

    def test_show_current_missing_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        # Only history, no current.json
        _write_synth_snapshot(
            _cwd_path(),
            target="history",
            git_head="abc1234",
        )
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "current"])
        assert result.exit_code == 1
        assert "current" in result.stderr.lower()

    def test_show_format_json_roundtrips_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        _write_synth_snapshot(_cwd_path(), target="current")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "current", "--format", "json"])
        assert result.exit_code == 0
        # Output should parse as JSON + have the version field
        data = json.loads(result.stdout)
        assert data["version"] == SNAPSHOT_VERSION
        assert data["git_head"] == "abc1234"


# --------------------------------------------------------------------------- #
# oh snapshot gc                                                              #
# --------------------------------------------------------------------------- #


class TestSnapshotGc:
    def test_gc_no_history_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "gc"])
        assert result.exit_code == 0
        assert "no snapshots" in result.stdout.lower()

    def test_gc_drops_over_count_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT", "2")
        cwd = _cwd_path()
        # 5 history entries
        for i in range(5):
            _write_synth_snapshot(
                cwd,
                target="history",
                git_head=f"abc{i:04d}",
                created_iso=f"2026-05-2{5 - i}T10:00:00+00:00",
            )
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "gc"])
        assert result.exit_code == 0
        # 5 entries, max_count=2, should drop 3
        assert "Dropped 3" in result.stdout

    def test_gc_dry_run_reports_without_dropping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT", "1")
        cwd = _cwd_path()
        for i in range(3):
            _write_synth_snapshot(
                cwd,
                target="history",
                git_head=f"abc{i:04d}",
                created_iso=f"2026-05-2{5 - i}T10:00:00+00:00",
            )
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "gc", "--dry-run"])
        assert result.exit_code == 0
        assert "Would drop 2" in result.stdout
        # All 3 entries still on disk
        snapshot_dir = get_snapshot_dir(cwd)
        history_dir = snapshot_dir / "history"
        assert len(list(history_dir.glob("*.json"))) == 3

    def test_gc_dry_run_nothing_to_drop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        cwd = _cwd_path()
        # Just 1 entry; default max_count=100 → nothing to drop
        _write_synth_snapshot(cwd, target="history", git_head="abc1234")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "gc", "--dry-run"])
        assert result.exit_code == 0
        assert "nothing to drop" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# --help discoverability                                                      #
# --------------------------------------------------------------------------- #


class TestSnapshotShowRenderEdgeCases:
    """Coverage for the show command's message-rendering branches."""

    def test_show_renders_tool_use_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        _write_synth_snapshot(
            _cwd_path(),
            target="current",
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "read x"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "/x"}}
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
                },
            ],
        )
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "current"])
        assert result.exit_code == 0
        assert "[tool] Read" in result.stdout
        assert "[result] ok" in result.stdout

    def test_show_renders_unknown_block_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        _write_synth_snapshot(
            _cwd_path(),
            target="current",
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "image", "source": {"data": "..."}}],
                },
            ],
        )
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "current"])
        assert result.exit_code == 0
        assert "[image]" in result.stdout

    def test_show_truncates_long_system_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        cwd = _cwd_path()
        # Write snapshot with a 500-char system_prompt
        snapshot_dir = get_snapshot_dir(cwd)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SNAPSHOT_VERSION,
            "schema": SNAPSHOT_SCHEMA,
            "created_at": "2026-05-28T14:30:12+00:00",
            "git_head": "abc1234",
            "cwd": str(cwd.resolve()),
            "model": "qwen-plus",
            "permission_mode": "default",
            "system_prompt": "x" * 500,
            "max_tokens": 1024,
            "messages": [],
            "tool_metadata": {},
            "extra": {},
        }
        (snapshot_dir / "current.json").write_text(json.dumps(payload))
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "current"])
        assert result.exit_code == 0
        # 240 chars + "..." truncation marker
        assert "..." in result.stdout
        # The literal 500-char prompt should NOT appear in full
        assert "x" * 500 not in result.stdout


class TestSnapshotListMalformed:
    def test_list_skips_malformed_history_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A valid current.json + a malformed history entry → list
        # skips the bad one without erroring
        _seed_required_env(monkeypatch)
        cwd = _cwd_path()
        _write_synth_snapshot(cwd, target="current", git_head="good567")
        # Hand-corrupt a history entry
        snapshot_dir = get_snapshot_dir(cwd)
        history_dir = snapshot_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        (history_dir / "broken-20260101000000.json").write_text("{{ not valid")

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "list"])
        assert result.exit_code == 0
        # Good entry shown
        assert "good567" in result.stdout
        # Broken one silently skipped (no traceback)


class TestSnapshotShowMalformed:
    def test_show_malformed_json_errors_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Hand-corrupt current.json — show falls into the
        # JSONDecodeError branch + exits 1
        _seed_required_env(monkeypatch)
        cwd = _cwd_path()
        snapshot_dir = get_snapshot_dir(cwd)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "current.json").write_text("{{ not valid json")

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "show", "current"])
        assert result.exit_code == 1
        assert "unparseable" in result.stderr.lower()


class TestSnapshotGcAge:
    def test_gc_drops_by_age_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        # Force age threshold = 1 day; mtime set to 30 days ago
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__HISTORY__MAX_AGE_DAYS", "1")
        cwd = _cwd_path()
        path = _write_synth_snapshot(
            cwd,
            target="history",
            git_head="abc1234",
            created_iso="2026-04-28T10:00:00+00:00",
        )
        # Set mtime to 30 days ago so age arm fires
        import os as _os
        import time as _time

        thirty_days_ago = _time.time() - 30 * 86400
        _os.utime(path, (thirty_days_ago, thirty_days_ago))

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "gc"])
        assert result.exit_code == 0
        assert "Dropped 1" in result.stdout


class TestSnapshotHelp:
    def test_snapshot_help_lists_all_subcommands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLUMNS", "200")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["snapshot", "--help"])
        assert result.exit_code == 0
        # All 3 subcommands present
        assert "list" in result.stdout
        assert "show" in result.stdout
        assert "gc" in result.stdout

    def test_top_level_help_mentions_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLUMNS", "200")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["--help"])
        assert result.exit_code == 0
        assert "snapshot" in result.stdout
