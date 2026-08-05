"""Run-scoped worktree and sandbox lifecycle.

The primitive is intentionally independent of completion policy. A caller may
use it to isolate one headless request today or a task-owned execution run in a
future UI without inheriting a completion policy.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.execution.sandbox import SandboxExecution
from openharness.observability import get_logger, new_run_id
from openharness.services.worktree import create_worktree, remove_worktree, verify_existing_worktree

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.cli import SandboxConfig
    from openharness.execution.base import ExecutionEnvironment
    from openharness.services.worktree import WorktreeHandle


logger = get_logger("run_session")


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
    execution_env_override: ExecutionEnvironment | None
    worktree: WorktreeHandle | None
    status: str = "running"


@contextlib.asynccontextmanager
async def open_run_session(
    *,
    cwd: Path,
    isolate: bool,
    sandbox_config: SandboxConfig,
    run_id: str | None = None,
    existing_worktree: WorktreeHandle | None = None,
) -> AsyncIterator[RunSession | None]:
    """Own one run's optional worktree and sandbox, including cleanup."""
    if not isolate and not sandbox_config.enabled:
        yield None
        return

    resolved_run_id = run_id or new_run_id()
    untracked_before = None if isolate else await _untracked_files(cwd)
    session = RunSession(
        run_id=resolved_run_id,
        cwd_override=None,
        execution_env_override=None,
        worktree=None,
    )
    sandbox: SandboxExecution | None = None

    try:
        if isolate:
            if existing_worktree is not None:
                await verify_existing_worktree(existing_worktree)
                handle = existing_worktree
            else:
                handle = await create_worktree(cwd, run_id=resolved_run_id)
            session.worktree = handle
            session.cwd_override = handle.path

        if sandbox_config.enabled:
            sandbox = SandboxExecution(
                cwd=session.cwd_override or cwd,
                image=sandbox_config.image,
                network=sandbox_config.network,
                memory=sandbox_config.memory,
                cpus=sandbox_config.cpus,
                pids=256,
                runtime=sandbox_config.runtime,
            )
            await sandbox.__aenter__()
            session.execution_env_override = sandbox

        yield session
    except BaseException:
        if session.status == "running":
            session.status = "crashed"
        raise
    else:
        if session.status == "running":
            session.status = "completed"
    finally:
        if sandbox is not None:
            with contextlib.suppress(Exception):
                await sandbox.__aexit__(None, None, None)
        if not isolate and untracked_before is not None:
            with contextlib.suppress(Exception):
                after = await _untracked_files(cwd)
                new_files = sorted(after - untracked_before) if after is not None else []
                if new_files:
                    logger.warning(
                        "run_left_untracked_files",
                        files=new_files[:20],
                        count=len(new_files),
                        hint="run without --isolate mutated the live cwd; review or remove these",
                    )
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
