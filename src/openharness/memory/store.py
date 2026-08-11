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

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

import yaml

from openharness.markdown_store import EmptyMarkdownStore, FilesystemMarkdownStore
from openharness.memory.model import Memory, MemoryScope, parse_memory


class MemoryStore(Protocol):
    """Discovery + lookup contract the rest of the harness consumes.

    Two operations:

    - :meth:`discover` runs at bootstrap and caches its result for the
      lifetime of the store instance. Catalog stays frozen for the
      duration of a non-interactive invocation (no hot reload in Phase 10).
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

        ``oh state memory show <name>`` calls this; missing name
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
        self._memory_project_dir = project_dir

    def add_or_update(self, memory: Memory) -> Path:
        """Write ``memory`` to disk via signature dedup — P11-T4.4a.

        If an existing memory in the store has the same ``signature``,
        overwrite that file (content + metadata round-trip; same name
        if possible, else rename). Otherwise write to a new file with
        a slug-based name and collision suffix.

        Atomic write via ``tempfile + os.replace`` (same pattern as
        :func:`mark_memory_used`).

        Returns the path written to. Invalidates the discover() cache
        so the next discovery picks up the new file.

        Team-scope memories land under ``<project_dir>/team/``
        (P11-T4.4b D29.10); private memories under ``<project_dir>/``
        root.
        """
        target_dir = self._target_dir_for_scope(memory.scope)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Signature dedup: scan existing memories for same signature
        existing_path = self._find_by_signature(memory.signature, target_dir)
        if existing_path is not None:
            target_path = existing_path
        else:
            target_path = self._next_available_path(memory, target_dir)

        content = _render_memory_markdown(memory)
        _atomic_write(target_path, content)

        # Invalidate cache so subsequent discover() picks up the change
        self._cache = None
        return target_path

    def _target_dir_for_scope(self, scope: MemoryScope) -> Path:
        """Per D29.10: team memories under ``<project_dir>/team/``;
        private memories at root."""
        if scope is MemoryScope.TEAM:
            return self._memory_project_dir / "team"
        return self._memory_project_dir

    def _find_by_signature(self, signature: str, target_dir: Path) -> Path | None:
        """Scan ``target_dir`` for a memory file with matching signature.

        Does NOT use the cached discover() result because cache may be
        stale relative to recent writes; we read fresh from disk.
        Returns the first matching path or None.
        """
        if not target_dir.is_dir():
            return None
        for path in sorted(target_dir.glob("*.md")):
            existing = parse_memory(path)
            if existing is None:
                continue
            if existing.signature == signature:
                return path
        return None

    def _next_available_path(self, memory: Memory, target_dir: Path) -> Path:
        """Compute slug-based filename. Collision (different signature
        already at ``<slug>.md``) → append ``-<id-suffix>`` for
        uniqueness."""
        # Slug = memory.name (already NAME_PATTERN-safe, so it's a
        # valid filesystem name on POSIX + Windows).
        slug = memory.name
        candidate = target_dir / f"{slug}.md"
        if not candidate.exists():
            return candidate
        # Collision with a different-signature memory — append id suffix
        mem_id = memory.id
        assert mem_id is not None  # id is populated before a memory is persisted
        suffix = mem_id[:8] if len(mem_id) >= 8 else mem_id
        return target_dir / f"{slug}-{suffix}.md"


# ---------------------------------------------------------------------------
# Markdown rendering helpers — P11-T4.4a
# ---------------------------------------------------------------------------


def _render_memory_markdown(memory: Memory) -> str:
    """Serialize :class:`Memory` to a markdown file with YAML frontmatter.

    Round-trip-stable with :func:`parse_memory`: writing then reading
    yields a :class:`Memory` with identical field values (modulo
    timestamp ISO-string formatting which is canonical).
    """
    frontmatter = _build_frontmatter_dict(memory)
    yaml_text = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    body = memory.body.rstrip("\n") + "\n"
    return f"---\n{yaml_text}---\n\n{body}"


def _build_frontmatter_dict(memory: Memory) -> dict[str, Any]:
    """Build the YAML-ready dict for a Memory's frontmatter.

    Lists serialize as YAML lists; None values serialize as ``null``;
    timestamps serialize as ISO 8601 strings (rather than YAML's native
    timestamp scalar — keeps parse round-trip predictable).
    """
    return {
        "id": memory.id,
        "name": memory.name,
        "description": memory.description,
        "type": memory.type.value,
        "scope": memory.scope.value,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
        "signature": memory.signature,
        "importance": memory.importance,
        "tags": list(memory.tags),
        "use_count": memory.use_count,
        "last_used_at": (
            memory.last_used_at.isoformat() if memory.last_used_at is not None else None
        ),
        "ttl_days": memory.ttl_days,
        "disabled": memory.disabled,
        "supersedes": list(memory.supersedes),
    }


def _atomic_write(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` via same-dir tempfile + os.replace.

    Same primitive shape as :func:`mark_memory_used` and
    :func:`update_session_memory_file` — concurrent reads see either
    old or new file, never a half-written one. Orphan tmp cleanup
    on failure.
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
