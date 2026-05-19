"""``CommandStore`` Protocol + ``FilesystemCommandStore`` — P5b-T1.

Per ``decisions/14`` C2:two-layer storage(global + project)with
project-wins override.

P8 refactor: the duplicated scan + merge machinery moved to
:class:`openharness.markdown_store.FilesystemMarkdownStore`. This
module keeps the :class:`CommandStore` Protocol (the public
contract) + a thin subclass that fixes the parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openharness.commands.model import Command, parse_command
from openharness.markdown_store import EmptyMarkdownStore, FilesystemMarkdownStore

if TYPE_CHECKING:
    from pathlib import Path


class CommandStore(Protocol):
    """Discovery + lookup contract for slash commands.

    Two operations:

    - :meth:`discover` runs **once at CLI bootstrap**(catalog stays
      frozen for the duration of an ``oh ask`` invocation per
      ``decisions/14`` Out of Scope:"interactive command discovery").
    - :meth:`get` is called by :func:`expand_command` to look up
      ``/<name>`` invocations.

    Sync because filesystem I/O at startup is trivial and sync is
    easier to test.
    """

    def discover(self) -> dict[str, Command]:
        """Return a ``name -> Command`` mapping for all valid commands."""
        ...  # pragma: no cover - Protocol method body

    def get(self, name: str) -> Command | None:
        """Look up a command by name. ``None`` if not found.

        :func:`expand_command` turns ``None`` into an
        :class:`openharness.commands.errors.UnknownCommandError` for the
        CLI to surface as a user-facing error.
        """
        ...  # pragma: no cover - Protocol method body


class EmptyCommandStore(EmptyMarkdownStore[Command]):
    """Sentinel store with no commands. Used when ``--no-commands`` is
    passed or as a placeholder where the caller doesn't care about
    Phase 5b semantics.

    Subclass of :class:`openharness.markdown_store.EmptyMarkdownStore`
    parameterized to :class:`Command` — preserves the public class
    name (P8 D21.3).
    """


class FilesystemCommandStore(FilesystemMarkdownStore[Command]):
    """Scan two filesystem layers and merge:project entries override
    global entries on same ``name``. Catalog is frozen after first
    :meth:`discover` call — bootstrap-time discovery,not hot reload.

    Subclass of :class:`openharness.markdown_store.FilesystemMarkdownStore`
    fixing the parser to :func:`parse_command` and the log event
    prefix to ``"command"`` — preserves the public class name + hides
    the generic from callers (P8 D21.3).
    """

    def __init__(
        self,
        *,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        super().__init__(
            global_dir=global_dir,
            project_dir=project_dir,
            parser=parse_command,
            log_event_prefix="command",
        )
