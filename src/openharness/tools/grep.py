"""Grep tool -- P2-T3 sub-unit 3e.

Search file contents using ripgrep. Read-only operation, so no project-root
scope guard (consistent with Read).

Per phase-2-plan.md:
- 8 MiB stream limit on subprocess output
- ``line_cap`` default 200, hard ceiling 2000
- ``"(no matches)"`` sentinel when nothing found

Per D9.1: relative paths resolve against ``context.cwd``; absolute paths used
as-is. Per D9.6: descriptive error messages.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


STREAM_LIMIT_BYTES = 8 * 1024 * 1024
DEFAULT_LINE_CAP = 200
HARD_LINE_CAP = 2000
NO_MATCHES_SENTINEL = "(no matches)"


class GrepInput(BaseModel):
    """Input schema for :class:`Grep`."""

    pattern: str = Field(min_length=1, description="Regex pattern (PCRE2 dialect).")
    path: str | None = Field(
        default=None,
        description="File or directory to search; default is cwd.",
    )
    glob: str | None = Field(
        default=None,
        description="Restrict to paths matching this glob (e.g. '**/*.py').",
    )
    ignore_case: bool = Field(default=False, description="Case-insensitive match.")
    hidden: bool = Field(default=False, description="Include dot-files / hidden directories.")
    line_cap: int = Field(
        default=DEFAULT_LINE_CAP,
        ge=1,
        le=HARD_LINE_CAP,
        description=(
            f"Maximum result lines to return (1-{HARD_LINE_CAP}). Default {DEFAULT_LINE_CAP}."
        ),
    )


class Grep(BaseTool[GrepInput]):
    """Search file contents using ripgrep."""

    name = "Grep"
    description = (
        "Search file contents (regex). Returns matching lines with file:line "
        "prefixes. Capped at line_cap rows (default 200, hard ceiling 2000). "
        "Requires ripgrep (rg) on PATH."
    )
    input_model = GrepInput
    is_read_only = True

    async def execute(
        self,
        args: GrepInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        rg = shutil.which("rg")
        if rg is None:
            return ToolResult(
                is_error=True,
                output="ripgrep (rg) not found on PATH; install ripgrep to use Grep",
            )

        cmd = [rg, "--line-number", "--color=never"]
        if args.ignore_case:
            cmd.append("-i")
        if args.hidden:
            cmd.append("--hidden")
        if args.glob is not None:
            cmd.extend(["--glob", args.glob])
        cmd.append(args.pattern)

        target = _resolve(args.path, context.cwd) if args.path else context.cwd
        cmd.append(str(target))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(context.cwd),
            limit=STREAM_LIMIT_BYTES,
        )
        stdout_bytes, stderr_bytes = await process.communicate()

        # ripgrep exit conventions: 0 = matches, 1 = no matches, >=2 = error.
        if process.returncode == 1:
            return ToolResult(output=NO_MATCHES_SENTINEL, metadata={"match_count": 0})
        if process.returncode != 0:
            err = stderr_bytes.decode("utf-8", errors="replace").strip()
            return ToolResult(is_error=True, output=f"rg failed: {err}")

        output = stdout_bytes.decode("utf-8", errors="replace")
        lines = output.splitlines()
        total_matches = len(lines)

        if total_matches > args.line_cap:
            kept = lines[: args.line_cap]
            kept.append(f"... [truncated to {args.line_cap} lines; total matches {total_matches}]")
            output = "\n".join(kept)
        else:
            output = "\n".join(lines)

        return ToolResult(
            output=output,
            metadata={"match_count": total_matches},
        )


def _resolve(raw: str, cwd: Path) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return cwd / candidate
