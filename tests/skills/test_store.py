"""Tests for ``FilesystemSkillStore`` + ``EmptySkillStore`` — P5c-T1.1c.

Coverage:

- Empty store sentinel.
- Both dirs absent → empty discovery, no crash.
- Global-only discovery.
- Project-only discovery.
- Both dirs:project entries override global on same ``name``.
- Same-layer same-name collision:first wins, warning logged.
- Malformed file inside a directory → skipped, other skills still load
  (the "one bad skill must not crash bootstrap" invariant).
- Discovery is cached:second :meth:`discover` does not re-scan
  (mutating the filesystem after first call yields stale result, which
  is the documented behavior).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.skills.store import EmptySkillStore, FilesystemSkillStore

if TYPE_CHECKING:
    from pathlib import Path


def _write_skill(directory: Path, name: str, description: str, body: str = "body") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


class TestEmptySkillStore:
    def test_discover_returns_empty(self) -> None:
        store = EmptySkillStore()
        assert store.discover() == {}

    def test_get_returns_none(self) -> None:
        store = EmptySkillStore()
        assert store.get("anything") is None


class TestFilesystemSkillStoreDiscovery:
    def test_both_dirs_none(self) -> None:
        store = FilesystemSkillStore()
        assert store.discover() == {}

    def test_nonexistent_dirs_handled_gracefully(self, tmp_path: Path) -> None:
        store = FilesystemSkillStore(
            global_dir=tmp_path / "does-not-exist-global",
            project_dir=tmp_path / "does-not-exist-project",
        )
        assert store.discover() == {}

    def test_empty_dir(self, tmp_path: Path) -> None:
        (tmp_path / "skills").mkdir()
        store = FilesystemSkillStore(global_dir=tmp_path / "skills")
        assert store.discover() == {}

    def test_global_only(self, tmp_path: Path) -> None:
        _write_skill(tmp_path / "global", "react-testing", "React tests")
        _write_skill(tmp_path / "global", "sql-tuning", "Postgres perf")
        store = FilesystemSkillStore(global_dir=tmp_path / "global")
        discovered = store.discover()
        assert set(discovered.keys()) == {"react-testing", "sql-tuning"}
        assert discovered["react-testing"].description == "React tests"

    def test_project_only(self, tmp_path: Path) -> None:
        _write_skill(tmp_path / "project", "team-style", "Team conventions")
        store = FilesystemSkillStore(project_dir=tmp_path / "project")
        discovered = store.discover()
        assert "team-style" in discovered

    def test_project_overrides_global(self, tmp_path: Path) -> None:
        _write_skill(tmp_path / "global", "shared", "global desc", body="global body")
        _write_skill(tmp_path / "project", "shared", "project desc", body="project body")
        store = FilesystemSkillStore(
            global_dir=tmp_path / "global",
            project_dir=tmp_path / "project",
        )
        discovered = store.discover()
        # Project entry replaces global entry for same name.
        assert discovered["shared"].description == "project desc"
        assert "project body" in discovered["shared"].body

    def test_project_adds_without_replacing_unrelated_global(self, tmp_path: Path) -> None:
        _write_skill(tmp_path / "global", "g-only", "only-global")
        _write_skill(tmp_path / "project", "p-only", "only-project")
        store = FilesystemSkillStore(
            global_dir=tmp_path / "global",
            project_dir=tmp_path / "project",
        )
        discovered = store.discover()
        assert set(discovered.keys()) == {"g-only", "p-only"}

    def test_malformed_file_skipped_others_loaded(self, tmp_path: Path) -> None:
        # One good skill + one malformed: discovery must yield the good one.
        good = tmp_path / "global"
        good.mkdir()
        _write_skill(good, "good", "good desc")
        (good / "bad.md").write_text("not a skill at all, no frontmatter\n")
        store = FilesystemSkillStore(global_dir=good)
        discovered = store.discover()
        assert set(discovered.keys()) == {"good"}

    def test_only_md_files_picked_up(self, tmp_path: Path) -> None:
        # Files with non-.md extension are ignored.
        d = tmp_path / "global"
        d.mkdir()
        _write_skill(d, "real", "desc")
        # A .txt file with frontmatter shape is invisible to the store.
        (d / "fake.txt").write_text(
            "---\nname: fake\ndescription: x\n---\nbody\n", encoding="utf-8"
        )
        store = FilesystemSkillStore(global_dir=d)
        discovered = store.discover()
        assert set(discovered.keys()) == {"real"}


class TestFilesystemSkillStoreCache:
    def test_get_uses_cache_after_discover(self, tmp_path: Path) -> None:
        _write_skill(tmp_path / "global", "x", "desc x")
        store = FilesystemSkillStore(global_dir=tmp_path / "global")
        store.discover()
        # Add a new file post-discovery — store should NOT see it
        # (catalog is bootstrap-frozen per decisions/12 Out of Scope).
        _write_skill(tmp_path / "global", "y", "desc y")
        assert store.get("y") is None
        assert store.get("x") is not None

    def test_get_warms_cache_on_first_call(self, tmp_path: Path) -> None:
        # ``get`` called without prior ``discover`` still works — it warms
        # the cache itself.
        _write_skill(tmp_path / "global", "x", "desc x")
        store = FilesystemSkillStore(global_dir=tmp_path / "global")
        assert store.get("x") is not None

    def test_discover_returns_defensive_copy(self, tmp_path: Path) -> None:
        _write_skill(tmp_path / "global", "x", "desc x")
        store = FilesystemSkillStore(global_dir=tmp_path / "global")
        result = store.discover()
        result.clear()  # mutate the returned dict
        # Internal cache must be unaffected.
        assert store.get("x") is not None

    def test_discover_called_twice_does_not_rescan(self, tmp_path: Path) -> None:
        # First discover() populates the cache. A new file written after
        # must NOT show up in the second discover() — bootstrap-frozen
        # semantics per decisions/12 Out of Scope.
        _write_skill(tmp_path / "global", "x", "desc x")
        store = FilesystemSkillStore(global_dir=tmp_path / "global")
        first = store.discover()
        assert "x" in first and "y" not in first
        _write_skill(tmp_path / "global", "y", "desc y")
        second = store.discover()
        assert "y" not in second


class TestFilesystemSkillStoreCollisions:
    def test_same_layer_collision_first_wins(self, tmp_path: Path) -> None:
        # Two files in the same directory both claim ``name: dup``.
        # Sorted ``glob`` ordering is deterministic — ``a-first.md`` is
        # scanned before ``b-second.md``, so the first wins. The second
        # emits a warning and is skipped.
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
        store = FilesystemSkillStore(global_dir=d)
        discovered = store.discover()
        assert discovered["dup"].description == "first"
        assert "first body" in discovered["dup"].body
