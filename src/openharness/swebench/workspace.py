"""Repo cache + per-instance workspace + patch extraction (D40.5).

Strategy: one ``git clone --bare`` per repository into the cache root
(saves ~300 network clones across the Lite set), then a fresh local clone
per instance checkout — instances never share mutable state. The patch is
``git add -A && git diff --cached`` (captures new files too), with
``.openharness/`` pathspec-excluded so harness runtime artifacts can never
contaminate a prediction.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from openharness.swebench.errors import SWEBenchError

if TYPE_CHECKING:
    from pathlib import Path

_GIT_TIMEOUT_S = 600.0
# Pathspec magic: everything except harness runtime artifacts.
_PATHSPEC = (".", ":(exclude).openharness")


class WorkspaceError(SWEBenchError):
    """A git operation on the cache or a workspace failed."""

    def __init__(self, operation: str, detail: str) -> None:
        super().__init__(f"git {operation} failed: {detail}")
        self.operation = operation
        self.detail = detail


def _run_git(args: list[str], *, cwd: Path | None, operation: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(operation, str(exc)) from exc
    if result.returncode != 0:
        raise WorkspaceError(operation, result.stderr.strip() or f"exit {result.returncode}")
    return result.stdout


def repo_cache_path(cache_root: Path, repo: str) -> Path:
    """``owner/name`` → ``<cache_root>/owner__name.git`` (SWE-bench id style)."""
    return cache_root / (repo.replace("/", "__") + ".git")


def ensure_repo_cache(cache_root: Path, repo: str, *, clone_url: str | None = None) -> Path:
    """Bare-clone ``repo`` into the cache once; later calls are offline no-ops."""
    target = repo_cache_path(cache_root, repo)
    if target.is_dir():
        return target
    cache_root.mkdir(parents=True, exist_ok=True)
    url = clone_url if clone_url is not None else f"https://github.com/{repo}.git"
    _run_git(["clone", "--bare", url, str(target)], cwd=None, operation="clone")
    return target


def prepare_workspace(cache_path: Path, base_commit: str, dest: Path) -> None:
    """Fresh clone from the cache at ``base_commit`` into ``dest``.

    An existing ``dest`` is removed first — re-preparing the same instance
    always yields a clean tree (D40.5: zero cross-run residue).
    """
    if dest.exists():
        shutil.rmtree(dest)
    _run_git(["clone", str(cache_path), str(dest)], cwd=None, operation="clone")
    _run_git(["checkout", "--detach", base_commit], cwd=dest, operation="checkout")


def extract_patch(workspace: Path) -> str:
    """Stage everything (minus ``.openharness/``) and return the cached diff
    against the checked-out base commit. Empty string = model changed nothing.
    """
    _run_git(["add", "-A", "--", *_PATHSPEC], cwd=workspace, operation="add")
    return _run_git(["diff", "--cached", "--", *_PATHSPEC], cwd=workspace, operation="diff")
