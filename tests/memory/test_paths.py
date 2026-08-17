"""Tests for :func:`get_project_memory_dir` + memory error types — P10-T1.1c.

Two surfaces:

1. **Path resolution** — same cwd → same dir; different cwd → distinct
   dir; symlinks follow to canonical; non-existent dir is returned
   without mkdir.
2. **Error types** — :class:`UnknownMemoryError` carries ``name`` +
   ``available`` for the CLI; :class:`MemoryParseError` is the
   reserved Phase-11 write-path error.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openharness.errors import OpenHarnessError
from openharness.memory.errors import MemoryParseError, UnknownMemoryError
from openharness.memory.paths import ensure_project_memory_dir, get_project_memory_dir


class TestGetProjectMemoryDir:
    def test_under_home_openharness_memory(self) -> None:
        d = get_project_memory_dir("/tmp/foo")
        assert d.parent == Path.home() / ".openharness" / "memory"

    def test_basename_in_dir_name(self) -> None:
        d = get_project_memory_dir("/tmp/my-project")
        assert d.name.startswith("my-project-")

    def test_sha1_suffix_is_12_chars(self) -> None:
        d = get_project_memory_dir("/tmp/foo")
        # Suffix after the "<basename>-" prefix
        suffix = d.name.rsplit("-", 1)[-1]
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_same_cwd_same_dir(self) -> None:
        d1 = get_project_memory_dir("/tmp/foo")
        d2 = get_project_memory_dir("/tmp/foo")
        assert d1 == d2

    def test_different_cwd_same_basename_distinct_dirs(self) -> None:
        # The whole point of the SHA-1 suffix: same basename, different
        # paths, must NOT collide.
        d1 = get_project_memory_dir("/a/foo")
        d2 = get_project_memory_dir("/b/foo")
        assert d1 != d2
        assert d1.name.startswith("foo-")
        assert d2.name.startswith("foo-")

    def test_does_not_create_directory(self, tmp_path: Path) -> None:
        # Acceptance check: resolving a path must not mkdir. Storage is
        # created lazily by Phase 11 writers; reading from a non-existent
        # dir returns an empty store, not "no such dir" error.
        d = get_project_memory_dir(tmp_path / "fresh-project")
        assert not d.exists()

    def test_accepts_string_cwd(self) -> None:
        d_str = get_project_memory_dir("/tmp/foo")
        d_path = get_project_memory_dir(Path("/tmp/foo"))
        assert d_str == d_path

    def test_symlink_resolves_to_canonical(self, tmp_path: Path) -> None:
        # Symlink → real dir → same memory dir. Important on macOS
        # where ``/var`` symlinks to ``/private/var``.
        real = tmp_path / "real-project"
        real.mkdir()
        link = tmp_path / "link-to-project"
        try:
            os.symlink(real, link)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")
        d_real = get_project_memory_dir(real)
        d_link = get_project_memory_dir(link)
        assert d_real == d_link

    def test_relative_path_resolves(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Relative paths must resolve against current cwd before hashing,
        # otherwise ``oh ask`` from ``./foo`` and ``/abs/path/foo`` would
        # split memory.
        sub = tmp_path / "x"
        sub.mkdir()
        monkeypatch.chdir(tmp_path)
        d_rel = get_project_memory_dir("x")
        d_abs = get_project_memory_dir(sub)
        assert d_rel == d_abs


class TestEnsureProjectMemoryDir:
    def test_creates_harness_owned_directory_for_fresh_project(self, tmp_path: Path) -> None:
        project = tmp_path / "fresh-project"
        project.mkdir()

        memory_dir = ensure_project_memory_dir(project)

        assert memory_dir == get_project_memory_dir(project)
        assert memory_dir.is_dir()

    def test_is_idempotent(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        first = ensure_project_memory_dir(project)
        second = ensure_project_memory_dir(project)

        assert first == second
        assert first.is_dir()


class TestUnknownMemoryError:
    def test_inherits_from_openharness_error(self) -> None:
        # Per the errors module convention — must land in cli.py's root
        # ``except OpenHarnessError`` arm.
        err = UnknownMemoryError("foo", available=[])
        assert isinstance(err, OpenHarnessError)

    def test_carries_name_and_available(self) -> None:
        err = UnknownMemoryError("foo", available=["bar", "baz"])
        assert err.name == "foo"
        assert err.available == ["bar", "baz"]

    def test_message_lists_available(self) -> None:
        err = UnknownMemoryError("foo", available=["zeta", "alpha"])
        # Sorted catalog so the message is deterministic regardless of
        # discover() iteration order.
        assert "alpha, zeta" in str(err)
        assert "'foo'" in str(err)

    def test_message_when_no_memories(self) -> None:
        err = UnknownMemoryError("foo", available=[])
        assert "(none)" in str(err)


class TestMemoryParseError:
    def test_inherits_from_openharness_error(self) -> None:
        err = MemoryParseError("reserved for Phase 11")
        assert isinstance(err, OpenHarnessError)

    def test_is_distinct_class(self) -> None:
        # MemoryParseError and UnknownMemoryError must NOT be confused —
        # they sit at different layers (parse vs lookup).
        assert MemoryParseError is not UnknownMemoryError
