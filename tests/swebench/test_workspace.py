"""T3 (D40.5) — repo cache + per-instance workspace + patch extraction.

All tests run against a local fixture repository created in ``tmp_path`` —
zero network, zero external deps, per the repo quality contract.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openharness.swebench.workspace import (
    WorkspaceError,
    ensure_repo_cache,
    extract_patch,
    prepare_workspace,
)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str, str]:
    """A local 'upstream' repo with two commits; returns (path, c1, c2)."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "c1", cwd=repo)
    c1 = git("rev-parse", "HEAD", cwd=repo)
    (repo / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "c2", cwd=repo)
    c2 = git("rev-parse", "HEAD", cwd=repo)
    return repo, c1, c2


class TestEnsureRepoCache:
    def test_clones_bare_once_and_is_idempotent(
        self, tmp_path: Path, upstream: tuple[Path, str, str]
    ) -> None:
        repo_path, _, _ = upstream
        cache_root = tmp_path / "cache"

        first = ensure_repo_cache(cache_root, "acme/widget", clone_url=str(repo_path))
        second = ensure_repo_cache(cache_root, "acme/widget", clone_url="file:///nonexistent")

        assert first == second == cache_root / "acme__widget.git"
        assert first.is_dir()  # bare repo directory

    def test_clone_failure_raises_workspace_error(self, tmp_path: Path) -> None:
        with pytest.raises(WorkspaceError, match="clone"):
            ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(tmp_path / "nope"))


class TestPrepareWorkspace:
    def test_checks_out_base_commit(self, tmp_path: Path, upstream: tuple[Path, str, str]) -> None:
        repo_path, c1, _ = upstream
        cache = ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(repo_path))
        ws = tmp_path / "ws"

        prepare_workspace(cache, c1, ws)

        assert (ws / "app.py").read_text(encoding="utf-8") == "VERSION = 1\n"

    def test_reprepare_gives_clean_workspace(
        self, tmp_path: Path, upstream: tuple[Path, str, str]
    ) -> None:
        repo_path, c1, _ = upstream
        cache = ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(repo_path))
        ws = tmp_path / "ws"
        prepare_workspace(cache, c1, ws)
        (ws / "residue.txt").write_text("left over\n", encoding="utf-8")

        prepare_workspace(cache, c1, ws)

        assert not (ws / "residue.txt").exists()

    def test_bad_commit_raises_workspace_error(
        self, tmp_path: Path, upstream: tuple[Path, str, str]
    ) -> None:
        repo_path, _, _ = upstream
        cache = ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(repo_path))

        with pytest.raises(WorkspaceError, match="checkout"):
            prepare_workspace(cache, "0" * 40, tmp_path / "ws")


class TestExtractPatch:
    def test_includes_modified_and_new_files(
        self, tmp_path: Path, upstream: tuple[Path, str, str]
    ) -> None:
        repo_path, c1, _ = upstream
        cache = ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(repo_path))
        ws = tmp_path / "ws"
        prepare_workspace(cache, c1, ws)
        (ws / "app.py").write_text("VERSION = 1\nFIXED = True\n", encoding="utf-8")
        (ws / "util.py").write_text("def helper() -> None: ...\n", encoding="utf-8")

        patch = extract_patch(ws)

        assert "FIXED = True" in patch
        assert "util.py" in patch

    def test_excludes_openharness_artifacts(
        self, tmp_path: Path, upstream: tuple[Path, str, str]
    ) -> None:
        repo_path, c1, _ = upstream
        cache = ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(repo_path))
        ws = tmp_path / "ws"
        prepare_workspace(cache, c1, ws)
        harness_dir = ws / ".openharness"
        harness_dir.mkdir()
        (harness_dir / "session.json").write_text("{}", encoding="utf-8")
        (ws / "app.py").write_text("VERSION = 1\nFIXED = True\n", encoding="utf-8")

        patch = extract_patch(ws)

        assert "FIXED = True" in patch
        assert ".openharness" not in patch

    def test_no_changes_gives_empty_patch(
        self, tmp_path: Path, upstream: tuple[Path, str, str]
    ) -> None:
        repo_path, c1, _ = upstream
        cache = ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(repo_path))
        ws = tmp_path / "ws"
        prepare_workspace(cache, c1, ws)

        assert extract_patch(ws) == ""

    def test_patch_applies_cleanly_on_fresh_checkout(
        self, tmp_path: Path, upstream: tuple[Path, str, str]
    ) -> None:
        """The emitted patch must survive ``git apply --check`` — the exact
        operation the evaluation side performs."""
        repo_path, c1, _ = upstream
        cache = ensure_repo_cache(tmp_path / "cache", "acme/widget", clone_url=str(repo_path))
        ws = tmp_path / "ws"
        prepare_workspace(cache, c1, ws)
        (ws / "app.py").write_text("VERSION = 1\nFIXED = True\n", encoding="utf-8")
        patch = extract_patch(ws)

        fresh = tmp_path / "fresh"
        prepare_workspace(cache, c1, fresh)
        patch_file = tmp_path / "fix.patch"
        patch_file.write_text(patch, encoding="utf-8")
        subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=fresh,
            capture_output=True,
            text=True,
            check=True,
        )
