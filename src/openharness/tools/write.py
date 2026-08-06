"""Write tool -- P2-T3 sub-unit 3b + P3-T3.3f (project-root check moved out).

Creates or overwrites a text file. Per D9.1:relative paths resolve
against ``context.cwd``;absolute paths are used as-is.

The **project-root scope guard** that used to live in this file is gone
as of P3-T3.3f. The Tier 3 mode-based check in
:class:`TierBasedPermissionChecker` runs before ``execute`` and returns
``DecisionResult.ask`` for paths outside cwd —— single-point enforcement,
framework-side, with ASK semantics that ``--auto`` can override.
Same migration as Edit;principle (avoid double-defense, single source of
truth for boundary policy) is universal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.execution import FileWriteOperation
from openharness.tools.base import (
    BaseTool,
    ExecutionDomain,
    ToolResult,
    tool_result_from_operation,
)

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


class WriteInput(BaseModel):
    """Input schema for :class:`Write`."""

    path: str = Field(description="File path. Relative paths resolve against cwd.")
    content: str = Field(description="Text content to write (UTF-8 encoded).")


class Write(BaseTool[WriteInput]):
    """Create or overwrite a text file."""

    execution_domain = ExecutionDomain.LOCAL_DATA
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

        if context.sandbox_session is not None:
            sandbox_result = await context.sandbox_session.execute(
                FileWriteOperation(path=path, content=args.content)
            )
            return tool_result_from_operation(sandbox_result)

        # P3-T3.3f:project-root scope check moved to AuthZ Tier 3
        # (TierBasedPermissionChecker) — Write no longer double-checks here.
        # Same migration as Edit;principle (single-point enforcement) is universal.
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


def _write_utf8(path: Path, content: str) -> int:
    """Write ``content`` as UTF-8 and return bytes written."""
    encoded = content.encode("utf-8")
    path.write_bytes(encoded)
    return len(encoded)
