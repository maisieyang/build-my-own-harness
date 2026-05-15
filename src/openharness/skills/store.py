"""``SkillStore`` Protocol + ``FilesystemSkillStore`` — P5c-T1.1c.

Per ``decisions/12`` L2 / L3:two-layer storage(global + project)with
project-wins override, surfaced to the harness as a Protocol so:

- ``cli.py`` consumes ``SkillStore`` not ``FilesystemSkillStore`` directly
  → tests inject in-memory stubs without touching the filesystem.
- ``QueryContext.skill_store`` types against the contract → future stores
  (e.g., remote API-backed)plug in without changing engine / CLI.

Mirrors the :class:`SupportsStreamingMessages` Protocol pattern in
``api/client.py``(structural typing, no inheritance required).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openharness.observability.logging import get_logger
from openharness.skills.model import Skill, parse_skill

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("skills")


class SkillStore(Protocol):
    """Discovery + lookup contract that the rest of the harness consumes.

    Two operations:

    - :meth:`discover` runs **once at bootstrap**(catalog stays frozen
      for the duration of an ``oh ask`` invocation per ``decisions/12``
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


class EmptySkillStore:
    """Sentinel store with no skills. Used as the default for
    :class:`QueryContext.skill_store` so existing tests / CLI flows that
    don't care about skills don't need to construct a filesystem store.
    """

    def discover(self) -> dict[str, Skill]:
        return {}

    def get(self, name: str) -> Skill | None:  # noqa: ARG002 — Protocol signature; sentinel ignores name
        return None


class FilesystemSkillStore:
    """Scan two filesystem layers and merge:project entries override
    global entries on same ``name``.

    Discovery is performed lazily on first :meth:`discover` call and
    cached for the lifetime of the store instance — bootstrap calls
    ``discover()`` once, ``LoadSkillTool.execute`` only uses :meth:`get`
    against the cache.

    The store does NOT watch the filesystem for changes. Catalog is
    frozen for the duration of an ``oh ask`` invocation per the
    boundary doc Out-of-Scope.
    """

    def __init__(
        self,
        *,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._global_dir = global_dir
        self._project_dir = project_dir
        self._cache: dict[str, Skill] | None = None

    def discover(self) -> dict[str, Skill]:
        if self._cache is None:
            self._cache = self._scan()
        return dict(self._cache)  # defensive copy

    def get(self, name: str) -> Skill | None:
        if self._cache is None:
            self._cache = self._scan()
        return self._cache.get(name)

    def _scan(self) -> dict[str, Skill]:
        # Global first, so project entries can naturally overwrite via
        # ``.update()``.
        merged: dict[str, Skill] = {}
        if self._global_dir is not None:
            self._merge_dir(merged, self._global_dir, layer="global")
        if self._project_dir is not None:
            self._merge_dir(merged, self._project_dir, layer="project")
        return merged

    @staticmethod
    def _merge_dir(
        merged: dict[str, Skill],
        directory: Path,
        *,
        layer: str,
    ) -> None:
        if not directory.exists() or not directory.is_dir():
            return
        # Sorted for deterministic ordering — tests can assert on order
        # and CI runs are reproducible across machines.
        for path in sorted(directory.glob("*.md")):
            skill = parse_skill(path)
            if skill is None:
                continue
            if skill.name in merged:
                if layer == "project":
                    _logger.info(
                        "skill_override",
                        name=skill.name,
                        overrides=str(merged[skill.name].source_path),
                        new_source=str(skill.source_path),
                    )
                else:
                    # Within the same layer, a later file with the same
                    # ``name`` collides with an earlier one — log + skip
                    # the new one(filesystem order is deterministic so
                    # this is reproducible).
                    _logger.warning(
                        "skill_name_collision",
                        name=skill.name,
                        existing=str(merged[skill.name].source_path),
                        skipped=str(skill.source_path),
                    )
                    continue
            merged[skill.name] = skill
