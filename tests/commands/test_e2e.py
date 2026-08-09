"""End-to-end + cross-cutting invariant verification — P5b-T4.

End-to-end smoke for slash commands is already covered by
``tests/cli/test_cli.py::TestSlashCommands`` (T3 wire-level tests
verify ``/cmd args`` resolves to an expanded user message reaching
the LLM via the stub client). This file focuses on the **fourth
tenant test** of the Phase 3 cross-cutting invariant.

Per ``decisions/14`` cross-cutting section, slash commands must NOT
appear in any of the LLM-facing infrastructure files. Slash command
resolution happens pre-LLM in ``cli.py`` + ``commands/`` only. If any
``Command``-related identifier leaks into the protected modules, the
abstraction is compromised and we re-open the boundary.

This is **stronger than the Skills invariant test** (Phase 5c) because
slash commands shouldn't even reach engine/context.py, prompts.py,
or tools/ — they vanish before any of those see the prompt.
"""

from __future__ import annotations

import importlib
import inspect
import re


class TestCrossCuttingInvariant:
    """Phase 5b is the **fourth tenant test** of Phase 3 abstractions.

    Wider protected-module set than Skills (5c): commands also must NOT
    touch ``engine/context.py``, ``prompts.py``, ``tools/__init__.py``.
    Slash command resolution is a CLI-level transformation, period.
    """

    _PROTECTED_MODULES = (
        "openharness.hooks.executor",
        "openharness.hooks.registry",
        "openharness.engine.query",
        "openharness.engine.context",
        "openharness.observability.logging",
        "openharness.prompts",
        "openharness.tools",
    )

    # Identifiers the protected modules MUST NOT mention. Word-boundary
    # match so ``Bash``'s ``command:`` field (lowercase) doesn't trip.
    _FORBIDDEN_IDENTIFIERS = (
        "Command",
        "CommandStore",
        "FilesystemCommandStore",
        "EmptyCommandStore",
        "parse_command",
        "expand_command",
        "UnknownCommandError",
    )

    @staticmethod
    def _strip_comments_and_docstrings(source: str) -> str:
        """Best-effort strip of inline comments + module/class docstrings.

        Lines starting with a hash become empty.
        Triple-quoted docstrings between balanced triple-double-quote are
        blanked. Not a full Python parser — but sufficient because the
        protected modules are written in conventional Python style. False
        positives would surface as test failures and prompt explicit review.
        """
        # Remove triple-quoted docstrings (both """ and ''' variants).
        cleaned = re.sub(r'"""[\s\S]*?"""', "", source, flags=re.MULTILINE)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned, flags=re.MULTILINE)
        # Drop ``# ...`` comments (per-line).
        cleaned_lines = []
        for line in cleaned.splitlines():
            stripped = line.split("#", 1)[0]
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines)

    def test_protected_modules_do_not_reference_commands(self) -> None:
        for mod_name in self._PROTECTED_MODULES:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            code = self._strip_comments_and_docstrings(source)
            for forbidden in self._FORBIDDEN_IDENTIFIERS:
                # Word-boundary regex so ``Bash``'s ``command`` field
                # (lowercase) doesn't false-match ``Command``.
                pattern = rf"\b{re.escape(forbidden)}\b"
                assert not re.search(pattern, code), (
                    f"{mod_name} references {forbidden!r} — cross-cutting "
                    f"invariant violated. Slash commands must NOT touch any "
                    f"LLM-facing module — they're a pre-LLM CLI transform "
                    f"that vanishes before run_query / system prompt / tool "
                    f"dispatch ever see the prompt."
                )


class TestCommandsAreNotToolsOrSkills:
    """Belt-and-braces:slash commands are NOT BaseTool subclasses,not
    SkillStore-compatible, not registered in any catalog the LLM sees.
    Catches the temptation to "while we're at it, expose commands to the
    LLM" — they're user-facing UX,not LLM-facing knowledge.
    """

    def test_command_is_not_a_BaseTool_subclass(self) -> None:
        from openharness.commands import Command
        from openharness.tools.base import BaseTool

        assert not issubclass(Command, BaseTool), (
            "Command must NOT inherit from BaseTool — commands are "
            "user-facing CLI shortcuts, not LLM-callable tools. The "
            "Skills/Commands role split is load-bearing."
        )

    def test_commands_package_does_not_import_BaseTool(self) -> None:
        # If commands/__init__.py grew a BaseTool import, that'd suggest
        # the role split has eroded. Test prevents regression.
        import openharness.commands as commands_pkg

        source = inspect.getsource(commands_pkg)
        assert "BaseTool" not in source, (
            "commands/ must not import BaseTool — slash commands are "
            "pre-LLM, BaseTool is for LLM-callable tools."
        )

    def test_command_store_is_not_in_QueryContext(self) -> None:
        # Skills added ``skill_store`` to QueryContext because LoadSkill
        # consumes it. Commands have no analogous need — they're
        # resolved before QueryContext is built. If a ``command_store``
        # field ever appears, the resolution-stage discipline has broken.
        from dataclasses import fields

        from openharness.engine.context import QueryContext

        field_names = {f.name for f in fields(QueryContext)}
        assert "command_store" not in field_names, (
            "QueryContext must NOT carry a command_store field — slash "
            "commands resolve in cli.py before QueryContext exists. "
            "If you need this, you're probably trying to ship ModeBundle "
            "(Phase 5d) into Phase 5b."
        )
