"""Compatibility wrappers for the former CLAUDE.md-only prompt API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.prompts.project_instructions import (
    discover_project_instruction_files,
    load_project_instructions,
)

if TYPE_CHECKING:
    from pathlib import Path


def discover_claude_md_files(cwd: str | Path) -> list[Path]:
    """Discover CLAUDE.md-compatible files within ``cwd``'s workspace."""
    return discover_project_instruction_files(
        cwd,
        instruction_names=("CLAUDE.md",),
    )


def load_claude_md_prompt(
    cwd: str | Path,
    *,
    max_chars_per_file: int = 12_000,
) -> str | None:
    """Load CLAUDE.md-compatible files within ``cwd``'s workspace."""
    return load_project_instructions(
        cwd,
        instruction_names=("CLAUDE.md",),
        max_chars_per_file=max_chars_per_file,
    )
