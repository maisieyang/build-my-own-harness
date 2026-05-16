"""``ExecutionEnvironment`` Protocol + ``ProcessResult`` — P7-T1.1a.

Per ``decisions/15-phase-7-boundary.md`` D17.1: minimal three-argument
protocol matching what current ``Bash`` needs. The shape is intentionally
shell-string-based(not exec-style ``list[str]``)so ``HostExecution``
can wrap ``asyncio.create_subprocess_shell`` byte-identically with the
current behavior. Phase 7b's Docker substrate naturally accepts the
same string for ``docker exec sh -c "..."``.

``ProcessResult`` is the substrate's raw output — separate from
``ToolResult`` which carries LLM-facing semantics. ``BashTool.execute``
translates one into the other(applies the ``(no output)`` sentinel,
truncation, ``is_error`` from ``exit_code``, ``metadata`` dict).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    """Raw output of a shell command invocation.

    ``output`` is the merged stdout + stderr in chronological order
    (achieved at the pipe level via ``stderr=asyncio.subprocess.STDOUT``).
    Separating the streams would change the ordering guarantee — defer
    until a real use case justifies the split.

    ``exit_code`` is the subprocess's exit status. When ``timed_out`` is
    ``True`` the process was killed before exiting cleanly; callers
    treating that as a failure should not rely on ``exit_code`` being
    meaningful in that case.
    """

    output: str
    exit_code: int
    timed_out: bool = False


class ExecutionEnvironment(Protocol):
    """Substrate that runs shell commands.

    The ONLY consumer in Phase 7a is ``BashTool``. Other tools(Read /
    Write / Edit / Grep / MCP adapters / LoadSkill / SpawnAgent)don't
    execute shell commands and ignore the ``execution_env`` field on
    ``ToolExecutionContext``.

    Implementations:

    - :class:`HostExecution` (Phase 7a, default) — wraps
      ``asyncio.create_subprocess_shell`` directly. Identity transform
      preserving current ``Bash`` semantics.
    - ``SandboxExecution`` (Phase 7b, future) — Docker container
      substrate with bind mount + cgroup + network policy.

    The Protocol is single-method by design:adding more methods would
    couple the abstraction to specific tool needs. If a future tool
    needs a fundamentally different operation(e.g., reading files
    inside a sandbox without going through shell),that's a separate
    Protocol — not an extension to this one.
    """

    async def run_command(
        self,
        command: str,
        cwd: Path,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run ``command`` as a shell command in ``cwd``.

        - ``command`` is passed to ``/bin/sh -c`` semantics (or the
          substrate's equivalent). Multi-word commands, shell expansion,
          pipes, redirects all work.
        - ``cwd`` is the working directory. Substrates that bind-mount
          (Phase 7b) translate this to the container's path namespace.
        - ``timeout`` (seconds, float) — substrate kills the process on
          expiry. ``None`` means no timeout enforcement at this layer
          (caller may still apply its own deadline). On timeout the
          returned ``ProcessResult.timed_out`` is ``True``.

        Returns :class:`ProcessResult` with merged stdout+stderr,
        exit code (sentinel on timeout), and the ``timed_out`` flag.

        Should not raise on subprocess failures — those surface via
        non-zero ``exit_code``. Programming errors (cwd doesn't exist,
        OS resource exhaustion, etc.) MAY propagate as exceptions; the
        tool layer wraps them into ``ToolError``.
        """
        ...  # pragma: no cover - Protocol method body
