"""``HostExecution`` — substrate that runs commands directly on the host — P7-T1.1b.

Per ``decisions/15-phase-7-boundary.md`` D17.2: this is the **identity
transform** for the substrate abstraction. Body is the
``asyncio.create_subprocess_shell`` logic extracted verbatim from
the current ``BashTool.execute`` (P2-T3). Behavior parity is the
load-bearing assertion — all existing BashTool tests must pass
unchanged after the refactor.

Module-level ``_HOST_EXECUTION`` singleton serves as the default for
``QueryContext.execution_env`` since :class:`HostExecution` is
stateless and side-effect-free.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from openharness.execution.base import ProcessResult

if TYPE_CHECKING:
    from pathlib import Path


# Grace period before escalating SIGTERM → SIGKILL. Matches the
# existing Bash behavior (P2-T3 / D9.5).
_KILL_GRACE_PERIOD_SECONDS = 2.0

# Sentinel exit code returned when the process is killed by timeout
# before it could exit cleanly. ``asyncio.subprocess`` doesn't reliably
# set returncode in this case;the substrate normalizes to -1 so callers
# have a stable value to check.
_TIMEOUT_EXIT_CODE = -1


class HostExecution:
    """Run shell commands directly on the host via ``asyncio.subprocess``.

    Default substrate for ``QueryContext.execution_env``. Stateless and
    thread-safe(no instance attributes),so the module-level singleton
    can be shared across queries and across sub-agents inheriting via
    ``dataclasses.replace``.
    """

    async def run_command(
        self,
        command: str,
        cwd: Path,
        timeout: float | None = None,
    ) -> ProcessResult:
        # Body extracted byte-identically from BashTool.execute (P2-T3).
        # Any behavioral drift here will surface as failing BashTool
        # tests — the load-bearing assertion for Phase 7a.
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merged into stdout pipe (chronological)
            cwd=str(cwd),
        )

        try:
            if timeout is None:
                stdout_bytes, _ = await process.communicate()
            else:
                stdout_bytes, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            timed_out = False
        except asyncio.TimeoutError:
            await _terminate_then_kill(process)
            timed_out = True
            stdout_bytes = b""

        if timed_out:
            return ProcessResult(
                output="",
                exit_code=_TIMEOUT_EXIT_CODE,
                timed_out=True,
            )

        output = stdout_bytes.decode("utf-8", errors="replace")
        exit_code = process.returncode if process.returncode is not None else _TIMEOUT_EXIT_CODE
        return ProcessResult(
            output=output,
            exit_code=exit_code,
            timed_out=False,
        )


async def _terminate_then_kill(process: asyncio.subprocess.Process) -> None:
    """Send SIGTERM, wait up to ``_KILL_GRACE_PERIOD_SECONDS``, then SIGKILL.

    Always reaps the process before returning so ``process.returncode``
    is set. Same logic as the original ``_terminate_then_kill`` in
    ``tools/bash.py`` — kept here so the substrate owns the full
    process-lifecycle responsibility.
    """
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=_KILL_GRACE_PERIOD_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


# Module-level singleton — ``QueryContext.execution_env`` defaults to
# this. Tests / programmatic users can construct a fresh
# ``HostExecution()`` if they want a separate instance, but the
# difference is academic since the class is stateless.
_HOST_EXECUTION: HostExecution = HostExecution()
