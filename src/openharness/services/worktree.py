"""Git worktree management for repair-loop physical isolation — loop-runtime
Track B T1 (L7).

``create_worktree``/``remove_worktree`` shell out to real ``git worktree``
commands via ``asyncio.create_subprocess_exec`` (argv form, never a shell).
Fail-closed: a non-git ``repo_root``, a dirty working tree, or a missing/
non-executable ``git`` binary all raise ``WorktreeError`` rather than
silently falling back to the live cwd or branching from a stale HEAD that
excludes the user's uncommitted edits.

Known limitation: there is a TOCTOU window between the dirty-check and the
actual ``worktree add`` call (two separate subprocess dispatches) -- a
concurrent writer could dirty ``repo_root`` in between. The dirty-check
runs as the LAST check immediately before ``worktree add`` to keep this
window as tight as a single subprocess dispatch allows; fully closing it
would require a repo-wide lock this module has no business taking (it
would affect every other git operation against the repo, not just this
one). Accepted for MVP -- this is a solo-developer CLI, not a
multi-tenant service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class WorktreeError(Exception):
    """Raised when worktree creation or removal cannot proceed safely."""


@dataclass(frozen=True)
class WorktreeHandle:
    path: Path
    branch: str
    base_ref: str
    repo_root: Path


async def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        # Covers FileNotFoundError (git not on PATH) AND other exec
        # failures (e.g. PermissionError on a non-executable git) --
        # both must fail closed as WorktreeError, not escape raw.
        raise WorktreeError(f"failed to execute git {args[0] if args else ''}: {exc}") from exc
    stdout, stderr = await process.communicate()
    returncode = process.returncode if process.returncode is not None else 1
    return returncode, stdout.decode().strip(), stderr.decode().strip()


async def create_worktree(
    repo_root: Path,
    *,
    run_id: str,
    branch_prefix: str = "openharness/run",
    worktrees_root: Path | None = None,
) -> WorktreeHandle:
    returncode, _, stderr = await _run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_root)
    if returncode != 0:
        raise WorktreeError(f"{repo_root} is not a git working tree: {stderr}")

    returncode, base_ref, stderr = await _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    if returncode != 0:
        raise WorktreeError(f"failed to resolve HEAD in {repo_root}: {stderr}")

    branch = f"{branch_prefix}/{run_id}"
    resolved_worktrees_root = worktrees_root or (repo_root.parent / ".openharness-worktrees")
    worktree_path = resolved_worktrees_root / f"{repo_root.name}-{run_id}"

    # Dirty-check runs as the LAST check immediately before the mutating
    # `worktree add` call (not earlier in the sequence) to keep the
    # TOCTOU window between "verified clean" and "branched from HEAD" as
    # tight as a single subprocess dispatch allows -- a full close would
    # need a repo-wide lock this module has no business taking.
    returncode, status_output, stderr = await _run_git(["status", "--porcelain"], cwd=repo_root)
    if returncode != 0:
        raise WorktreeError(f"git status failed in {repo_root}: {stderr}")
    if status_output:
        raise WorktreeError(
            f"working tree {repo_root} is dirty; refusing to branch from a stale HEAD"
        )

    returncode, _, stderr = await _run_git(
        ["worktree", "add", "-b", branch, str(worktree_path), base_ref], cwd=repo_root
    )
    if returncode != 0:
        raise WorktreeError(f"git worktree add failed: {stderr}")

    return WorktreeHandle(
        path=worktree_path.resolve(),
        branch=branch,
        base_ref=base_ref,
        repo_root=repo_root,
    )


async def remove_worktree(handle: WorktreeHandle, *, force: bool = False) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(handle.path))

    returncode, _, stderr = await _run_git(args, cwd=handle.repo_root)
    if returncode != 0:
        raise WorktreeError(f"git worktree remove failed: {stderr}")
