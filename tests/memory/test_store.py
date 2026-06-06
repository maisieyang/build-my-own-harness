"""Tests for :class:`FilesystemMemoryStore` + :class:`EmptyMemoryStore` — P10-T2.

The store is a thin subclass of
:class:`openharness.markdown_store.FilesystemMarkdownStore[Memory]`;
these tests pin the **memory-specific** contract on top of the shared
substrate's behavior:

1. Empty / missing dir → empty discover (no mkdir, no raise).
2. Valid memory in dir → discoverable via ``discover()`` and ``get()``.
3. Malformed files mixed with valid → valid loaded, malformed skipped
   (skip-not-fail discipline from :func:`parse_memory`).
4. ``scope: team`` loaded since Phase 11 D29.10 enabled the enum value
   (Phase 10 D28.5 rejected it). Team-routing-to-subdir is a WRITE
   concern tested in TestAddOrUpdate.
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

from pathlib import Path  # used at runtime in TestAddOrUpdate (P11-T4.4a)

import pytest

from openharness.memory.model import Memory, MemoryScope, MemoryType, parse_memory
from openharness.memory.store import (
    EmptyMemoryStore,
    FilesystemMemoryStore,
    MemoryStore,
)

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

    def test_memory_md_index_silently_skipped(self, tmp_path: Path) -> None:
        """P17-T3 (D37.5): MEMORY.md is reserved as the LLM-visible
        index file (D36.10), never a memory body. discover() must
        skip it without firing a ``memory_missing_frontmatter`` or
        any other warning, regardless of the MEMORY.md content shape.
        Dogfood (2026-06-06) showed Phase 16 left this warning on
        every session startup; T3 closes it.
        """
        d = tmp_path / "memdir"
        _write_memory(d, "a.md", name="alpha", id_="01HA000000")
        d.mkdir(exist_ok=True)
        # Index-shaped content — no frontmatter, just pointer lines.
        (d / "MEMORY.md").write_text("- [Alpha](a.md) — first entry\n", encoding="utf-8")
        store = FilesystemMemoryStore(project_dir=d)
        catalog = store.discover()
        assert set(catalog.keys()) == {"alpha"}, (
            "MEMORY.md should not appear as a discovered memory"
        )

    def test_memory_md_only_yields_empty_store(self, tmp_path: Path) -> None:
        """P17-T3 (D37.5): a fresh project that has only seeded the
        MEMORY.md index file but no memory bodies yet → discover
        returns an empty dict cleanly, without warnings."""
        d = tmp_path / "memdir"
        d.mkdir()
        (d / "MEMORY.md").write_text("# Memory index\n\n(empty)\n", encoding="utf-8")
        store = FilesystemMemoryStore(project_dir=d)
        assert store.discover() == {}


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

    def test_scope_team_loaded_in_phase_11(self, tmp_path: Path) -> None:
        # Phase 11 D29.10 enables team scope. Team memories at the
        # project_dir root are surfaced same as private (separating
        # team/ subdir routing is the WRITE-time concern handled by
        # add_or_update). Tests for the team subdir live in
        # TestAddOrUpdate below.
        d = tmp_path / "memdir"
        _write_memory(d, "good.md", name="good")
        (d / "team-scoped.md").write_text(
            _VALID_FRONTMATTER_TEMPLATE.format(
                id_="01HTEAM00000",
                name="team-mem",
                description="team-shared",
                body="body",
            ).replace("scope: private", "scope: team")
        )
        store = FilesystemMemoryStore(project_dir=d)
        catalog = store.discover()
        assert "good" in catalog
        assert "team-mem" in catalog

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


# ---------------------------------------------------------------------------
# P11-T4.4a — add_or_update (write path)
# ---------------------------------------------------------------------------


class TestAddOrUpdate:
    """``FilesystemMemoryStore.add_or_update`` — the agent write path
    introduced in Phase 11 alongside extraction's secondary pass."""

    @staticmethod
    def _make_memory(
        *,
        name: str = "test-mem",
        body: str = "test body",
        scope: MemoryScope = MemoryScope.PRIVATE,
        id_: str = "01HABCDEFGHIJK",
    ) -> Memory:
        from datetime import datetime, timezone

        from openharness.memory.model import compute_memory_signature

        signature = compute_memory_signature(body, MemoryType.PROJECT, scope)
        return Memory(
            id=id_,
            name=name,
            description=f"description for {name}",
            type=MemoryType.PROJECT,
            scope=scope,
            created_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
            body=body,
            signature=signature,
            source_path=Path("/tmp/placeholder.md"),  # overridden by add_or_update
        )

    def test_new_memory_writes_to_slug_filename(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        memory = self._make_memory(name="stripe-sdk")
        written = store.add_or_update(memory)
        assert written.name == "stripe-sdk.md"
        assert written.parent == tmp_path
        # Re-parse round-trips
        reparsed = parse_memory(written)
        assert reparsed is not None
        assert reparsed.name == "stripe-sdk"
        assert reparsed.body.strip() == "test body"

    def test_signature_dedup_overwrites_existing(self, tmp_path: Path) -> None:
        # Same body+type+scope → same signature → overwrite, not new file
        store = FilesystemMemoryStore(project_dir=tmp_path)
        m1 = self._make_memory(name="stripe-sdk", body="content v1")
        path1 = store.add_or_update(m1)

        # Same body → same signature; updated metadata gets persisted
        m2 = self._make_memory(name="stripe-sdk-renamed", body="content v1")
        path2 = store.add_or_update(m2)

        # SAME file written to (signature match) despite name change
        assert path1 == path2
        # Only one file in dir
        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1
        # New metadata took effect
        reparsed = parse_memory(path2)
        assert reparsed is not None
        assert reparsed.name == "stripe-sdk-renamed"

    def test_different_signature_writes_new_file(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        m1 = self._make_memory(name="alpha", body="content alpha", id_="01HALPHA0000")
        m2 = self._make_memory(name="beta", body="content beta", id_="01HBETA00000")
        path1 = store.add_or_update(m1)
        path2 = store.add_or_update(m2)
        assert path1 != path2
        # Both files present
        assert path1.exists()
        assert path2.exists()

    def test_filename_collision_uses_id_suffix(self, tmp_path: Path) -> None:
        # Two different memories with the SAME ``name`` (different
        # bodies → different signatures). The first claims ``<slug>.md``;
        # the second falls back to ``<slug>-<id-suffix>.md``.
        store = FilesystemMemoryStore(project_dir=tmp_path)
        m1 = self._make_memory(name="dup", body="body a", id_="01HFIRST0001")
        m2 = self._make_memory(name="dup", body="body b", id_="01HSECOND002")
        path1 = store.add_or_update(m1)
        path2 = store.add_or_update(m2)
        assert path1.name == "dup.md"
        # Second falls back to slug + id-suffix
        assert path2.name.startswith("dup-")
        assert path2.name.endswith(".md")
        assert path1 != path2

    def test_team_scope_writes_to_team_subdir(self, tmp_path: Path) -> None:
        # D29.10: team memories under ``<project_dir>/team/``
        store = FilesystemMemoryStore(project_dir=tmp_path)
        team_mem = self._make_memory(name="team-shared", scope=MemoryScope.TEAM)
        written = store.add_or_update(team_mem)
        assert written.parent == tmp_path / "team"
        assert written.name == "team-shared.md"

    def test_private_and_team_dont_collide(self, tmp_path: Path) -> None:
        # Same name, different scope → different storage subdirs →
        # different paths, both retained
        store = FilesystemMemoryStore(project_dir=tmp_path)
        private = self._make_memory(
            name="same-name", body="private body", scope=MemoryScope.PRIVATE
        )
        team = self._make_memory(name="same-name", body="team body", scope=MemoryScope.TEAM)
        p_priv = store.add_or_update(private)
        p_team = store.add_or_update(team)
        assert p_priv != p_team
        assert p_priv.parent == tmp_path
        assert p_team.parent == tmp_path / "team"

    def test_discover_cache_invalidated_after_write(self, tmp_path: Path) -> None:
        # Before Phase 11: discover() was frozen for the store's
        # lifetime. add_or_update must invalidate the cache so the
        # newly-written memory shows up on next discover().
        store = FilesystemMemoryStore(project_dir=tmp_path)
        assert store.discover() == {}  # caches the empty result
        store.add_or_update(self._make_memory(name="fresh"))
        # Re-discover surfaces the write
        catalog = store.discover()
        assert "fresh" in catalog

    def test_no_orphan_tmp_files_on_success(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        store.add_or_update(self._make_memory(name="x"))
        files = sorted(p.name for p in tmp_path.iterdir())
        assert files == ["x.md"]
