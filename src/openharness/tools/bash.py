"""Bash tool -- P2-T3 sub-unit 3d.

Executes a shell command via :func:`asyncio.create_subprocess_shell`, in
``context.cwd``. Per phase-2-plan.md + D9.5:

- Default timeout 600s (overridable per-call via ``timeout_seconds``).
- ``stdout`` and ``stderr`` are merged into a single output stream.
- On timeout: ``SIGTERM`` -> 2s grace -> ``SIGKILL``.
- Output truncated at 12,000 chars (with a tail marker).
- Empty stdout returns ``"(no output)"`` sentinel (P3-T1.1b) — aligns with
  Read's ``(empty)`` and Grep's ``(no matches)``.
- ``exit_code`` and ``duration_ms`` go to ``metadata`` (D9.5: output is for
  the LLM, metadata is for programs).

Per D9.4: **no deny-list at this layer**. Bash trusts what it receives.
P2-T6 ``PermissionChecker`` enforces safety one layer up.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


DEFAULT_TIMEOUT_SECONDS = 600
KILL_GRACE_PERIOD_SECONDS = 2.0
MAX_OUTPUT_CHARS = 12_000

# P3-T1.1b: empty-stdout sentinel. Aligns with Read's ``(empty)`` and Grep's
# ``(no matches)`` — gives the LLM a non-empty string to read so it can
# disambiguate "command ran, produced no output" from "tool didn't run /
# something stripped the output". Source: OpenHarness REFERENCE A.3.
NO_OUTPUT_SENTINEL = "(no output)"


class BashInput(BaseModel):
    """Input schema for :class:`Bash`."""

    command: str = Field(min_length=1, description="Shell command to execute.")
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Override the default 600s timeout. Process is SIGTERM'd on timeout, "
            "then SIGKILL'd 2s later if still alive."
        ),
    )


class Bash(BaseTool[BashInput]):
    """Run a shell command via /bin/sh and return the merged stdout/stderr."""

    name = "Bash"
    description = (
        "Execute a shell command in the project's cwd. Merges stdout/stderr; "
        "default 600s timeout (override via timeout_seconds). Output truncated "
        "at 12,000 chars. exit_code and duration_ms appear in metadata."
    )
    input_model = BashInput

    async def execute(
        self,
        args: BashInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        timeout = (
            args.timeout_seconds if args.timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS
        )

        start = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            args.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merged into stdout pipe
            cwd=str(context.cwd),
        )

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            timed_out = False
        except asyncio.TimeoutError:
            await _terminate_then_kill(process)
            timed_out = True
            stdout_bytes = b""

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if timed_out:
            return ToolResult(
                is_error=True,
                output=f"command timed out after {timeout}s",
                metadata={
                    "exit_code": process.returncode,
                    "duration_ms": elapsed_ms,
                    "timed_out": True,
                },
            )

        output = stdout_bytes.decode("utf-8", errors="replace")
        # Strict empty-only: only the empty-bytes case triggers the sentinel;
        # whitespace-only output (e.g., bare ``echo`` -> "\n") passes through
        # unchanged. is_error is decided downstream by exit_code.
        if output == "":
            output = NO_OUTPUT_SENTINEL
        elif len(output) > MAX_OUTPUT_CHARS:
            dropped = len(output) - MAX_OUTPUT_CHARS
            output = output[:MAX_OUTPUT_CHARS] + f"\n... [truncated {dropped} chars]"

        exit_code = process.returncode if process.returncode is not None else -1
        return ToolResult(
            output=output,
            is_error=(exit_code != 0),
            metadata={
                "exit_code": exit_code,
                "duration_ms": elapsed_ms,
            },
        )


async def _terminate_then_kill(process: asyncio.subprocess.Process) -> None:
    """Send SIGTERM, wait up to ``KILL_GRACE_PERIOD_SECONDS``, then SIGKILL.

    Always reaps the process before returning so ``process.returncode`` is set.
    """
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_PERIOD_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
