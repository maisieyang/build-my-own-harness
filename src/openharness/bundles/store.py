"""``BundleStore`` Protocol + ``FilesystemBundleStore`` — P5d-T1.

Per ``decisions/17-phase-5d-boundary.md`` D19.2:two-layer storage
(global + project)with project-wins override. Same machinery as
``commands/store.py`` and ``skills/store.py`` — would be a Phase 8
``markdown_store/`` extraction candidate but kept parallel for now
(matches the explicit not-doing decision in 5b retro §3.5 and 5c
retro §5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openharness.bundles.model import Bundle, parse_bundle
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("bundles")


class BundleStore(Protocol):
    """Discovery + lookup contract for ModeBundle storage."""

    def discover(self) -> dict[str, Bundle]:
        """Return all valid bundles found across configured layers."""
        ...  # pragma: no cover - Protocol method body

    def get(self, name: str) -> Bundle | None:
        """Look up a bundle by name; ``None`` if not found."""
        ...  # pragma: no cover - Protocol method body


class EmptyBundleStore:
    """Sentinel store. Used when no bundle storage layer is configured
    (or for explicit "ignore bundles" path — same shape as
    :class:`openharness.skills.store.EmptySkillStore`).
    """

    def discover(self) -> dict[str, Bundle]:
        return {}

    def get(self, name: str) -> Bundle | None:  # noqa: ARG002
        return None


class FilesystemBundleStore:
    """Scan two filesystem layers and merge (project overrides global)."""

    def __init__(
        self,
        *,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._global_dir = global_dir
        self._project_dir = project_dir
        self._cache: dict[str, Bundle] | None = None

    def discover(self) -> dict[str, Bundle]:
        if self._cache is None:
            self._cache = self._scan()
        return dict(self._cache)

    def get(self, name: str) -> Bundle | None:
        if self._cache is None:
            self._cache = self._scan()
        return self._cache.get(name)

    def _scan(self) -> dict[str, Bundle]:
        merged: dict[str, Bundle] = {}
        if self._global_dir is not None:
            self._merge_dir(merged, self._global_dir, layer="global")
        if self._project_dir is not None:
            self._merge_dir(merged, self._project_dir, layer="project")
        return merged

    @staticmethod
    def _merge_dir(
        merged: dict[str, Bundle],
        directory: Path,
        *,
        layer: str,
    ) -> None:
        if not directory.exists() or not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            bundle = parse_bundle(path)
            if bundle is None:
                continue
            if bundle.name in merged:
                if layer == "project":
                    _logger.info(
                        "bundle_override",
                        name=bundle.name,
                        overrides=str(merged[bundle.name].source_path),
                        new_source=str(bundle.source_path),
                    )
                else:
                    _logger.warning(
                        "bundle_name_collision",
                        name=bundle.name,
                        existing=str(merged[bundle.name].source_path),
                        skipped=str(bundle.source_path),
                    )
                    continue
            merged[bundle.name] = bundle
