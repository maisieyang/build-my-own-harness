"""CLAUDE.md cascade discovery + system-prompt injection — P10-T4.4b.

Per ``decisions/25-phase-10-boundary.md`` D28.2: CLAUDE.md is the
**human-written, project-stable** context layer. Same philosophy as
``.gitignore`` / ``.editorconfig`` — configuration vertical to the
project, discovered by walking from ``cwd`` upward to the filesystem
root, plus a user-global fallback at ``~/.openharness/CLAUDE.md``.

Discovery order (later entries override earlier in the LLM's attention
because they sit later in the assembled section — but each file is
labeled with its absolute path so the LLM can resolve conflicts
explicitly):

1. For each directory ``d`` in ``[cwd, *cwd.parents]`` until root:
   a. ``d/CLAUDE.md``
   b. ``d/.claude/CLAUDE.md``
   c. ``d/.claude/rules/*.md`` (sorted by filename)
2. ``~/.openharness/CLAUDE.md`` (global fallback, appended last)

Dedup: same canonical path appears at most once. A symlinked CLAUDE.md
visible via two routes (e.g., user symlinks global to project's) is
emitted only once. We resolve paths for set-membership but keep the
*first-seen* original path for display so the labeled subsections
match the user's mental model of "which file."

Injection format::

    ## Project Instructions

    ### /abs/path/CLAUDE.md

    \\`\\`\\`md
    <file contents, possibly truncated>
    \\`\\`\\`

    ### /next/abs/path/CLAUDE.md
    ...

The umbrella is ``## Project Instructions`` (h2 — matches ``## Tools`` /
``## Environment``); per-file labels are ``### <abspath>`` (h3) so the
visual hierarchy reads as "this is a section like the others, but
it has multiple sub-items each from a different file."

Truncation: per-file char cap (default 12_000) with a
``\\n...[truncated]...\\n`` marker so the LLM knows content was
elided. Caps the worst case where a user writes a 100k-char
CLAUDE.md and would otherwise blow the prompt budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("prompts.claudemd")

_TRUNCATE_MARKER = "\n...[truncated]...\n"
_DEFAULT_MAX_CHARS_PER_FILE = 12_000


def _project_candidates(directory: Path) -> list[Path]:
    """Per-directory candidate list — the three locations a single
    project layer might place CLAUDE.md content.

    Listed in deterministic order so the discovery walk is stable
    (catalog-style output for the LLM doesn't shuffle between runs).
    """
    candidates = [
        directory / "CLAUDE.md",
        directory / ".claude" / "CLAUDE.md",
    ]
    # ``.claude/rules/*.md`` — sorted so a 5-rule project always
    # produces the same prompt section.
    rules_dir = directory / ".claude" / "rules"
    if rules_dir.is_dir():
        candidates.extend(sorted(rules_dir.glob("*.md")))
    return candidates


def discover_claude_md_files(cwd: str | Path) -> list[Path]:
    """Walk from ``cwd`` upward + global fallback. Return de-duped paths.

    The walk terminates when ``directory.parent == directory`` — true
    only at the filesystem root on POSIX (``/.parent == /``) and at
    drive roots on Windows (``C:\\.parent == C:\\``). This handles
    every platform without an explicit "depth limit" magic number.

    Files that don't exist are silently skipped (no warning) — most
    directories on a path don't carry CLAUDE.md, that's the normal
    case. Files that exist but can't be read (permission denied,
    decode error) are deferred to :func:`load_claude_md_prompt`
    where the failure surfaces in the load phase with a labeled
    warning.

    Dedup contract: a CLAUDE.md visible via multiple routes (e.g.,
    via symlink and via its target) appears **once** at the first
    discovery position. The original (non-resolved) path is preserved
    for display.
    """
    from pathlib import Path as _Path  # local import to keep TYPE_CHECKING clean

    current = _Path(cwd).resolve()
    results: list[Path] = []
    seen_canonical: set[Path] = set()

    # 1. Walk up from cwd to filesystem root
    for directory in (current, *current.parents):
        for candidate in _project_candidates(directory):
            _add_if_new(candidate, results, seen_canonical)
        if directory.parent == directory:
            # Root reached (POSIX: ``/`` has ``parent == /``;
            # Windows: drive root ditto). Defensive — _project_
            # candidates already iterated for this directory.
            break

    # 2. Global fallback — appended last so project-level instructions
    # come first in the assembled section.
    global_fallback = _Path.home() / ".openharness" / "CLAUDE.md"
    _add_if_new(global_fallback, results, seen_canonical)

    return results


def _add_if_new(
    candidate: Path,
    results: list[Path],
    seen_canonical: set[Path],
) -> None:
    """Append ``candidate`` to ``results`` if it exists AND hasn't been
    seen under its canonical (symlink-resolved) form.

    ``Path.exists`` returns ``False`` on a broken symlink — that's the
    right behavior (we can't read what isn't there).
    """
    if not candidate.exists():
        return
    try:
        canonical = candidate.resolve()
    except OSError:
        # ``resolve()`` can raise on permission denied (rare). Fall
        # back to the literal path for dedup — strictly less de-dup
        # but better than dropping the file entirely.
        canonical = candidate
    if canonical in seen_canonical:
        return
    results.append(candidate)
    seen_canonical.add(canonical)


def load_claude_md_prompt(
    cwd: str | Path,
    *,
    max_chars_per_file: int = _DEFAULT_MAX_CHARS_PER_FILE,
) -> str | None:
    """Discover + read + assemble the CLAUDE.md cascade as one section.

    Returns ``None`` when no CLAUDE.md files exist (the caller should
    omit the section entirely — empty section ≠ "no instructions").

    Each loaded file becomes a ``## <absolute path>`` subsection with
    its body wrapped in a ``\\`\\`\\`md`` fence. Files larger than
    ``max_chars_per_file`` are truncated with the
    ``\\n...[truncated]...\\n`` marker so the LLM knows content was
    elided (vs missing entirely).

    Per-file read failures (permission denied, decode error) log a
    ``claude_md_load_failed`` warning and skip that file — the rest
    of the cascade still loads. This matches the skip-not-fail
    discipline of :func:`parse_memory` / :func:`parse_skill`.
    """
    files = discover_claude_md_files(cwd)
    if not files:
        return None

    body_blocks: list[str] = []
    for path in files:
        content = _read_truncated(path, max_chars_per_file)
        if content is None:
            continue  # warning already logged inside helper
        body_blocks.append(f"### {path}\n\n```md\n{content.strip()}\n```")

    if not body_blocks:
        # All files existed at discovery time but all failed at read
        # time (e.g., perms changed mid-flight) — same UX as "no files":
        # caller omits the section.
        return None

    return "## Project Instructions\n\n" + "\n\n".join(body_blocks)


def _read_truncated(path: Path, max_chars_per_file: int) -> str | None:
    """Read ``path`` UTF-8, truncate to ``max_chars_per_file`` if needed.

    Returns ``None`` + log on :class:`OSError` (file gone, perms) or
    :class:`UnicodeDecodeError` (binary content masquerading as
    ``.md``). The caller treats ``None`` as "skip this file."
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning(
            "claude_md_load_failed",
            source_path=str(path),
            phase="read",
            error=str(exc),
        )
        return None
    except UnicodeDecodeError as exc:
        _logger.warning(
            "claude_md_load_failed",
            source_path=str(path),
            phase="decode",
            error=str(exc),
        )
        return None
    if len(content) > max_chars_per_file:
        # Keep the head and the marker; tail is dropped because the
        # most signal-dense part of project instructions is the
        # opening section.
        content = content[:max_chars_per_file] + _TRUNCATE_MARKER
    return content
