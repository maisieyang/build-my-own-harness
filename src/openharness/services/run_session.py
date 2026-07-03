"""Run-scoped worktree + sandbox + journal lifecycle — loop-runtime Track B T4.

``open_run_session`` owns the ``AsyncExitStack``-style lifecycle for an
entire repair-loop RUN (potentially many ``_run_ask`` attempts), instead of
each attempt building and tearing down its own worktree/sandbox/journal.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.execution.sandbox import SandboxExecution
from openharness.services.run_journal import RunJournal, generate_run_id, get_run_dir
from openharness.services.worktree import create_worktree, remove_worktree

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.cli import SandboxConfig
    from openharness.execution.base import ExecutionEnvironment
    from openharness.services.worktree import WorktreeHandle


@dataclass
class RunSession:
    """Mutable -- callers set ``.status`` before their ``async with`` block
    exits (``open_run_session`` auto-transitions "running" -> "completed"
    on a clean exit and -> "crashed" on an exception, so callers only need
    to set a more specific terminal status, e.g. "failed", themselves).

    Not synchronized: today's callers run repair-loop attempts
    sequentially against one session. If a future caller ever runs
    concurrent attempts against the same session, ``status`` writes would
    race -- out of scope until such a caller actually exists."""

    run_id: str
    cwd_override: Path | None
    execution_env_override: ExecutionEnvironment | None
    worktree: WorktreeHandle | None
    journal: RunJournal | None
    status: str = "running"


@contextlib.asynccontextmanager
async def open_run_session(
    *,
    cwd: Path,
    isolate: bool,
    journal_enabled: bool,
    sandbox_config: SandboxConfig,
    run_id: str | None = None,
) -> AsyncIterator[RunSession | None]:
    if not isolate and not journal_enabled and not sandbox_config.enabled:
        yield None
        return

    resolved_run_id = run_id or generate_run_id()
    session = RunSession(
        run_id=resolved_run_id,
        cwd_override=None,
        execution_env_override=None,
        worktree=None,
        journal=None,
        status="running",
    )
    sandbox: SandboxExecution | None = None

    # Setup (worktree/journal/sandbox) lives INSIDE the try so a failure
    # partway through (e.g. sandbox.__aenter__() raising after the
    # worktree was already created) still runs the finally block below --
    # each cleanup step there is independently guarded by `is not None`
    # checks, so it safely no-ops for whatever wasn't actually created.
    try:
        if isolate:
            handle = await create_worktree(cwd, run_id=resolved_run_id)
            session.worktree = handle
            session.cwd_override = handle.path

        if journal_enabled:
            journal = RunJournal(get_run_dir(cwd, resolved_run_id), resolved_run_id)
            session.journal = journal
            journal.append("run_started", attempt=None)

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
        # Each step is independently best-effort: a failure in one must
        # not prevent the others from running, and must not mask
        # whatever exception is already propagating from the try block
        # above (bare `except Exception: pass` is deliberate here --
        # this is cleanup-of-cleanup, not a place to introduce a NEW
        # unrelated exception that replaces the real one).
        if sandbox is not None:
            with contextlib.suppress(Exception):
                await sandbox.__aexit__(None, None, None)
        if journal_enabled and session.journal is not None:
            with contextlib.suppress(Exception):
                session.journal.append("run_finished", attempt=None, status=session.status)
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
                # Fail-closed: only remove the worktree when the status
                # check ITSELF succeeded (returncode 0) and reported no
                # changes -- a failed check (non-zero exit) must never be
                # treated as "confirmed clean", since that would risk
                # force-removing a worktree with real uncommitted work.
                if proc.returncode == 0 and not stdout.decode().strip():
                    await remove_worktree(session.worktree, force=True)
