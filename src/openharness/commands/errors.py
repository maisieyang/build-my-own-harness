"""Slash command errors — P5b-T2.

Single error type:``UnknownCommandError`` — raised by
:func:`expand_command` when the user invokes ``/<name>`` but no
command with that name is registered. The CLI catches it and renders
a user-facing message containing the available catalog so the user
can self-correct.

Inherits from :class:`OpenHarnessError` so it lands in the root
``except`` arm of ``cli.py``'s error chain alongside ``LoopError`` /
``ToolError`` / ``PermissionError`` / ``HookError``. The boundary
doc(decisions/14)does NOT extend the error taxonomy further — slash
commands are a thin CLI-input transformation,not a new error
category.
"""

from __future__ import annotations

from openharness.errors import OpenHarnessError


class UnknownCommandError(OpenHarnessError):
    """User invoked ``/<name>`` but no command with that name is registered.

    Carries the queried ``name`` and the catalog of ``available`` command
    names so the CLI can render an actionable error message.
    """

    def __init__(self, name: str, *, available: list[str]) -> None:
        catalog = ", ".join(sorted(available)) if available else "(none)"
        super().__init__(f"no slash command named {name!r};available commands:{catalog}")
        self.name = name
        self.available = available
