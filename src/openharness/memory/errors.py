"""Memory subsystem errors — P10-T1.1c.

Per ``decisions/25-phase-10-boundary.md``:

- :class:`MemoryParseError` — raised by Phase 11's :func:`mark_memory_used`
  rewrite path when the file's frontmatter cannot be re-parsed before
  the atomic write (Phase 10 includes the class so Phase 11 doesn't add
  a new error taxonomy mid-feature; the class is reserved, not yet
  raised by Phase 10 code).
- :class:`UnknownMemoryError` — raised by ``oh state memory show <name>``
  when the requested memory doesn't exist; mirrors
  :class:`UnknownCommandError` from Phase 5b for UX consistency.

Both inherit from :class:`OpenHarnessError` so they land in the root
``except`` arm of ``cli.py``'s error chain alongside
:class:`LoopError` / :class:`ToolError` / :class:`PermissionError`.
"""

from __future__ import annotations

from openharness.errors import OpenHarnessError


class MemoryParseError(OpenHarnessError):
    """Reserved for Phase 11's usage-tracking write path.

    Phase 10 itself never raises this — :func:`parse_memory` returns
    ``None`` + warning log on any parse failure (skip-not-fail
    discipline). The class is declared now so Phase 11's
    :func:`mark_memory_used` can raise it if the on-disk file becomes
    unparseable between the relevance-injection-time read and the
    atomic frontmatter rewrite (e.g., another process truncated the
    file in between).
    """


class UnknownMemoryError(OpenHarnessError):
    """User invoked ``oh state memory show <name>`` but no memory matches.

    Carries the queried ``name`` and the catalog of ``available``
    memory names so the CLI can render an actionable error message
    (mirrors :class:`openharness.commands.errors.UnknownCommandError`).
    """

    def __init__(self, name: str, *, available: list[str]) -> None:
        catalog = ", ".join(sorted(available)) if available else "(none)"
        super().__init__(f"no memory named {name!r}; available memories: {catalog}")
        self.name = name
        self.available = available
