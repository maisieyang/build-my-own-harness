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
from openharness.observability import get_logger
from openharness.services.run_journal import RunJournal, generate_run_id, get_run_dir, load_journal
from openharness.services.worktree import create_worktree, remove_worktree, verify_existing_worktree

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.cli import SandboxConfig
    from openharness.execution.base import ExecutionEnvironment
    from openharness.services.worktree import WorktreeHandle


logger = get_logger("run_session")


async def _untracked_files(cwd: Path) -> set[str] | None:
    """Untracked (non-ignored) files in ``cwd``'s repo, or ``None`` when
    ``cwd`` isn't a git working tree / git itself fails — callers treat
    ``None`` as "can't tell, stay silent"."""
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
    goal: str = "",
    run_id: str | None = None,
    existing_worktree: WorktreeHandle | None = None,
) -> AsyncIterator[RunSession | None]:
    """``existing_worktree``: loop-runtime Track B T7 (``--resume-run``) --
    when given (and ``isolate``), reuse this already-on-disk worktree
    instead of calling ``create_worktree`` (still independently
    revalidated via ``verify_existing_worktree`` -- a caller-supplied
    handle skips ``create_worktree``'s own validation, so a stale or
    concurrently-removed worktree must still fail closed here). The
    caller (``ask()``) reconstructs the handle from the resumed run's
    journal, not ``create_worktree``'s return value.

    ``goal``: recorded on the ``run_started``/``run_resumed`` journal
    event so ``--resume-run`` can later validate the resumed goal without
    depending on a separate persisted-state file.
    """
    if not isolate and not journal_enabled and not sandbox_config.enabled:
        yield None
        return

    resolved_run_id = run_id or generate_run_id()
    # Sprint 2 F3 (dogfood 2026-07-12): non-isolated runs mutate the live
    # cwd — snapshot untracked files now so close can name whatever the
    # run leaves behind (repair-loop models rerouted by permission walls
    # drop artifacts in cwd; the stray fizzbuzz.py of experiment 5).
    untracked_before = None if isolate else await _untracked_files(cwd)
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
            if existing_worktree is not None:
                await verify_existing_worktree(existing_worktree)
                handle = existing_worktree
            else:
                handle = await create_worktree(cwd, run_id=resolved_run_id)
            session.worktree = handle
            session.cwd_override = handle.path

        if journal_enabled:
            journal = RunJournal(get_run_dir(cwd, resolved_run_id), resolved_run_id)
            session.journal = journal
            # A resumed run's journal already has a "run_started" event --
            # append "run_resumed" instead so a single journal.jsonl never
            # ends up with two "run_started" markers (which would make it
            # look like two runs concatenated in one file to any future
            # tooling reconstructing run duration/count from the journal).
            is_resume = any(e["event"] == "run_started" for e in load_journal(journal.run_dir))
            journal.append(
                "run_resumed" if is_resume else "run_started",
                attempt=None,
                goal=goal,
                worktree_path=str(session.worktree.path) if session.worktree is not None else None,
                branch_name=session.worktree.branch if session.worktree is not None else None,
            )

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
                # Fail-closed: only remove the worktree when the status
                # check ITSELF succeeded (returncode 0) and reported no
                # changes -- a failed check (non-zero exit) must never be
                # treated as "confirmed clean", since that would risk
                # force-removing a worktree with real uncommitted work.
                if proc.returncode == 0 and not stdout.decode().strip():
                    await remove_worktree(session.worktree, force=True)
