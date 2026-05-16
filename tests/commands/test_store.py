"""Tests for ``FilesystemCommandStore`` + ``EmptyCommandStore`` — P5b-T1.1c."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.commands.store import EmptyCommandStore, FilesystemCommandStore

if TYPE_CHECKING:
    from pathlib import Path


def _write_command(directory: Path, name: str, description: str, body: str = "body") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


class TestEmptyCommandStore:
    def test_discover_returns_empty(self) -> None:
        assert EmptyCommandStore().discover() == {}

    def test_get_returns_none(self) -> None:
        assert EmptyCommandStore().get("anything") is None


class TestFilesystemCommandStoreDiscovery:
    def test_both_dirs_none(self) -> None:
        assert FilesystemCommandStore().discover() == {}

    def test_nonexistent_dirs_handled(self, tmp_path: Path) -> None:
        store = FilesystemCommandStore(
            global_dir=tmp_path / "missing-global",
            project_dir=tmp_path / "missing-project",
        )
        assert store.discover() == {}

    def test_global_only(self, tmp_path: Path) -> None:
        _write_command(tmp_path / "global", "review", "Review changes")
        store = FilesystemCommandStore(global_dir=tmp_path / "global")
        discovered = store.discover()
        assert "review" in discovered
        assert discovered["review"].description == "Review changes"

    def test_project_only(self, tmp_path: Path) -> None:
        _write_command(tmp_path / "project", "team-cmd", "Team helper")
        store = FilesystemCommandStore(project_dir=tmp_path / "project")
        assert "team-cmd" in store.discover()

    def test_project_overrides_global(self, tmp_path: Path) -> None:
        _write_command(tmp_path / "global", "shared", "global desc", body="global body")
        _write_command(tmp_path / "project", "shared", "project desc", body="project body")
        store = FilesystemCommandStore(
            global_dir=tmp_path / "global",
            project_dir=tmp_path / "project",
        )
        discovered = store.discover()
        assert discovered["shared"].description == "project desc"
        assert "project body" in discovered["shared"].body

    def test_malformed_file_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "global"
        d.mkdir()
        _write_command(d, "good", "good desc")
        (d / "bad.md").write_text("no frontmatter at all\n")
        store = FilesystemCommandStore(global_dir=d)
        assert set(store.discover().keys()) == {"good"}

    def test_only_md_files_picked_up(self, tmp_path: Path) -> None:
        d = tmp_path / "global"
        d.mkdir()
        _write_command(d, "real", "desc")
        (d / "fake.txt").write_text(
            "---\nname: fake\ndescription: x\n---\nbody\n",
            encoding="utf-8",
        )
        store = FilesystemCommandStore(global_dir=d)
        assert set(store.discover().keys()) == {"real"}


class TestFilesystemCommandStoreCache:
    def test_get_uses_cache(self, tmp_path: Path) -> None:
        _write_command(tmp_path / "global", "x", "desc")
        store = FilesystemCommandStore(global_dir=tmp_path / "global")
        store.discover()
        # File added post-discovery → catalog frozen, not visible.
        _write_command(tmp_path / "global", "y", "desc y")
        assert store.get("y") is None
        assert store.get("x") is not None

    def test_get_warms_cache_on_first_call(self, tmp_path: Path) -> None:
        _write_command(tmp_path / "global", "x", "desc")
        store = FilesystemCommandStore(global_dir=tmp_path / "global")
        assert store.get("x") is not None

    def test_discover_returns_defensive_copy(self, tmp_path: Path) -> None:
        _write_command(tmp_path / "global", "x", "desc")
        store = FilesystemCommandStore(global_dir=tmp_path / "global")
        result = store.discover()
        result.clear()
        assert store.get("x") is not None

    def test_discover_called_twice_does_not_rescan(self, tmp_path: Path) -> None:
        _write_command(tmp_path / "global", "x", "desc")
        store = FilesystemCommandStore(global_dir=tmp_path / "global")
        first = store.discover()
        assert "x" in first and "y" not in first
        _write_command(tmp_path / "global", "y", "desc y")
        second = store.discover()
        assert "y" not in second


class TestFilesystemCommandStoreCollisions:
    def test_same_layer_collision_first_wins(self, tmp_path: Path) -> None:
        d = tmp_path / "global"
        d.mkdir()
        (d / "a-first.md").write_text(
            "---\nname: dup\ndescription: first\n---\nfirst body\n",
            encoding="utf-8",
        )
        (d / "b-second.md").write_text(
            "---\nname: dup\ndescription: second\n---\nsecond body\n",
            encoding="utf-8",
        )
        store = FilesystemCommandStore(global_dir=d)
        discovered = store.discover()
        assert discovered["dup"].description == "first"
