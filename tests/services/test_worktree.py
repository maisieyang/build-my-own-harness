"""Tests for ``services/worktree.py`` — loop-runtime Track B T1 (L7 worktree
physical isolation).

``create_worktree``/``remove_worktree`` shell out to real ``git worktree``
commands (via ``asyncio.create_subprocess_exec``, argv form — not a shell).
Fail-closed: a non-git repo_root, a dirty working tree, or a missing ``git``
binary all raise ``WorktreeError`` rather than silently falling back to
operating on the live cwd or branching from a stale HEAD that excludes the
user's uncommitted edits.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from openharness.services.worktree import (
    WorktreeError,
    WorktreeHandle,
    create_worktree,
    remove_worktree,
    verify_existing_worktree,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _head_sha(path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


class TestCreateWorktreeFailClosed:
    async def test_non_git_repo_fails_closed(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()
        with pytest.raises(WorktreeError):
            await create_worktree(not_a_repo, run_id="test-run-1", worktrees_root=tmp_path / "wt")

    async def test_dirty_working_tree_fails_closed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "dirty.txt").write_text("uncommitted\n")
        with pytest.raises(WorktreeError):
            await create_worktree(repo, run_id="test-run-2", worktrees_root=tmp_path / "wt")

    async def test_missing_git_binary_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
        (tmp_path / "empty-bin").mkdir()
        with pytest.raises(WorktreeError):
            await create_worktree(repo, run_id="test-run-3", worktrees_root=tmp_path / "wt")

    async def test_non_executable_git_fails_closed_as_worktree_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review fix: a `git` file that EXISTS on PATH but isn't
        executable raises PermissionError (an OSError subtype other than
        FileNotFoundError) -- must still surface as WorktreeError, not a
        raw uncaught exception."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text("#!/bin/sh\nexit 0\n")
        fake_git.chmod(0o000)
        monkeypatch.setenv("PATH", str(fake_bin))

        with pytest.raises(WorktreeError):
            await create_worktree(repo, run_id="test-run-3b", worktrees_root=tmp_path / "wt")


class TestCreateWorktreeSuccess:
    async def test_success_path_pins_resolved_head_sha_and_creates_branch(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        expected_sha = _head_sha(repo)

        handle = await create_worktree(repo, run_id="test-run-4", worktrees_root=tmp_path / "wt")

        assert isinstance(handle, WorktreeHandle)
        assert handle.repo_root == repo
        assert handle.base_ref == expected_sha
        assert handle.path.exists()
        assert handle.path != repo
        assert "test-run-4" in handle.branch

        branch_check = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=handle.path,
            check=True,
            capture_output=True,
            text=True,
        )
        assert branch_check.stdout.strip() == handle.branch

        await remove_worktree(handle, force=True)
        assert not handle.path.exists()

    async def test_default_worktrees_root_is_sibling_of_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        handle = await create_worktree(repo, run_id="test-run-5")

        try:
            assert handle.path.parent != repo
            assert handle.path.is_relative_to(repo.parent)
            assert not handle.path.is_relative_to(repo)
        finally:
            await remove_worktree(handle, force=True)


class TestVerifyExistingWorktree:
    """Review fix (Track B T7): reused WorktreeHandles (reconstructed from
    persisted state, not returned by create_worktree) must be validated
    before use -- a stale/removed path must fail closed as WorktreeError."""

    async def test_valid_worktree_passes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        handle = await create_worktree(repo, run_id="test-run-7", worktrees_root=tmp_path / "wt")

        await verify_existing_worktree(handle)  # must not raise

        await remove_worktree(handle, force=True)

    async def test_nonexistent_path_fails_closed(self, tmp_path: Path) -> None:
        handle = WorktreeHandle(
            path=tmp_path / "never-existed",
            branch="openharness/run/ghost",
            base_ref="",
            repo_root=tmp_path,
        )
        with pytest.raises(WorktreeError):
            await verify_existing_worktree(handle)

    async def test_removed_worktree_fails_closed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        handle = await create_worktree(repo, run_id="test-run-8", worktrees_root=tmp_path / "wt")
        await remove_worktree(handle, force=True)

        with pytest.raises(WorktreeError):
            await verify_existing_worktree(handle)


class TestRemoveWorktree:
    async def test_remove_worktree_deletes_directory(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        handle = await create_worktree(repo, run_id="test-run-6", worktrees_root=tmp_path / "wt")
        assert handle.path.exists()

        await remove_worktree(handle)

        assert not handle.path.exists()


class TestWorktreeHandleIsFrozen:
    def test_is_frozen(self) -> None:
        handle = WorktreeHandle(path=Path("/x"), branch="b", base_ref="sha", repo_root=Path("/y"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            handle.branch = "other"  # type: ignore[misc]
