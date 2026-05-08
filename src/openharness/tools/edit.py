"""Edit tool -- P2-T3 sub-unit 3c.

Exact-string replacement in a text file. ``replace_all=False`` (default)
substitutes the first occurrence only; ``replace_all=True`` substitutes
all occurrences. ``old_str`` not found returns an error result.

Per phase-2-plan.md: no implicit uniqueness check (callers may want to
replace the first of many duplicates). Multi-line ``old_str`` is supported
-- bytes-equivalent match, no whitespace or case normalization.

Path resolution + project-root scope guard mirror :mod:`openharness.tools.write`
(D9.1 / D9.2).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


class EditInput(BaseModel):
    """Input schema for :class:`Edit`."""

    path: str = Field(description="File path. Relative paths resolve against cwd.")
    old_str: str = Field(
        min_length=1,
        description="Exact substring to replace. Must be non-empty.",
    )
    new_str: str = Field(description="Replacement text. May be empty (deletes old_str).")
    replace_all: bool = Field(
        default=False,
        description="If true, replaces every occurrence; default replaces only the first.",
    )


class Edit(BaseTool[EditInput]):
    """Replace exact-match string(s) in a text file."""

    name = "Edit"
    description = (
        "Replace an exact substring (no regex) in a text file. By default "
        "replaces the first occurrence; pass replace_all=true for all. "
        "Supports multi-line old_str. Errors if old_str is not found or "
        "if the file is not valid UTF-8."
    )
    input_model = EditInput

    async def execute(
        self,
        args: EditInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve(args.path, context.cwd)

        if not _inside_project_root(path, context.cwd):
            return ToolResult(
                is_error=True,
                output=f"path resolves outside project root: {path}",
            )

        if not path.exists():
            return ToolResult(is_error=True, output=f"file not found: {path}")
        if not path.is_file():
            return ToolResult(is_error=True, output=f"not a regular file: {path}")

        try:
            original = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                is_error=True,
                output=f"file is not valid UTF-8: {path}",
            )

        if args.old_str not in original:
            return ToolResult(
                is_error=True,
                output=f"old_str not found in {path}; no replacement made",
            )

        if args.replace_all:
            count = original.count(args.old_str)
            updated = original.replace(args.old_str, args.new_str)
        else:
            count = 1
            updated = original.replace(args.old_str, args.new_str, 1)

        await asyncio.to_thread(path.write_text, updated, encoding="utf-8")

        return ToolResult(
            output=f"replaced {count} occurrence(s) in {path}",
            metadata={"replacements": count, "path": str(path)},
        )


def _resolve(raw: str, cwd: Path) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return cwd / candidate


def _inside_project_root(path: Path, cwd: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(cwd.resolve(strict=False))
    except ValueError:
        return False
    return True
