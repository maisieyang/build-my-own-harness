"""Bundle errors — P5d-T1.

Single error type ``UnknownBundleError`` for "slash command references
a nonexistent bundle name" + "bundle frontmatter references a
nonexistent hook name". Inherits from ``OpenHarnessError`` so it lands
in cli.py's root except chain alongside other framework errors.
"""

from __future__ import annotations

from openharness.errors import OpenHarnessError


class UnknownBundleError(OpenHarnessError):
    """Bundle or hook name referenced but not found.

    Two cases:

    - Slash command's ``mode: <name>`` references a bundle that doesn't
      exist in the global / project bundles directory.
    - Bundle's ``hooks: [name1, name2]`` references a hook that's not
      in ``BUILTIN_HOOKS``.

    Carries the queried ``name`` and the catalog of ``available`` names
    so the CLI can render an actionable error message (same UX pattern
    as :class:`openharness.commands.UnknownCommandError`).
    """

    def __init__(self, name: str, *, available: list[str], kind: str = "bundle") -> None:
        catalog = ", ".join(sorted(available)) if available else "(none)"
        super().__init__(f"no {kind} named {name!r};available {kind}s:{catalog}")
        self.name = name
        self.available = available
        self.kind = kind
