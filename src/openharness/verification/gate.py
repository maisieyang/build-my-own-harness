"""L3 verification gate — runs command-form verification steps.

Command gate only (grader-agent gate is out of scope, see loop-runtime
L3 plan). Fail-fast: stops at the first non-zero exit (including
timeout). Fail-closed: zero configured steps never passes, and a
malformed/empty ``--verify`` command is a failed step, not a crash.

Uses argv-form execution (``asyncio.create_subprocess_exec``, never
``shell=True`` / ``create_subprocess_shell``) — commands come from
config the agent may have touched, so shell interpretation of them
would be a command-injection surface.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.execution.host import _TIMEOUT_EXIT_CODE, _terminate_then_kill

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_MAX_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class StepResult:
    """Outcome of a single verification command."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a full verification run — fail-closed on zero steps."""

    passed: bool
    steps: tuple[StepResult, ...]
    feedback: str


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    dropped = len(text) - _MAX_OUTPUT_CHARS
    return text[:_MAX_OUTPUT_CHARS] + f"...[truncated {dropped} chars]"


async def run_verification_steps(
    commands: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 600.0,
) -> VerificationResult:
    """Run ``commands`` in order, stopping at the first failure.

    Each command is split with ``shlex.split`` and run via argv-form
    ``create_subprocess_exec`` — no shell is invoked. Returns fail-closed
    (``passed=False``) when ``commands`` is empty, and a malformed or
    blank command string is recorded as a failed step rather than
    propagating ``shlex``'s ``ValueError``.
    """
    if not commands:
        return VerificationResult(
            passed=False, steps=(), feedback="no verification steps configured"
        )

    steps: list[StepResult] = []

    for command in commands:
        start = time.monotonic()

        try:
            argv = shlex.split(command)
            if not argv:
                raise ValueError("empty command")
        except ValueError as exc:
            steps.append(
                StepResult(
                    command=command,
                    returncode=127,
                    stdout="",
                    stderr=_truncate(f"invalid verification command: {exc}"),
                    duration_s=time.monotonic() - start,
                    timed_out=False,
                )
            )
            break

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            steps.append(
                StepResult(
                    command=command,
                    returncode=127,
                    stdout="",
                    stderr=_truncate(str(exc)),
                    duration_s=time.monotonic() - start,
                    timed_out=False,
                )
            )
            break

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            await _terminate_then_kill(process)
            timed_out = True
            stdout_bytes = b""
            stderr_bytes = b""

        duration_s = time.monotonic() - start
        returncode = _TIMEOUT_EXIT_CODE if timed_out else (process.returncode or 0)

        steps.append(
            StepResult(
                command=command,
                returncode=returncode,
                stdout=_truncate(stdout_bytes.decode("utf-8", errors="replace")),
                stderr=_truncate(stderr_bytes.decode("utf-8", errors="replace")),
                duration_s=duration_s,
                timed_out=timed_out,
            )
        )

        if returncode != 0:
            break

    passed = len(steps) > 0 and all(s.returncode == 0 for s in steps)
    feedback = f"{len(steps)} step(s) passed" if passed else f"step failed: {steps[-1].command}"

    return VerificationResult(passed=passed, steps=tuple(steps), feedback=feedback)


async def maybe_run_verification(
    verify: Sequence[str] | None,
    *,
    cwd: Path,
    timeout: float,
) -> VerificationResult | None:
    """Shared gating helper: ``None`` when verification wasn't requested.

    Single source for the "``if verify: run_verification_steps(...)`` else
    ``None``" decision, used by both the ``--output-format json`` and
    ``stream-json`` output paths (code-review finding: this was duplicated
    between ``cli.py`` and ``_stream_render.py``).
    """
    if not verify:
        return None
    return await run_verification_steps(verify, cwd=cwd, timeout=timeout)
