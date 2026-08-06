"""Target-project instruction discovery and prompt rendering.

The harness owns the loading mechanism; the target project owns the
instruction files. Discovery is therefore rooted at an explicit workspace
boundary and never consults the harness installation directory, filesystem
ancestors, or user-global configuration.
"""

from __future__ import annotations

from pathlib import Path

from openharness.observability.logging import get_logger

_logger = get_logger("prompts.project_instructions")

_DEFAULT_INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md")
_DEFAULT_MAX_CHARS_PER_FILE = 12_000
_TRUNCATE_MARKER = "\n...[truncated]...\n"


def discover_project_instruction_files(
    workspace_root: str | Path,
    *,
    working_directory: str | Path | None = None,
    instruction_names: tuple[str, ...] = _DEFAULT_INSTRUCTION_NAMES,
) -> list[Path]:
    """Discover project-owned instruction files inside ``workspace_root``.

    Directories are visited from the workspace root toward the working
    directory, so nested instructions appear later and can specialize broader
    project guidance. Canonical paths outside the workspace are rejected,
    including symlinks that point across the boundary.
    """
    root = Path(workspace_root).resolve()
    current = Path(working_directory).resolve() if working_directory else root
    if current != root and not current.is_relative_to(root):
        raise ValueError("working_directory must be inside workspace_root")

    results: list[Path] = []
    seen_canonical: set[Path] = set()
    for directory in _directories_from_root(root, current):
        for candidate in _project_candidates(directory, instruction_names):
            _add_bounded_file(candidate, root, results, seen_canonical)
    return results


def load_project_instructions(
    workspace_root: str | Path,
    *,
    working_directory: str | Path | None = None,
    instruction_names: tuple[str, ...] = _DEFAULT_INSTRUCTION_NAMES,
    max_chars_per_file: int = _DEFAULT_MAX_CHARS_PER_FILE,
) -> str | None:
    """Load project instruction files as a labeled system-prompt section."""
    files = discover_project_instruction_files(
        workspace_root,
        working_directory=working_directory,
        instruction_names=instruction_names,
    )
    body_blocks: list[str] = []
    for path in files:
        content = _read_truncated(path, max_chars_per_file)
        if content is not None:
            body_blocks.append(f"### {path}\n\n```md\n{content.strip()}\n```")
    if not body_blocks:
        return None
    return "## Project Instructions\n\n" + "\n\n".join(body_blocks)


def _directories_from_root(root: Path, current: Path) -> list[Path]:
    directories = [root]
    cursor = root
    for part in current.relative_to(root).parts:
        cursor /= part
        directories.append(cursor)
    return directories


def _project_candidates(
    directory: Path,
    instruction_names: tuple[str, ...],
) -> list[Path]:
    candidates = [directory / name for name in instruction_names]
    if "CLAUDE.md" in instruction_names:
        candidates.append(directory / ".claude" / "CLAUDE.md")
        rules_dir = directory / ".claude" / "rules"
        if rules_dir.is_dir():
            candidates.extend(sorted(rules_dir.glob("*.md")))
    return candidates


def _add_bounded_file(
    candidate: Path,
    root: Path,
    results: list[Path],
    seen_canonical: set[Path],
) -> None:
    if not candidate.is_file():
        return
    try:
        canonical = candidate.resolve()
    except OSError:
        return
    if canonical != root and not canonical.is_relative_to(root):
        return
    if canonical in seen_canonical:
        return
    results.append(candidate)
    seen_canonical.add(canonical)


def _read_truncated(path: Path, max_chars_per_file: int) -> str | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning(
            "project_instruction_load_failed",
            source_path=str(path),
            phase="read",
            error=str(exc),
        )
        return None
    except UnicodeDecodeError as exc:
        _logger.warning(
            "project_instruction_load_failed",
            source_path=str(path),
            phase="decode",
            error=str(exc),
        )
        return None
    if len(content) > max_chars_per_file:
        return content[:max_chars_per_file] + _TRUNCATE_MARKER
    return content
