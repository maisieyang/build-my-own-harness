"""Slash commands subsystem — Phase 5b.

User-facing UX shortcut: markdown files in ``~/.openharness/commands/`` or
``<cwd>/.openharness/commands/`` define ``/cmd`` shortcuts. When the user
invokes ``oh ask "/review last commit"``, the CLI looks up ``review.md``,
substitutes ``{args}``, and the resolved body becomes the user message
that ``run_query`` receives.

Per ``decisions/14-phase-5b-boundary.md``:

- C1: pure prompt template — no system prompt override, no catalog filter,
  no hook injection, no permission overlay. ModeBundle defers to Phase 5d.
- C2: ``~/.openharness/commands/<name>.md`` (global) + ``<cwd>/.openharness/
  commands/<name>.md`` (project). Project overrides global on same ``name``.
- C3: ``{args}`` placeholder substitution; tail append fallback when body
  has no placeholder.
- C4: all invocations one-shot. No persistent mode state.
- C5: NO hook integration. Slash commands are pre-LLM — they vanish before
  ``run_query`` ever runs.

Cross-cutting invariant: Phase 5b is the **fourth tenant test** of the
Phase 3 abstractions. Slash commands must NOT touch:
``permissions/``, ``hooks/``, ``engine/query.py``, ``engine/context.py``,
``observability/logging.py``, ``prompts.py``, ``tools/``. They live
entirely in ``commands/`` + ``cli.py``.

Skills (Phase 5c) and Commands (Phase 5b) share the markdown+frontmatter
shape but their **role** is fundamentally different:

- **Skills** = LLM-facing knowledge — catalog in system prompt, body via
  tool call. The LLM is the consumer.
- **Commands** = User-facing UX — pre-LLM resolution, the LLM never
  knows commands exist. The user is the consumer.

A planned Phase 7 refactor may extract the shared ``markdown_store``
helper once both patterns prove they share the abstraction. Phase 5b
explicitly does NOT refactor — it ships parallel structure.

Public API:

    from openharness.commands import (
        Command, parse_command,
        FilesystemCommandStore, EmptyCommandStore, CommandStore,
        expand_command, UnknownCommandError,
    )
"""

from __future__ import annotations

from openharness.commands.errors import UnknownCommandError
from openharness.commands.expand import expand_command
from openharness.commands.model import Command, parse_command
from openharness.commands.store import (
    CommandStore,
    EmptyCommandStore,
    FilesystemCommandStore,
)

__all__ = [
    "Command",
    "CommandStore",
    "EmptyCommandStore",
    "FilesystemCommandStore",
    "UnknownCommandError",
    "expand_command",
    "parse_command",
]
