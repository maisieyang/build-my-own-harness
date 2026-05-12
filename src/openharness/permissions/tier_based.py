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
