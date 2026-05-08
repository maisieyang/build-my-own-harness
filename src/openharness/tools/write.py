"""Write tool -- P2-T3 sub-unit 3b.

Creates or overwrites a text file. Per D9.2: writes are scoped to
``context.cwd`` (the "project root"). Resolved paths outside the cwd are
rejected with an error result; the ``--auto`` flag from P2-T6 will plumb
through to relax this for power users.

Per D9.1: relative paths resolve against ``context.cwd``; absolute paths
are used as-is (then immediately checked against the scope rule).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


class WriteInput(BaseModel):
    """Input schema for :class:`Write`."""

    path: str = Field(description="File path. Relative paths resolve against cwd.")
    content: str = Field(description="Text content to write (UTF-8 encoded).")


class Write(BaseTool[WriteInput]):
    """Create or overwrite a text file."""

    name = "Write"
    description = (
        "Write text content to a file (UTF-8). Creates the file if absent, "
        "overwrites if present. Refuses to write outside the project root. "
        "The parent directory must already exist; use Bash 'mkdir -p' if not."
    )
    input_model = WriteInput

    async def execute(
        self,
        args: WriteInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve(args.path, context.cwd)

        if not _inside_project_root(path, context.cwd):
            return ToolResult(
                is_error=True,
                output=f"path resolves outside project root: {path}",
            )

        if path.exists() and path.is_dir():
            return ToolResult(
                is_error=True,
                output=f"cannot overwrite directory: {path}",
            )

        if not path.parent.exists():
            return ToolResult(
                is_error=True,
                output=f"parent directory does not exist: {path.parent}",
            )

        try:
            bytes_written = await asyncio.to_thread(_write_utf8, path, args.content)
        except OSError as exc:
            return ToolResult(is_error=True, output=f"write failed: {exc}")

        return ToolResult(
            output=f"wrote {bytes_written} bytes to {path}",
            metadata={"bytes_written": bytes_written, "path": str(path)},
        )


def _resolve(raw: str, cwd: Path) -> Path:
    """Resolve ``raw`` against ``cwd`` if relative; otherwise return as-is."""
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return cwd / candidate


def _inside_project_root(path: Path, cwd: Path) -> bool:
    """True iff ``path`` (after symlink + ``..`` normalization) is the same
    as ``cwd`` or one of its descendants.

    ``resolve(strict=False)`` works on non-existent paths -- important because
    Write's whole job is creating files that don't yet exist.
    """
    try:
        path.resolve(strict=False).relative_to(cwd.resolve(strict=False))
    except ValueError:
        return False
    return True


def _write_utf8(path: Path, content: str) -> int:
    """Write ``content`` as UTF-8 and return bytes written."""
    encoded = content.encode("utf-8")
    path.write_bytes(encoded)
    return len(encoded)
