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
from typing import TYPE_CHECKING

from openharness.permissions.checker import DecisionResult

if TYPE_CHECKING:
    from pydantic import BaseModel

    from openharness.config import Settings
    from openharness.tools import ToolRegistry
    from openharness.tools.base import ToolExecutionContext

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


# Bash catastrophic substring deny-list — carry-over from Phase 2
# DenyListChecker. These patterns are a small framework-owned safety floor
# for the Bash tool;P3-T4 Hook lets users add custom command-level checks
# (e.g., LLM-as-judge on the shell command for ambiguous cases).
_BASH_DENY_PATTERNS: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf /*",
    ":(){ :|:& };:",  # classic fork bomb
    "mkfs",
    "dd if=/dev/zero of=/dev/",
    "> /dev/sda",
    "chmod -R 777 /",
)


def _matches_bash_deny(command: str) -> str | None:
    """Return the catastrophic Bash pattern that matched, or None."""
    for pattern in _BASH_DENY_PATTERNS:
        if pattern in command:
            return pattern
    return None


def _extract_path_arg(args: BaseModel) -> str | None:
    """Best-effort extraction of a path-bearing field from validated args.

    Conventions across our tools (Read/Write/Edit use ``path``; Bash uses
    ``command`` which is shell syntax not a path; Grep uses ``pattern``
    + ``glob`` neither of which is a single path to gate).
    Returns ``None`` when no recognizable path field — Tier 1/2/3 then
    auto-skip (correct:Bash safety is on ``command``, not path).
    """
    for attr in ("path", "file_path"):
        value = getattr(args, attr, None)
        if isinstance(value, str):
            return value
    return None


class TierBasedPermissionChecker:
    """P3-T3.3e:full three-Tier AuthZ checker composing all building blocks.

    Replaces Phase 2's ``DenyListChecker`` once cli.py wires this in.
    Implements the :class:`PermissionChecker` Protocol structurally — no
    inheritance.

    Resolution order (first-match-wins):

    1. **Bash deny-list** (carry-over for catastrophic shell patterns)
    2. **Tier 1**:hardcoded sensitive paths → DENY
    3. **Tier 2**:user-config glob patterns from ``Settings.deny_paths`` → DENY
    4. **Tier 3**:mode-based — write/exec tools outside cwd → **ASK** (G)
    5. fallthrough → ALLOW

    Dependencies injected at construction (per Three-Axis):

    - ``registry``:to look up ``tool.is_read_only`` for Tier 3
    - ``settings``:to read ``deny_paths`` for Tier 2

    evaluate() signature stays minimal (matches the Protocol from
    ``checker.py``) — per-call data only;config / registry are state.
    """

    def __init__(self, registry: ToolRegistry, settings: Settings) -> None:
        self._registry = registry
        self._deny_paths = settings.deny_paths

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DecisionResult:
        # 1. Bash catastrophic deny-list (Phase 2 carry-over).
        if tool_name == "Bash":
            command = getattr(args, "command", None)
            if isinstance(command, str):
                bash_pat = _matches_bash_deny(command)
                if bash_pat is not None:
                    return DecisionResult.deny(f"matches catastrophic Bash pattern {bash_pat!r}")

        # Path-bearing tools enter the 3-Tier path filter.
        path = _extract_path_arg(args)

        if path is not None:
            # 2. Tier 1 hardcoded sensitive paths.
            t1 = _matches_tier1(path)
            if t1 is not None:
                return DecisionResult.deny(f"path {path!r} matches sensitive system path ({t1})")
            # 3. Tier 2 user globs.
            t2 = _matches_tier2(path, self._deny_paths, context.cwd)
            if t2 is not None:
                return DecisionResult.deny(f"path {path!r} matches deny rule {t2!r}")

        # 4. Tier 3 mode-based (needs tool.is_read_only).
        try:
            tool = self._registry.get(tool_name)
        except KeyError:
            # Tool not found:return ALLOW;the loop will catch this via
            # registry.get separately and produce a "tool not found" error.
            # AuthZ shouldn't second-guess that.
            return DecisionResult.allow()

        t3 = _matches_tier3(tool.is_read_only, path, context.cwd)
        if t3 is not None:
            # Tier 3 maps to ASK (Three-Axis G):writing outside cwd is a
            # plausible legitimate intent;loop layer + PermissionMode
            # decide final outcome.
            return DecisionResult.ask(t3)

        return DecisionResult.allow()
