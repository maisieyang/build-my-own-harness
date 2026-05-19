"""Tests for ``FilesystemMarkdownStore`` + ``EmptyMarkdownStore`` — P8-T1.

Uses a stub dataclass + parser so the generic store can be exercised
without coupling to any specific domain (commands/skills/bundles).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.markdown_store import (
    EmptyMarkdownStore,
    FilesystemMarkdownStore,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class _FakeDoc:
    """Minimal :class:`MarkdownDocument` Protocol implementation."""

    name: str
    source_path: Path


def _fake_parser(path: Path) -> _FakeDoc | None:
    """Parse a one-line ``name: <value>`` markdown file.

    Returns ``None`` on missing name or unreadable file — mirrors
    the never-raise discipline of real parsers.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("name: "):
            name = line.removeprefix("name: ").strip()
            if name:
                return _FakeDoc(name=name, source_path=path)
    return None


def _write_doc(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(f"name: {name}\n", encoding="utf-8")


class TestEmptyMarkdownStore:
    def test_discover_returns_empty(self) -> None:
        store: EmptyMarkdownStore[_FakeDoc] = EmptyMarkdownStore()
        assert store.discover() == {}

    def test_get_returns_none(self) -> None:
        store: EmptyMarkdownStore[_FakeDoc] = EmptyMarkdownStore()
        assert store.get("anything") is None


class TestFilesystemMarkdownStore:
    def test_both_dirs_none(self) -> None:
        store: FilesystemMarkdownStore[_FakeDoc] = FilesystemMarkdownStore(
            parser=_fake_parser, log_event_prefix="fake"
        )
        assert store.discover() == {}

    def test_global_only(self, tmp_path: Path) -> None:
        _write_doc(tmp_path / "global", "alpha")
        _write_doc(tmp_path / "global", "beta")
        store: FilesystemMarkdownStore[_FakeDoc] = FilesystemMarkdownStore(
            global_dir=tmp_path / "global",
            parser=_fake_parser,
            log_event_prefix="fake",
        )
        catalog = store.discover()
        assert set(catalog.keys()) == {"alpha", "beta"}

    def test_project_overrides_global(self, tmp_path: Path) -> None:
        _write_doc(tmp_path / "global", "shared")
        # Project override: same name, different source.
        (tmp_path / "project").mkdir()
        (tmp_path / "project" / "shared.md").write_text(
            "name: shared\nextra-marker: project\n", encoding="utf-8"
        )
        store: FilesystemMarkdownStore[_FakeDoc] = FilesystemMarkdownStore(
            global_dir=tmp_path / "global",
            project_dir=tmp_path / "project",
            parser=_fake_parser,
            log_event_prefix="fake",
        )
        doc = store.get("shared")
        assert doc is not None
        assert str(doc.source_path).endswith("project/shared.md")

    def test_parser_returns_none_skips(self, tmp_path: Path) -> None:
        # Bad file (no "name: " line) gets skipped by parser.
        (tmp_path / "global").mkdir()
        (tmp_path / "global" / "good.md").write_text("name: good\n", encoding="utf-8")
        (tmp_path / "global" / "bad.md").write_text("garbage\n", encoding="utf-8")
        store: FilesystemMarkdownStore[_FakeDoc] = FilesystemMarkdownStore(
            global_dir=tmp_path / "global",
            parser=_fake_parser,
            log_event_prefix="fake",
        )
        assert set(store.discover().keys()) == {"good"}

    def test_only_md_picked_up(self, tmp_path: Path) -> None:
        d = tmp_path / "global"
        d.mkdir()
        _write_doc(d, "real")
        (d / "ignore.txt").write_text("name: ignored\n", encoding="utf-8")
        store: FilesystemMarkdownStore[_FakeDoc] = FilesystemMarkdownStore(
            global_dir=d, parser=_fake_parser, log_event_prefix="fake"
        )
        assert set(store.discover().keys()) == {"real"}

    def test_missing_dir_skipped(self, tmp_path: Path) -> None:
        # Non-existent global_dir returns empty (skip-not-fail).
        store: FilesystemMarkdownStore[_FakeDoc] = FilesystemMarkdownStore(
            global_dir=tmp_path / "does_not_exist",
            parser=_fake_parser,
            log_event_prefix="fake",
        )
        assert store.discover() == {}

    def test_cache_stable(self, tmp_path: Path) -> None:
        _write_doc(tmp_path / "global", "x")
        store: FilesystemMarkdownStore[_FakeDoc] = FilesystemMarkdownStore(
            global_dir=tmp_path / "global",
            parser=_fake_parser,
            log_event_prefix="fake",
        )
        first = store.discover()
        _write_doc(tmp_path / "global", "y")
        # Cache means y not seen on the second call (bootstrap frozen).
        second = store.discover()
        assert "y" not in second
        assert first.keys() == second.keys()
