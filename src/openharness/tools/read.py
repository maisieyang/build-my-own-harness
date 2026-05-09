"""Read tool -- P2-T3 sub-unit 3a.

Reads a text file's contents. Supports optional 1-indexed line offset / limit
slicing. UTF-8 decoding with ``errors="replace"`` — binary content surfaces as
replacement characters rather than raising. Empty files return the
``"(empty)"`` sentinel.

Per D9.1: relative paths resolve against ``context.cwd``; absolute paths are
used as-is. Per D9.6: error messages are descriptive and capped short.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


# 10 MiB. Aligns with phase-2-plan.md acceptance: files larger than this
# return an error rather than blowing up the LLM context window.
MAX_READ_BYTES = 10 * 1024 * 1024


class ReadInput(BaseModel):
    """Input schema for :class:`Read`."""

    path: str = Field(description="File path. Relative paths resolve against cwd.")
    offset: int | None = Field(
        default=None,
        ge=1,
        description="1-indexed line number to start at (inclusive).",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of lines to return.",
    )


class Read(BaseTool[ReadInput]):
    """Read a text file's contents."""

    name = "Read"
    description = (
        "Read a text file. Returns the contents (UTF-8, with replacement "
        "characters for invalid bytes). Supports 1-indexed line offset/limit. "
        "Files larger than 10 MiB are rejected; use Grep on huge files instead."
    )
    input_model = ReadInput
    is_read_only = True

    async def execute(
        self,
        args: ReadInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve(args.path, context.cwd)

        if not path.exists():
            return ToolResult(is_error=True, output=f"file not found: {path}")
        if not path.is_file():
            return ToolResult(is_error=True, output=f"not a regular file: {path}")

        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            return ToolResult(
                is_error=True,
                output=(
                    f"file too large: {size} bytes "
                    f"(limit {MAX_READ_BYTES} bytes); use Grep for large files"
                ),
            )
        if size == 0:
            return ToolResult(output="(empty)", metadata={"size_bytes": 0})

        # Run blocking IO in a worker thread so the loop's event loop is free
        # for whatever else (currently nothing -- D6.3 is serial -- but P3+
        # parallel-tools shares this rule).
        text = await asyncio.to_thread(
            path.read_text,
            encoding="utf-8",
            errors="replace",
        )

        if args.offset is not None or args.limit is not None:
            text = _slice_lines(text, offset=args.offset, limit=args.limit)

        return ToolResult(output=text, metadata={"size_bytes": size})


def _resolve(raw: str, cwd: Path) -> Path:
    """Resolve ``raw`` against ``cwd`` if it is a relative path; otherwise
    return as-is."""
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return cwd / candidate


def _slice_lines(text: str, *, offset: int | None, limit: int | None) -> str:
    """Apply 1-indexed line offset / limit. ``offset=None`` starts from the
    first line; ``limit=None`` reads to the end."""
    lines = text.splitlines(keepends=True)
    start = (offset - 1) if offset is not None else 0
    end = (start + limit) if limit is not None else len(lines)
    return "".join(lines[start:end])
