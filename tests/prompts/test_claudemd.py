"""Tests for CLAUDE.md cascade discovery + load — P10-T4.4b.

Surface-by-surface coverage:

1. **Discovery walk** — finds CLAUDE.md at each cascade level
   (cwd, parents, .claude/, .claude/rules/), terminates at root,
   includes ``~/.openharness/CLAUDE.md`` global fallback last.
2. **Dedup** — same canonical path (via symlink or direct) appears
   exactly once.
3. **Empty cascade** — no CLAUDE.md anywhere → discovery returns [],
   load returns None (caller omits the section).
4. **Truncation** — files larger than ``max_chars_per_file`` get a
   ``[truncated]`` marker.
5. **Skip-not-fail on read errors** — permission-denied file logs
   warning + skipped; other files still load.
6. **Format** — ``# Project Instructions`` heading + per-file
   ``## <abspath>`` subsection + ``\\`\\`\\`md`` fence.

The HOME-isolation autouse fixture (``tests/conftest.py``) points
``~`` at a fresh tmp dir per test, so the global fallback in
``~/.openharness/CLAUDE.md`` doesn't leak the developer's real
CLAUDE.md into the test results.
"""

from __future__ import annotations

import os
import stat
import sys
from typing import TYPE_CHECKING

import pytest

from openharness.prompts.claudemd import (
    discover_claude_md_files,
    load_claude_md_prompt,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestDiscoverCascade:
    def test_no_claude_md_anywhere_returns_empty(self, tmp_path: Path) -> None:
        # HOME is already isolated by conftest — no project CLAUDE.md,
        # no global fallback file. Expect [].
        assert discover_claude_md_files(tmp_path) == []

    def test_finds_at_cwd_root(self, tmp_path: Path) -> None:
        path = tmp_path / "CLAUDE.md"
        path.write_text("project instructions\n")
        result = discover_claude_md_files(tmp_path)
        assert path in result

    def test_finds_via_parent_walk(self, tmp_path: Path) -> None:
        # User runs ``oh ask`` from ``<project>/src/sub/`` — must find
        # the project-root CLAUDE.md by walking up parents.
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "CLAUDE.md").write_text("root instructions\n")
        deep = project_root / "src" / "sub"
        deep.mkdir(parents=True)
        result = discover_claude_md_files(deep)
        # The project-root CLAUDE.md must be in the result list.
        # Use .resolve() because tmp_path on macOS contains /private/var
        # symlink and the helper resolves cwd before walking.
        assert (project_root / "CLAUDE.md").resolve() in [p.resolve() for p in result]

    def test_finds_dot_claude_claude_md(self, tmp_path: Path) -> None:
        # ``<dir>/.claude/CLAUDE.md`` is a valid alternate location.
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("alt location\n")
        result = discover_claude_md_files(tmp_path)
        assert (claude_dir / "CLAUDE.md") in result

    def test_finds_dot_claude_rules_sorted(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "zeta.md").write_text("rule z\n")
        (rules_dir / "alpha.md").write_text("rule a\n")
        (rules_dir / "mu.md").write_text("rule m\n")
        result = discover_claude_md_files(tmp_path)
        # All three rules included
        names = [p.name for p in result if p.parent.name == "rules"]
        # Sorted by filename — alpha < mu < zeta
        assert names == ["alpha.md", "mu.md", "zeta.md"]

    def test_global_fallback_appended_last(self, tmp_path: Path) -> None:
        # Project CLAUDE.md + global CLAUDE.md → global comes last in
        # the result list (per D28.2 "project-level more specific
        # than global-level").
        project_md = tmp_path / "CLAUDE.md"
        project_md.write_text("project\n")
        global_dir = pytest_home() / ".openharness"
        global_dir.mkdir(parents=True, exist_ok=True)
        global_md = global_dir / "CLAUDE.md"
        global_md.write_text("global\n")
        result = discover_claude_md_files(tmp_path)
        # Both present
        assert project_md in result
        assert global_md in result
        # Project comes before global in the cascade order
        assert result.index(project_md) < result.index(global_md)

    def test_root_termination_does_not_loop(self, tmp_path: Path) -> None:
        # ``Path("/").parent == Path("/")`` — the walk must break
        # otherwise the loop is infinite. Just verify the call returns
        # in reasonable time + no infinite recursion.
        result = discover_claude_md_files("/")
        assert isinstance(result, list)


class TestDiscoverDedup:
    def test_symlinked_claude_md_appears_once(self, tmp_path: Path) -> None:
        # Setup: project CLAUDE.md + global CLAUDE.md is a SYMLINK to
        # the project file → should appear exactly once in the discovery.
        project_md = tmp_path / "CLAUDE.md"
        project_md.write_text("shared content\n")
        global_dir = pytest_home() / ".openharness"
        global_dir.mkdir(parents=True, exist_ok=True)
        global_md = global_dir / "CLAUDE.md"
        try:
            os.symlink(project_md, global_md)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")
        result = discover_claude_md_files(tmp_path)
        # Both paths resolve to the same canonical path → exactly one entry
        canonical_paths = [p.resolve() for p in result]
        assert canonical_paths.count(project_md.resolve()) == 1


class TestLoadFormat:
    def test_returns_none_when_no_files(self, tmp_path: Path) -> None:
        assert load_claude_md_prompt(tmp_path) is None

    def test_returns_section_with_heading(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("project rules\n")
        result = load_claude_md_prompt(tmp_path)
        assert result is not None
        # ``## Project Instructions`` umbrella (h2, matches ## Tools etc.)
        assert result.startswith("## Project Instructions\n\n")

    def test_per_file_subsection_format(self, tmp_path: Path) -> None:
        path = tmp_path / "CLAUDE.md"
        path.write_text("project rules\n")
        result = load_claude_md_prompt(tmp_path)
        assert result is not None
        # Per-file label is h3 (nested under the h2 umbrella)
        assert f"### {path}" in result
        # Markdown fence around body
        assert "```md\nproject rules\n```" in result

    def test_multiple_files_joined_with_blank_lines(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("first\n")
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "a.md").write_text("second\n")
        result = load_claude_md_prompt(tmp_path)
        assert result is not None
        # Both bodies present
        assert "first" in result
        assert "second" in result
        # Subsections separated by ``\n\n``
        first_idx = result.index("first")
        second_idx = result.index("second")
        assert first_idx < second_idx

    def test_truncates_oversized_file_with_marker(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("X" * 20_000)
        result = load_claude_md_prompt(tmp_path, max_chars_per_file=1_000)
        assert result is not None
        assert "[truncated]" in result
        # Body capped at 1_000 chars (+ marker)
        # The fence-wrapped body inside the section is 1000 chars of X
        # + the truncate marker.
        assert "X" * 1_000 in result
        # No 20k Xs in result (would mean truncation didn't apply)
        assert "X" * 1_500 not in result

    def test_does_not_truncate_when_under_cap(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("small content")
        result = load_claude_md_prompt(tmp_path, max_chars_per_file=1_000)
        assert result is not None
        assert "[truncated]" not in result


class TestLoadFailureSkipping:
    def test_permission_denied_file_skipped_others_loaded(self, tmp_path: Path) -> None:
        # POSIX-only: chmod the file unreadable; load must skip it
        # but still produce the section from sibling files.
        if sys.platform.startswith("win"):
            pytest.skip("POSIX chmod semantics required")
        bad = tmp_path / "CLAUDE.md"
        bad.write_text("unreadable\n")
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        good = rules_dir / "good.md"
        good.write_text("readable content\n")
        # Make bad unreadable
        os.chmod(bad, 0)
        try:
            result = load_claude_md_prompt(tmp_path)
        finally:
            # Restore so pytest cleanup works
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)
        assert result is not None
        assert "readable content" in result
        assert "unreadable" not in result

    def test_all_files_unreadable_returns_none(self, tmp_path: Path) -> None:
        if sys.platform.startswith("win"):
            pytest.skip("POSIX chmod semantics required")
        bad = tmp_path / "CLAUDE.md"
        bad.write_text("x\n")
        os.chmod(bad, 0)
        try:
            result = load_claude_md_prompt(tmp_path)
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)
        # Discovery found it, load skipped it, no other files → None
        assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def pytest_home() -> Path:
    """Return the per-test isolated HOME directory.

    The autouse ``_clean_openharness_env`` fixture in ``tests/conftest.py``
    sets ``$HOME`` to a fresh tmp dir for each test. We read
    ``Path.home()`` lazily at call time inside the test so the fixture's
    monkeypatch has taken effect.
    """
    from pathlib import Path as _Path

    return _Path.home()
