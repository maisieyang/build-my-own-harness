"""Edit tool -- P2-T3 sub-unit 3c + P3-T1.1c (atomic write).

Exact-string replacement in a text file. ``replace_all=False`` (default)
substitutes the first occurrence only; ``replace_all=True`` substitutes
all occurrences. ``old_str`` not found returns an error result.

Per phase-2-plan.md: no implicit uniqueness check (callers may want to
replace the first of many duplicates). Multi-line ``old_str`` is supported
-- bytes-equivalent match, no whitespace or case normalization.

Path resolution + project-root scope guard mirror :mod:`openharness.tools.write`
(D9.1 / D9.2).

P3-T1.1c: writes go through ``_atomic_write_text`` (tempfile + fsync + rename
in the target's directory). A mid-write crash leaves the original file
bytes-intact and removes the partial tempfile. POSIX rename is atomic
within a single filesystem; we keep the tempfile in ``path.parent`` so
the rename never crosses a filesystem boundary.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
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

        await asyncio.to_thread(_atomic_write_text, path, updated)

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


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically via tempfile + fsync + rename.

    Atomicity contract:
    - Writers see the new content fully, or the original file unchanged —
      never a partial write.
    - On any exception during write / fsync / rename, the partial tempfile
      is unlinked; the target file is untouched.
    - POSIX rename is atomic within a filesystem; the tempfile lives in
      ``path.parent`` so the rename never crosses filesystems.

    ``os.fsync`` is the deepest point — it forces the OS write buffer to
    durable storage. Skipping it would leave a small window where the
    rename completes but the data isn't on disk yet (a power loss in that
    window would leave a renamed-but-empty file).
    """
    tmp_fd, tmp_str = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except BaseException:
        # Cleanup partial tempfile on any failure (OSError, KeyboardInterrupt,
        # SystemExit, etc.). missing_ok=True for the case where rename
        # actually succeeded before the exception (cross-failure idempotence).
        tmp_path.unlink(missing_ok=True)
        raise
