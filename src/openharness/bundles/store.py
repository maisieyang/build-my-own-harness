"""``BundleStore`` Protocol + ``FilesystemBundleStore`` — P5d-T1.

Per ``decisions/17-phase-5d-boundary.md`` D19.2:two-layer storage
(global + project)with project-wins override.

P8 refactor: the scan + merge machinery moved to
:class:`openharness.markdown_store.FilesystemMarkdownStore`. This
module keeps the :class:`BundleStore` Protocol + thin subclasses
that fix the parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openharness.bundles.model import Bundle, parse_bundle
from openharness.markdown_store import EmptyMarkdownStore, FilesystemMarkdownStore

if TYPE_CHECKING:
    from pathlib import Path


class BundleStore(Protocol):
    """Discovery + lookup contract for ModeBundle storage."""

    def discover(self) -> dict[str, Bundle]:
        """Return all valid bundles found across configured layers."""
        ...  # pragma: no cover - Protocol method body

    def get(self, name: str) -> Bundle | None:
        """Look up a bundle by name; ``None`` if not found."""
        ...  # pragma: no cover - Protocol method body


class EmptyBundleStore(EmptyMarkdownStore[Bundle]):
    """Sentinel store. Used when no bundle storage layer is configured
    (or for explicit "ignore bundles" path — same shape as
    :class:`openharness.skills.store.EmptySkillStore`).

    Subclass of :class:`openharness.markdown_store.EmptyMarkdownStore`
    parameterized to :class:`Bundle` (P8 D21.3).
    """


class FilesystemBundleStore(FilesystemMarkdownStore[Bundle]):
    """Scan two filesystem layers and merge (project overrides global).

    Subclass of :class:`openharness.markdown_store.FilesystemMarkdownStore`
    fixing the parser to :func:`parse_bundle` and the log event
    prefix to ``"bundle"`` (P8 D21.3).
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
            parser=parse_bundle,
            log_event_prefix="bundle",
        )
