"""``CommandStore`` Protocol + ``FilesystemCommandStore`` — P5b-T1.

Per ``decisions/14`` C2:two-layer storage(global + project)with
project-wins override. Mirrors ``skills/store.py`` structure verbatim;
the two will be candidates for a shared ``markdown_store`` helper in a
future Phase 7 polish task — Phase 5b explicitly does NOT refactor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openharness.commands.model import Command, parse_command
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("commands")


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


class EmptyCommandStore:
    """Sentinel store with no commands. Used when ``--no-commands`` is
    passed or as a placeholder where the caller doesn't care about
    Phase 5b semantics.
    """

    def discover(self) -> dict[str, Command]:
        return {}

    def get(self, name: str) -> Command | None:  # noqa: ARG002 — Protocol signature
        return None


class FilesystemCommandStore:
    """Scan two filesystem layers and merge:project entries override
    global entries on same ``name``. Catalog is frozen after first
    :meth:`discover` call — bootstrap-time discovery,not hot reload.
    """

    def __init__(
        self,
        *,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._global_dir = global_dir
        self._project_dir = project_dir
        self._cache: dict[str, Command] | None = None

    def discover(self) -> dict[str, Command]:
        if self._cache is None:
            self._cache = self._scan()
        return dict(self._cache)  # defensive copy

    def get(self, name: str) -> Command | None:
        if self._cache is None:
            self._cache = self._scan()
        return self._cache.get(name)

    def _scan(self) -> dict[str, Command]:
        merged: dict[str, Command] = {}
        if self._global_dir is not None:
            self._merge_dir(merged, self._global_dir, layer="global")
        if self._project_dir is not None:
            self._merge_dir(merged, self._project_dir, layer="project")
        return merged

    @staticmethod
    def _merge_dir(
        merged: dict[str, Command],
        directory: Path,
        *,
        layer: str,
    ) -> None:
        if not directory.exists() or not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            cmd = parse_command(path)
            if cmd is None:
                continue
            if cmd.name in merged:
                if layer == "project":
                    _logger.info(
                        "command_override",
                        name=cmd.name,
                        overrides=str(merged[cmd.name].source_path),
                        new_source=str(cmd.source_path),
                    )
                else:
                    _logger.warning(
                        "command_name_collision",
                        name=cmd.name,
                        existing=str(merged[cmd.name].source_path),
                        skipped=str(cmd.source_path),
                    )
                    continue
            merged[cmd.name] = cmd
