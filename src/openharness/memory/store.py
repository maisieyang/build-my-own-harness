""":class:`MemoryStore` Protocol + :class:`FilesystemMemoryStore` — P10-T2.

Per ``decisions/25-phase-10-boundary.md`` D28.4: Phase 10 hosts the
memory consumer on the existing
:class:`openharness.markdown_store.FilesystemMarkdownStore[T]` primitive
— the 6th consumer (commands / skills / bundles / Phase 9 plugins
multiplexer / this) and the **4th independent compounding test** of
Phase 8's substrate. If hosting requires any change to
``markdown_store/``, the abstraction failed under its 4th test;
stop and re-open the boundary doc.

One asymmetry vs the other 3 stores: memory has **no global layer** in
Phase 10. Team scope (which would be the cross-project layer) defers
to Phase 11 alongside ``check_team_memory_secrets``. The constructor
therefore takes ``project_dir`` only; ``global_dir=None`` is wired into
``super().__init__`` so the inherited ``_scan`` skips the global pass.

Same Protocol-based contract surface as :class:`SkillStore` (P5c-T1.1c):
``cli.py`` consumes :class:`MemoryStore` not :class:`FilesystemMemoryStore`
directly so tests inject in-memory stubs and Phase 11+ can swap
implementations (e.g., a remote-API-backed store) without changing
engine / prompt-injection wiring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openharness.markdown_store import EmptyMarkdownStore, FilesystemMarkdownStore
from openharness.memory.model import Memory, parse_memory

if TYPE_CHECKING:
    from pathlib import Path


class MemoryStore(Protocol):
    """Discovery + lookup contract the rest of the harness consumes.

    Two operations:

    - :meth:`discover` runs at bootstrap and caches its result for the
      lifetime of the store instance. Catalog stays frozen for the
      duration of an ``oh ask`` invocation (no hot reload in Phase 10).
    - :meth:`get` is called per relevance-injection cycle (P10-T3) —
      sync because it's an in-memory dict lookup after the first
      discover.
    """

    def discover(self) -> dict[str, Memory]:
        """Return a ``name -> Memory`` mapping for all valid memories.

        Implementations:
        - skip malformed files (emit warning via observability logger,
          DO NOT raise)
        - resolve same-name collisions per their own policy
          (filesystem store: first sorted wins, log + skip duplicate)
        """
        ...  # pragma: no cover - Protocol method body

    def get(self, name: str) -> Memory | None:
        """Look up a memory by name. ``None`` if not found.

        ``oh memory show <name>`` (P10-T5) calls this; missing name
        becomes :class:`UnknownMemoryError` at the CLI layer.
        """
        ...  # pragma: no cover - Protocol method body


class EmptyMemoryStore(EmptyMarkdownStore[Memory]):
    """Sentinel store with no memories. Used as the default for
    bootstrap paths that haven't constructed a filesystem store
    (e.g., ``--no-enable-memory`` CLI flag) and for tests that don't
    care about memory.

    One-line subclass of :class:`openharness.markdown_store.EmptyMarkdownStore`
    parameterized to :class:`Memory` — preserves the public class name
    so callers' ``isinstance(store, EmptyMemoryStore)`` checks keep
    working (matches :class:`EmptySkillStore` / :class:`EmptyCommandStore`).
    """


class FilesystemMemoryStore(FilesystemMarkdownStore[Memory]):
    """Scan a single project directory for ``.md`` memory files.

    Phase 10 has no global / cross-project memory layer (D28.5
    deferred team scope to Phase 11), so the constructor takes
    ``project_dir`` only and pins ``global_dir=None`` into the
    parent. The directory comes from
    :func:`openharness.memory.paths.get_project_memory_dir` — a
    cwd-hashed sub-directory of ``~/.openharness/memory/``.

    Same caching + skip-not-fail discipline as
    :class:`FilesystemSkillStore`:

    - First :meth:`discover` call scans the directory once and caches.
    - Malformed files trigger ``parse_memory`` warnings and are
      dropped; one bad file never blocks others.
    - Same-name collisions inside the single layer: first sorted wins,
      duplicates emit ``memory_name_collision`` warning.

    The store does NOT watch the filesystem for changes. Hot reload
    isn't shipped in Phase 10; would land alongside the Phase 11
    extraction write path if real demand surfaces.
    """

    def __init__(self, *, project_dir: Path) -> None:
        # ``global_dir=None`` pinned into the parent — Phase 10 has no
        # cross-project layer. When team scope lands in Phase 11, the
        # constructor will grow a ``team_dir`` kwarg (or similar) and
        # the parent's _scan loop will iterate both layers.
        super().__init__(
            global_dir=None,
            project_dir=project_dir,
            parser=parse_memory,
            log_event_prefix="memory",
        )
