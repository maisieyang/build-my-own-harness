"""``SkillStore`` Protocol + ``FilesystemSkillStore`` — P5c-T1.1c.

Per ``decisions/12`` L2 / L3:two-layer storage(global + project)with
project-wins override, surfaced to the harness as a Protocol so:

- ``cli.py`` consumes ``SkillStore`` not ``FilesystemSkillStore`` directly
  → tests inject in-memory stubs without touching the filesystem.
- ``QueryContext.skill_store`` types against the contract → future stores
  (e.g., remote API-backed)plug in without changing engine / CLI.

P8 refactor: the scan + merge machinery moved to
:class:`openharness.markdown_store.FilesystemMarkdownStore`. This
module keeps the :class:`SkillStore` Protocol (the public contract
the engine consumes) + thin subclasses that fix the parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openharness.markdown_store import EmptyMarkdownStore, FilesystemMarkdownStore
from openharness.skills.model import Skill, parse_skill

if TYPE_CHECKING:
    from pathlib import Path


class SkillStore(Protocol):
    """Discovery + lookup contract that the rest of the harness consumes.

    Two operations:

    - :meth:`discover` runs **once at bootstrap**(catalog stays frozen
      for the duration of a non-interactive invocation per ``decisions/12``
      "Out of scope:hot reload"). Sync because filesystem I/O at
      startup is trivial and sync is easier to test.
    - :meth:`get` is called per ``LoadSkill`` tool dispatch — also sync
      because in-memory map lookup after discovery.
    """

    def discover(self) -> dict[str, Skill]:
        """Return a ``name -> Skill`` mapping for all valid skills found.

        Implementations:
        - skip malformed files(emit warning via observability logger,
          DO NOT raise)
        - resolve same-name collisions according to their own policy
          (filesystem store:project overrides global)
        """
        ...  # pragma: no cover - Protocol method body

    def get(self, name: str) -> Skill | None:
        """Look up a skill by name. ``None`` if not found.

        ``LoadSkillTool`` calls this when the LLM emits
        ``LoadSkill(name="x")``. Returning ``None`` lets the tool turn
        the miss into an ``is_error=True`` ToolResult that includes the
        catalog of available skills — LLM gets a helpful nudge, not a
        crash.
        """
        ...  # pragma: no cover - Protocol method body


class EmptySkillStore(EmptyMarkdownStore[Skill]):
    """Sentinel store with no skills. Used as the default for
    :class:`QueryContext.skill_store` so existing tests / CLI flows that
    don't care about skills don't need to construct a filesystem store.

    Subclass of :class:`openharness.markdown_store.EmptyMarkdownStore`
    parameterized to :class:`Skill` (P8 D21.3).
    """


class FilesystemSkillStore(FilesystemMarkdownStore[Skill]):
    """Scan two filesystem layers and merge:project entries override
    global entries on same ``name``.

    Discovery is performed lazily on first :meth:`discover` call and
    cached for the lifetime of the store instance — bootstrap calls
    ``discover()`` once, ``LoadSkillTool.execute`` only uses :meth:`get`
    against the cache.

    The store does NOT watch the filesystem for changes. Catalog is
    frozen for the duration of a non-interactive invocation per the
    boundary doc Out-of-Scope.
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
            parser=parse_skill,
            log_event_prefix="skill",
        )
