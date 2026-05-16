"""Tests for ``FilesystemBundleStore`` + ``EmptyBundleStore`` — P5d-T1."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.bundles.store import EmptyBundleStore, FilesystemBundleStore

if TYPE_CHECKING:
    from pathlib import Path


def _write_bundle(directory: Path, name: str, description: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n",
        encoding="utf-8",
    )


class TestEmptyBundleStore:
    def test_discover_empty(self) -> None:
        assert EmptyBundleStore().discover() == {}

    def test_get_none(self) -> None:
        assert EmptyBundleStore().get("anything") is None


class TestFilesystemBundleStore:
    def test_both_dirs_none(self) -> None:
        assert FilesystemBundleStore().discover() == {}

    def test_global_only(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "global", "review", "Review mode")
        store = FilesystemBundleStore(global_dir=tmp_path / "global")
        assert "review" in store.discover()
        assert store.get("review") is not None
        assert store.get("missing") is None

    def test_project_overrides_global(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "global", "shared", "global version")
        _write_bundle(tmp_path / "project", "shared", "project version")
        store = FilesystemBundleStore(
            global_dir=tmp_path / "global",
            project_dir=tmp_path / "project",
        )
        b = store.get("shared")
        assert b is not None
        assert b.description == "project version"

    def test_malformed_file_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "global"
        d.mkdir()
        _write_bundle(d, "good", "good bundle")
        (d / "bad.md").write_text("no frontmatter\n", encoding="utf-8")
        store = FilesystemBundleStore(global_dir=d)
        # Bad bundle skipped; good one still loaded.
        assert set(store.discover().keys()) == {"good"}

    def test_only_md_picked_up(self, tmp_path: Path) -> None:
        d = tmp_path / "global"
        d.mkdir()
        _write_bundle(d, "real", "y")
        (d / "fake.txt").write_text("---\nname: fake\n---\n", encoding="utf-8")
        store = FilesystemBundleStore(global_dir=d)
        assert set(store.discover().keys()) == {"real"}

    def test_cache_stable(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "global", "x", "d")
        store = FilesystemBundleStore(global_dir=tmp_path / "global")
        first = store.discover()
        _write_bundle(tmp_path / "global", "y", "d")
        second = store.discover()
        # Cache means y not seen on the second call (bootstrap frozen).
        assert "y" not in second
        assert first.keys() == second.keys()
