"""Run-scoped worktree lifecycle.

The primitive is intentionally independent of completion policy. A caller may
use it to isolate one headless request today or a task-owned execution run in a
future UI without inheriting a completion policy.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.observability import new_run_id
from openharness.services.worktree import create_worktree, remove_worktree, verify_existing_worktree

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.services.worktree import WorktreeHandle


async def _untracked_files(cwd: Path) -> set[str] | None:
    """Return untracked, non-ignored files or ``None`` when Git cannot answer."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return {line for line in out.decode().splitlines() if line.strip()}


@dataclass
class RunSession:
    """Mutable lifecycle state for one isolated execution."""

    run_id: str
    cwd_override: Path | None
    worktree: WorktreeHandle | None
    status: str = "running"


@contextlib.asynccontextmanager
async def open_run_session(
    *,
    cwd: Path,
    isolate: bool,
    run_id: str | None = None,
    existing_worktree: WorktreeHandle | None = None,
) -> AsyncIterator[RunSession | None]:
    """Own one run's optional worktree, including cleanup.

    Sandbox sessions belong to the CLI/query session that consumes their
    verified boundary.  This worktree lifecycle must never inject the legacy
    ``ExecutionEnvironment`` and bypass that contract.
    """
    if not isolate:
        yield None
        return

    resolved_run_id = run_id or new_run_id()
    session = RunSession(
        run_id=resolved_run_id,
        cwd_override=None,
        worktree=None,
    )

    try:
        if isolate:
            if existing_worktree is not None:
                await verify_existing_worktree(existing_worktree)
                handle = existing_worktree
            else:
                handle = await create_worktree(cwd, run_id=resolved_run_id)
            session.worktree = handle
            session.cwd_override = handle.path

        yield session
    except BaseException:
        if session.status == "running":
            session.status = "crashed"
        raise
    else:
        if session.status == "running":
            session.status = "completed"
    finally:
        if isolate and session.worktree is not None:
            with contextlib.suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "status",
                    "--porcelain",
                    cwd=session.worktree.path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and not stdout.decode().strip():
                    await remove_worktree(session.worktree, force=True)
