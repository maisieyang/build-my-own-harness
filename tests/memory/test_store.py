"""Tests for :class:`FilesystemMemoryStore` + :class:`EmptyMemoryStore` — P10-T2.

The store is a thin subclass of
:class:`openharness.markdown_store.FilesystemMarkdownStore[Memory]`;
these tests pin the **memory-specific** contract on top of the shared
substrate's behavior:

1. Empty / missing dir → empty discover (no mkdir, no raise).
2. Valid memory in dir → discoverable via ``discover()`` and ``get()``.
3. Malformed files mixed with valid → valid loaded, malformed skipped
   (skip-not-fail discipline from :func:`parse_memory`).
4. ``scope: team`` files dropped at parser layer (D28.5 enforcement
   re-verified at store level — defense in depth against accidental
   future store-layer bypass).
5. Same-name collision within the single project layer → later sorted
   wins + ``memory_override`` info log (substrate inherits the same
   "project layer overrides" semantics it uses for global+project).
6. ``discover()`` cached on first call (second call returns same
   snapshot without re-scanning).
7. :class:`EmptyMemoryStore` is a usable :class:`MemoryStore` sentinel.

The shared substrate (scan / merge / skip-malformed) has its own
tests under ``tests/markdown_store/``; we do not re-test the generic
behavior here — only the memory-domain integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openharness.memory.model import MemoryScope, MemoryType
from openharness.memory.store import (
    EmptyMemoryStore,
    FilesystemMemoryStore,
    MemoryStore,
)

if TYPE_CHECKING:
    from pathlib import Path


_VALID_FRONTMATTER_TEMPLATE = """\
---
id: {id_}
name: {name}
description: {description}
type: project
scope: private
created_at: 2026-05-26T10:00:00+00:00
updated_at: 2026-05-26T10:00:00+00:00
---

{body}
"""


def _write_memory(
    dir_: Path,
    filename: str,
    *,
    name: str,
    id_: str = "01HXXXXXXXX",
    description: str = "Test memory",
    body: str = "Body",
) -> Path:
    """Write a minimal valid memory file and return its path."""
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / filename
    path.write_text(
        _VALID_FRONTMATTER_TEMPLATE.format(id_=id_, name=name, description=description, body=body)
    )
    return path


class TestFilesystemMemoryStoreEmpty:
    def test_nonexistent_dir_discovers_empty(self, tmp_path: Path) -> None:
        # No mkdir, no raise — the substrate's _merge_dir short-circuits
        # on missing dir, which is the right behavior for "fresh project
        # with no memories yet."
        store = FilesystemMemoryStore(project_dir=tmp_path / "never-created")
        assert store.discover() == {}

    def test_empty_dir_discovers_empty(self, tmp_path: Path) -> None:
        (tmp_path / "memdir").mkdir()
        store = FilesystemMemoryStore(project_dir=tmp_path / "memdir")
        assert store.discover() == {}

    def test_dir_with_non_md_files_ignored(self, tmp_path: Path) -> None:
        # Substrate globs ``*.md`` — anything else gets skipped silently
        # (matches commands/skills/bundles).
        d = tmp_path / "memdir"
        d.mkdir()
        (d / "README.txt").write_text("not a memory")
        (d / "notes.org").write_text("also not")
        store = FilesystemMemoryStore(project_dir=d)
        assert store.discover() == {}


class TestFilesystemMemoryStoreHappy:
    def test_single_valid_memory_discoverable(self, tmp_path: Path) -> None:
        d = tmp_path / "memdir"
        path = _write_memory(d, "stripe.md", name="stripe-sdk")
        store = FilesystemMemoryStore(project_dir=d)
        catalog = store.discover()
        assert set(catalog.keys()) == {"stripe-sdk"}
        m = catalog["stripe-sdk"]
        assert m.name == "stripe-sdk"
        assert m.type is MemoryType.PROJECT
        assert m.scope is MemoryScope.PRIVATE
        assert m.source_path == path

    def test_get_by_name(self, tmp_path: Path) -> None:
        d = tmp_path / "memdir"
        _write_memory(d, "a.md", name="alpha")
        _write_memory(d, "b.md", name="beta")
        store = FilesystemMemoryStore(project_dir=d)
        assert store.get("alpha") is not None
        assert store.get("beta") is not None
        assert store.get("missing") is None

    def test_multiple_valid_memories_all_discovered(self, tmp_path: Path) -> None:
        d = tmp_path / "memdir"
        _write_memory(d, "a.md", name="alpha", id_="01HA000000")
        _write_memory(d, "b.md", name="beta", id_="01HB000000")
        _write_memory(d, "c.md", name="gamma", id_="01HC000000")
        store = FilesystemMemoryStore(project_dir=d)
        catalog = store.discover()
        assert set(catalog.keys()) == {"alpha", "beta", "gamma"}


class TestFilesystemMemoryStoreFaultTolerance:
    def test_malformed_mixed_with_valid_only_valid_loaded(self, tmp_path: Path) -> None:
        d = tmp_path / "memdir"
        # 1 valid
        _write_memory(d, "good.md", name="good")
        # 2 malformed
        (d / "no_frontmatter.md").write_text("just body\n")
        (d / "bad_yaml.md").write_text("---\nname: [unbalanced\n---\nbody\n")
        # 1 more valid
        _write_memory(d, "also-good.md", name="also-good", id_="01HZ000000")
        store = FilesystemMemoryStore(project_dir=d)
        catalog = store.discover()
        # Valid loaded; malformed silently dropped (warnings logged by parser).
        assert set(catalog.keys()) == {"good", "also-good"}

    def test_scope_team_dropped_at_store_layer(self, tmp_path: Path) -> None:
        # D28.5 defense in depth — parser rejects ``scope: team`` (P10-T1.1b
        # test covers that), and the store's discover() must therefore not
        # surface it. Pin the behavior at the store level too.
        d = tmp_path / "memdir"
        # Valid sibling so we can confirm the store still produces a catalog.
        _write_memory(d, "good.md", name="good")
        (d / "team-scoped.md").write_text(
            _VALID_FRONTMATTER_TEMPLATE.format(
                id_="01HTEAM00000",
                name="team-mem",
                description="should be dropped",
                body="body",
            ).replace("scope: private", "scope: team")
        )
        store = FilesystemMemoryStore(project_dir=d)
        catalog = store.discover()
        assert "good" in catalog
        assert "team-mem" not in catalog

    def test_duplicate_name_later_sorted_wins(self, tmp_path: Path) -> None:
        # Substrate behavior: ``project`` layer treats same-name as
        # "override" (info-level ``memory_override`` log) and the later
        # sorted file wins. This is the same semantics commands /
        # skills / bundles inherit when their project layer overrides
        # global — for Phase 10's single-project-layer store, the same
        # rule applies within the layer.
        #
        # Mostly a user-error case (Phase 11 extraction will dedup via
        # signature so two same-name files won't normally co-exist).
        # Pin the behavior so future store changes are intentional.
        d = tmp_path / "memdir"
        _write_memory(d, "a-version.md", name="dup", id_="01HFIRST0000", body="loses")
        _write_memory(d, "z-version.md", name="dup", id_="01HSECOND000", body="wins")
        store = FilesystemMemoryStore(project_dir=d)
        catalog = store.discover()
        assert "dup" in catalog
        m = catalog["dup"]
        # ``z-version.md`` is sorted later → wins
        assert m.source_path.name == "z-version.md"
        assert m.body.strip() == "wins"


class TestFilesystemMemoryStoreCaching:
    def test_discover_cached_on_first_call(self, tmp_path: Path) -> None:
        d = tmp_path / "memdir"
        _write_memory(d, "a.md", name="alpha")
        store = FilesystemMemoryStore(project_dir=d)
        first = store.discover()
        # Add a NEW memory file AFTER first discover() — the store should
        # NOT pick it up on second call (cache is frozen for the store's
        # lifetime, no hot reload in Phase 10).
        _write_memory(d, "b.md", name="beta")
        second = store.discover()
        assert set(first.keys()) == {"alpha"}
        assert set(second.keys()) == {"alpha"}  # NOT {"alpha", "beta"}

    def test_get_uses_same_cache(self, tmp_path: Path) -> None:
        d = tmp_path / "memdir"
        _write_memory(d, "a.md", name="alpha")
        store = FilesystemMemoryStore(project_dir=d)
        assert store.get("alpha") is not None
        # Add another file — get should NOT see it.
        _write_memory(d, "b.md", name="beta")
        assert store.get("beta") is None

    def test_discover_returns_copy_not_internal_dict(self, tmp_path: Path) -> None:
        # Substrate behavior: discover() returns ``dict(self._cache)``
        # — caller mutating the returned dict must NOT corrupt the cache.
        d = tmp_path / "memdir"
        _write_memory(d, "a.md", name="alpha")
        store = FilesystemMemoryStore(project_dir=d)
        catalog1 = store.discover()
        del catalog1["alpha"]
        catalog2 = store.discover()
        assert "alpha" in catalog2


class TestEmptyMemoryStore:
    def test_discover_empty(self) -> None:
        assert EmptyMemoryStore().discover() == {}

    def test_get_returns_none(self) -> None:
        assert EmptyMemoryStore().get("anything") is None

    def test_satisfies_memory_store_protocol(self) -> None:
        # MemoryStore is a structural Protocol; any duck-typed satisfier
        # works. Pin at compile time so future Protocol changes break
        # loudly here.
        store: MemoryStore = EmptyMemoryStore()
        assert store.discover() == {}


class TestFilesystemMemoryStoreProtocolConformance:
    def test_satisfies_memory_store_protocol(self, tmp_path: Path) -> None:
        store: MemoryStore = FilesystemMemoryStore(project_dir=tmp_path)
        assert store.discover() == {}


@pytest.mark.parametrize("store_cls", [EmptyMemoryStore, FilesystemMemoryStore])
def test_get_missing_returns_none(store_cls: type, tmp_path: Path) -> None:
    """Both store types return None on missing-name lookup — the
    contract :class:`MemoryStore` Protocol requires."""
    store = store_cls(project_dir=tmp_path) if store_cls is FilesystemMemoryStore else store_cls()
    assert store.get("nonexistent") is None
