"""Tier-based permission checker — P3-T3.

Implements the three-tier policy structure per ``decisions/08`` D13.2:

- **Tier 1**: hardcoded sensitive paths (framework-owned; user can't change)
- **Tier 2**: user-configured glob deny rules (via ``Settings.deny_paths``)
- **Tier 3**: mode-based — write/exec tools restricted to project root

Sub-units land incrementally:

- 3a (this commit): Tier 1 pattern list + ``_glob_match`` helper
- 3b: ``Settings.deny_paths`` field + Tier 2 logic
- 3c: Tier 3 mode-based + ``_inside_project_root``
- 3d: ``DecisionResult`` type + ``Decision.ASK`` (third state for HITL)
- 3e: ``TierBasedPermissionChecker`` class composing all three tiers
- 3f: ``DenyListChecker`` migration + Edit cleanup

Design choice (P3-T3 Three-Axis Micro-Decision B): use ``fnmatch`` + a
simple ``dir/**`` recursive suffix translation, **not** a third-party
``pathspec`` dependency. This covers the most common deny patterns
(``*.env``, ``secrets/**``, ``dir/file``) without adding runtime deps.
The trade-off: no full ``.gitignore`` semantics (no ``!`` negation, no
``/`` prefix significance). Documented + good enough for Phase 3.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

# Per decisions/08 D13.2 + P3-T3 Three-Axis Micro-Decision A:
# 8 hardcoded patterns covering universally-sensitive paths. Adding
# entries here is a framework-level change and requires updating the
# decision record + tests in tests/permissions/test_tier_based.py.
HARDCODED_SENSITIVE_PATHS: tuple[str, ...] = (
    # User credential directories
    "~/.ssh/**",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.kube/**",
    "~/.config/gh/**",
    # System credential files (POSIX)
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
)


def _glob_match(path: str, pattern: str) -> bool:
    """Match ``path`` against ``pattern`` with ``~`` expansion + recursive
    ``dir/**`` suffix.

    Semantics:

    - Both ``path`` and ``pattern`` are run through ``os.path.expanduser``
      so ``~`` resolves consistently on both sides.
    - If ``pattern`` ends in ``/**``, it matches the directory itself and
      every descendant (prefix-match requiring trailing ``/`` for non-empty
      descendants).
    - Otherwise falls through to standard ``fnmatch.fnmatch``.

    Limitations (P3-T3 Three-Axis Micro-Decision B):

    - No ``.gitignore``-style ``!`` negation
    - ``**`` is only recognized as the suffix ``/**``, not mid-pattern
    - Patterns are case-sensitive (fnmatch behavior on POSIX)
    - **``*`` crosses path separators** (Python ``fnmatch`` quirk; unlike
      shell glob / ``.gitignore`` where ``*`` stays in one segment).
      So ``foo/*`` matches both ``foo/bar`` and ``foo/bar/baz``. Users
      who want strict single-level matching write explicit patterns;
      recursive intent should use our ``/**`` suffix. For deny rules
      this errs on the safer side: over-deny > under-deny.
    """
    norm_pattern = os.path.expanduser(pattern)
    norm_path = os.path.expanduser(path)

    if norm_pattern.endswith("/**"):
        prefix = norm_pattern[:-3]  # drop the literal "/**"
        return norm_path == prefix or norm_path.startswith(prefix + "/")

    return fnmatch.fnmatch(norm_path, norm_pattern)


def _matches_tier1(path: str) -> str | None:
    """Return the Tier 1 pattern that matched, or ``None`` if no match.

    Returning the matching pattern (not just a bool) lets the caller
    build a deny reason like ``"matches sensitive system path (~/.ssh/**)"``
    — the LLM reading the reason can infer next steps.
    """
    for pattern in HARDCODED_SENSITIVE_PATHS:
        if _glob_match(path, pattern):
            return pattern
    return None


def _inside_project_root(path: Path | str, cwd: Path) -> bool:
    """True iff ``path`` (after symlink + ``..`` normalization) is the same
    as ``cwd`` or one of its descendants.

    Tier 3 boundary helper (P3-T3.3c). Mirrors the historical logic from
    ``edit.py`` — P3-T3.3f deletes that copy and centralizes here.

    ``resolve(strict=False)`` works on non-existent paths — important
    because permission checks happen *before* the tool runs, so the
    target may not exist yet (e.g., Write creating a new file).
    """
    p = Path(path) if isinstance(path, str) else path
    try:
        p.resolve(strict=False).relative_to(cwd.resolve(strict=False))
    except ValueError:
        return False
    return True


def _matches_tier3(
    is_read_only: bool,
    path: str | None,
    cwd: Path,
) -> str | None:
    """Tier 3 mode-based check (P3-T3.3c).

    Per Three-Axis Micro-Decision C:

    - Read-only tools (``is_read_only=True``) skip Tier 3 entirely.
      Reading files outside cwd (e.g., ``~/Documents/notes.md``) is a
      legitimate use case.
    - Strict tools (``is_read_only=False``) must operate within cwd.
      A path outside cwd returns a reason string.
    - No path (``path=None``, e.g., Bash's ``command`` field) skips
      Tier 3 — Bash safety lives in Tier 1/2 + Hook layer.

    Per Three-Axis Micro-Decision G:Tier 3 matches map to **ASK** in
    the final DecisionResult (not DENY) — but that mapping is in the
    Checker layer (3e). This function returns a reason string for the
    boundary violation; the caller decides ASK vs DENY semantics.
    """
    if is_read_only:
        return None
    if path is None:
        return None
    if _inside_project_root(path, cwd):
        return None
    return f"path {path!r} is outside project root (cwd: {cwd})"


def _matches_tier2(path: str, patterns: tuple[str, ...], cwd: Path) -> str | None:
    """Return the Tier 2 user pattern that matched, or ``None`` if no match.

    P3-T3.3b. Mirrors :func:`_matches_tier1` but with user-supplied patterns
    (from ``Settings.deny_paths``) and ``.gitignore``-style cwd-relative
    semantics:

    - **Absolute or tilde patterns** (``/etc/foo`` / ``~/proj/x``):
      matched against the path directly (after ``~`` expansion).
    - **Relative patterns** (``secrets/**`` / ``*.env``):
      matched against the path **relative to cwd**. Lets a user write
      ``OPENHARNESS_DENY_PATHS='secrets/**'`` and have it work without
      hardcoding their project root.

    First-match-wins: the returned pattern (original form) feeds the
    deny reason so the LLM can see why it was denied.
    """
    abs_path = os.path.abspath(os.path.expanduser(path))

    # Compute cwd-relative form if path is under cwd; else None.
    rel_path: str | None
    try:
        rel_path = str(Path(abs_path).relative_to(cwd.resolve()))
    except ValueError:
        rel_path = None  # path is outside cwd — relative patterns can't match

    for pattern in patterns:
        # Absolute/tilde patterns: match the path directly.
        if pattern.startswith(("/", "~")):
            if _glob_match(abs_path, pattern):
                return pattern
        # Relative patterns: match against cwd-relative path (.gitignore style).
        elif rel_path is not None and _glob_match(rel_path, pattern):
            return pattern
    return None
