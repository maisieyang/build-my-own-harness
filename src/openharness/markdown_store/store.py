"""Generic filesystem markdown store — P8-T1.

:class:`FilesystemMarkdownStore` is the generic version of the three
duplicate ``FilesystemXStore`` classes from commands/skills/bundles.
It scans a global directory (defaults to a per-domain
``~/.openharness/<x>/``) and a project directory (defaults to
``<cwd>/.openharness/<x>/``), merges with project-overrides-global,
and caches the result.

:class:`EmptyMarkdownStore` is the generic sentinel — used when no
storage layer is configured (matches the parallel
:class:`~openharness.skills.store.EmptySkillStore`,
:class:`~openharness.commands.store.EmptyCommandStore`,
:class:`~openharness.bundles.store.EmptyBundleStore`).

Each domain's existing ``FilesystemXStore`` survives as a thin
subclass that fixes the parser kwarg — keeps public class names
stable (per D21.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class MarkdownDocument(Protocol):
    """Minimum shape every consumer dataclass exposes.

    All three current dataclasses (``Command`` / ``Skill`` /
    ``Bundle``) carry these fields as frozen dataclass attributes.
    The Protocol exists so the generic store can read ``.name`` for
    catalog keys and ``.source_path`` for collision logging without
    coupling to any specific dataclass.

    Declared as ``@property``-style so frozen-dataclass attributes
    (read-only via ``__post_init__``-only set) satisfy the contract
    under mypy --strict (writable attribute Protocol vs read-only
    dataclass attribute is invariant otherwise).
    """

    @property
    def name(self) -> str: ...

    @property
    def source_path(self) -> Path: ...


T = TypeVar("T", bound=MarkdownDocument)


class EmptyMarkdownStore(Generic[T]):
    """Sentinel store. ``discover() == {}``; ``get(name)`` always
    returns ``None``.

    Each domain's existing ``EmptyXStore`` becomes a one-line
    subclass:

    ::

        class EmptyCommandStore(EmptyMarkdownStore[Command]):
            pass

    The subclass exists so callers' ``isinstance(store,
    EmptyCommandStore)`` checks keep working (preserves the public
    class name).
    """

    def discover(self) -> dict[str, T]:
        return {}

    def get(self, name: str) -> T | None:
        del name
        return None


class FilesystemMarkdownStore(Generic[T]):
    """Scan up to two filesystem layers and merge (project wins).

    Constructor takes:

    - ``global_dir`` / ``project_dir``: layered scan locations.
      ``None`` skips that layer. Same shape as the three duplicates
      it replaces.
    - ``parser``: ``Callable[[Path], T | None]`` — the domain's
      ``parse_X`` function. Each entry in the discovered directory
      is fed through this; ``None`` means "skip this file" (warning
      already logged inside the parser).
    - ``log_event_prefix``: string used for collision/override log
      events (``<prefix>_override`` for project-wins on same name,
      ``<prefix>_name_collision`` for unexpected duplicate inside
      the same layer).

    Cache: ``discover()`` and ``get()`` populate ``_cache`` on
    first call; subsequent calls return the cached snapshot
    (same behavior as the three duplicates).
    """

    def __init__(
        self,
        *,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
        parser: Callable[[Path], T | None],
        log_event_prefix: str,
    ) -> None:
        self._global_dir = global_dir
        self._project_dir = project_dir
        self._parser = parser
        self._log_event_prefix = log_event_prefix
        self._cache: dict[str, T] | None = None
        # Logger uses the same name as the prefix's domain, e.g.
        # "command" prefix → "commands" logger (Phase 5b convention
        # used "commands" plural for the logger name; preserve).
        # Callers pass log_event_prefix singular ("command",
        # "skill", "bundle") and we map → plural ("commands",
        # "skills", "bundles").
        self._logger = get_logger(self._logger_name())

    def _logger_name(self) -> str:
        # Match the existing per-domain logger names so collision
        # log events appear in the same channel as the per-domain
        # parser warnings. Each consumer's logger is the plural form.
        return {
            "command": "commands",
            "skill": "skills",
            "bundle": "bundles",
        }.get(self._log_event_prefix, self._log_event_prefix)

    def discover(self) -> dict[str, T]:
        if self._cache is None:
            self._cache = self._scan()
        return dict(self._cache)

    def get(self, name: str) -> T | None:
        if self._cache is None:
            self._cache = self._scan()
        return self._cache.get(name)

    def _scan(self) -> dict[str, T]:
        merged: dict[str, T] = {}
        if self._global_dir is not None:
            self._merge_dir(merged, self._global_dir, layer="global")
        if self._project_dir is not None:
            self._merge_dir(merged, self._project_dir, layer="project")
        return merged

    def _merge_dir(
        self,
        merged: dict[str, T],
        directory: Path,
        *,
        layer: str,
    ) -> None:
        if not directory.exists() or not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            doc = self._parser(path)
            if doc is None:
                continue
            if doc.name in merged:
                if layer == "project":
                    self._logger.info(
                        f"{self._log_event_prefix}_override",
                        name=doc.name,
                        overrides=str(merged[doc.name].source_path),
                        new_source=str(doc.source_path),
                    )
                else:
                    self._logger.warning(
                        f"{self._log_event_prefix}_name_collision",
                        name=doc.name,
                        existing=str(merged[doc.name].source_path),
                        skipped=str(doc.source_path),
                    )
                    continue
            merged[doc.name] = doc
