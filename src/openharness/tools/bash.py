"""Bash tool -- P2-T3 sub-unit 3d; P7-T3 refactored to delegate.

Executes a shell command via the substrate :class:`ExecutionEnvironment`
configured on ``QueryContext.execution_env`` (defaults to
``HostExecution``, swapped to ``SandboxExecution`` when Phase 7b
lands). Per ``decisions/15-phase-7-boundary.md`` D17.4:**BashTool is
the ONLY consumer of ``execution_env``**;Read/Write/Edit/Grep don't
need substrate isolation because path-based AuthZ Tier 1-3 already
covers them.

Behavior contract(unchanged across the P7-T3 refactor):

- Default timeout 600s (overridable per-call via ``timeout_seconds``).
- ``stdout`` and ``stderr`` are merged into a single output stream
  (handled by the substrate's pipe-level merge in :class:`HostExecution`).
- On timeout:``SIGTERM`` -> 2s grace -> ``SIGKILL`` (handled by the
  substrate).
- Output over 12,000 chars is head+tail truncated (50/50, middle marker,
  F6) — summaries/exit text live at the END of command output and must
  survive; handled here in the tool layer because truncation is
  LLM-facing semantics, not raw process I/O.
- Empty stdout returns ``"(no output)"`` sentinel (P3-T1.1b) — same
  reasoning, LLM-facing.
- ``exit_code`` and ``duration_ms`` go to ``metadata`` (D9.5).

Per D9.4: **no deny-list at this layer**. Bash trusts what it receives.
P2-T6 ``PermissionChecker`` enforces safety one layer up.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.execution import (
    BoundaryViolation,
    CommandOperation,
    ExecutionEffect,
    ExecutionFailed,
    ProcessCompleted,
    TimedOut,
)
from openharness.execution.host import _HOST_EXECUTION
from openharness.tools.base import BaseTool, ExecutionDomain, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


DEFAULT_TIMEOUT_SECONDS = 600
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
    """Run a shell command via the configured ExecutionEnvironment.

    P7-T3 refactor:the subprocess plumbing (create_subprocess_shell +
    merged pipe + timeout + SIGTERM->SIGKILL escalation) is owned by
    the substrate (:class:`HostExecution` in Phase 7a;
    ``SandboxExecution`` in Phase 7b). The Bash tool's responsibility
    shrinks to:

    1. Pack ``BashInput`` into substrate call args
    2. Translate substrate's ``ProcessResult`` into LLM-facing
       ``ToolResult`` (apply truncation, empty sentinel, is_error from
       exit_code, metadata dict)
    """

    execution_domain = ExecutionDomain.LOCAL_DATA
    required_execution_effect = ExecutionEffect.COMMAND
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
        if context.sandbox_session is not None:
            sandbox_result = await context.sandbox_session.execute(
                CommandOperation(
                    command=args.command,
                    cwd=context.cwd,
                    timeout=float(timeout),
                )
            )
            if isinstance(sandbox_result, TimedOut):
                return ToolResult(
                    is_error=True,
                    output=f"command timed out after {timeout}s",
                    metadata={"timed_out": True},
                )
            if isinstance(sandbox_result, BoundaryViolation):
                return ToolResult(
                    is_error=True,
                    output=(
                        f"sandbox boundary violation ({sandbox_result.dimension}): "
                        f"{sandbox_result.requested}; {sandbox_result.evidence}"
                    ),
                    metadata={
                        "boundary_violation": {
                            "dimension": sandbox_result.dimension,
                            "requested": sandbox_result.requested,
                            "evidence": sandbox_result.evidence,
                            "hard_deny": sandbox_result.hard_deny,
                        }
                    },
                )
            if isinstance(sandbox_result, ExecutionFailed):
                return ToolResult(
                    is_error=True,
                    output=f"sandbox command failed: {sandbox_result.reason}",
                )
            if not isinstance(sandbox_result, ProcessCompleted):
                return ToolResult(is_error=True, output="sandbox returned an invalid result")
            return _command_result(
                output=sandbox_result.output,
                exit_code=sandbox_result.exit_code,
                elapsed_ms=0,
            )
        # P7-T3:fall back to the host singleton when ``execution_env``
        # isn't populated. This happens when a test or external caller
        # constructs ``ToolExecutionContext(cwd=p)`` directly without
        # going through the engine. The engine always populates
        # ``execution_env`` per P7-T2.
        env = context.execution_env if context.execution_env is not None else _HOST_EXECUTION

        start = time.monotonic()
        result = await env.run_command(
            command=args.command,
            cwd=context.cwd,
            timeout=float(timeout),
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if result.timed_out:
            return ToolResult(
                is_error=True,
                output=f"command timed out after {timeout}s",
                metadata={
                    "exit_code": result.exit_code,
                    "duration_ms": elapsed_ms,
                    "timed_out": True,
                },
            )

        return _command_result(
            output=result.output,
            exit_code=result.exit_code,
            elapsed_ms=elapsed_ms,
        )


def _command_result(*, output: str, exit_code: int, elapsed_ms: int) -> ToolResult:
    # Strict empty-only: only the empty-output case triggers the
    # sentinel; whitespace-only output (e.g., bare ``echo`` -> "\n")
    # passes through unchanged. is_error is decided by exit_code.
    if output == "":
        output = NO_OUTPUT_SENTINEL
    elif len(output) > MAX_OUTPUT_CHARS:
        dropped = len(output) - MAX_OUTPUT_CHARS
        half = MAX_OUTPUT_CHARS // 2
        output = output[:half] + f"\n... [truncated {dropped} chars] ...\n" + output[-half:]

    return ToolResult(
        output=output,
        is_error=(exit_code != 0),
        metadata={
            "exit_code": exit_code,
            "duration_ms": elapsed_ms,
        },
    )
