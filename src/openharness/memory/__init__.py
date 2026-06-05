"""Memory subsystem — Phase 10 (read path only).

Per ``decisions/25-phase-10-boundary.md``:

- D28.1: storage at ``~/.openharness/memory/<basename>-<sha1(cwd)[:12]>/``,
  outside the repo (private + non-git-tracked).
- D28.3: 14-field YAML frontmatter schema with ``use_count`` and
  ``last_used_at`` inlined (no separate ``.usage_index.json``).
- D28.4: :class:`FilesystemMemoryStore` will be the 6th consumer of
  :mod:`openharness.markdown_store` — the 4th compounding test of
  Phase 8's substrate.
- D28.5: only ``scope: private`` in Phase 10; ``team`` defers to
  Phase 11 (alongside extraction + secret scanning).
- D28.9: **no agent write path in Phase 10**. Memories are created
  by manual filesystem edits in this phase; Phase 11 adds the
  ``extract_memories_from_turn`` secondary LLM pass.

Phase 10 tasks:

- T1 (shipped): :class:`Memory` dataclass + :class:`MemoryType` /
  :class:`MemoryScope` enums + :func:`compute_memory_signature` +
  :func:`parse_memory` + :func:`get_project_memory_dir` +
  :class:`MemoryParseError` / :class:`UnknownMemoryError`.
- T2: :class:`MemoryStore` Protocol + :class:`FilesystemMemoryStore`
  (6th ``markdown_store`` consumer) + :class:`EmptyMemoryStore`.
- T3: relevance scoring + usage-tracking atomic rewrite.
- T4: ``prompts/`` refactor + CLAUDE.md cascade + memory injection.
- T5: ``oh memory list / show / path`` CLI.
- T6: E2E smoke + invariant verification + retro.

Public API:

    from openharness.memory import (
        Memory, MemoryType, MemoryScope,
        compute_memory_signature, parse_memory,
        get_project_memory_dir,
        MemoryStore, FilesystemMemoryStore, EmptyMemoryStore,
        MemoryParseError, UnknownMemoryError,
    )
"""

from __future__ import annotations

from openharness.memory.errors import MemoryParseError, UnknownMemoryError
from openharness.memory.model import (
    Memory,
    MemoryScope,
    MemoryType,
    compute_memory_signature,
    parse_memory,
)
from openharness.memory.paths import get_project_memory_dir
from openharness.memory.store import (
    EmptyMemoryStore,
    FilesystemMemoryStore,
    MemoryStore,
)

# P16-T2 (D36.7): ``select_relevant_memories`` and ``mark_memory_used`` are
# NOT re-exported here. Phase 10's harness-side relevance ranking is
# deprecated — the LLM does selection from the MEMORY.md index now
# (D36.4). Existing internal callers (tests, the deprecated relevance
# module itself) keep working via the fully-qualified paths
# ``openharness.memory.relevance.select_relevant_memories`` and
# ``openharness.memory.usage.mark_memory_used``.

__all__ = [
    "EmptyMemoryStore",
    "FilesystemMemoryStore",
    "Memory",
    "MemoryParseError",
    "MemoryScope",
    "MemoryStore",
    "MemoryType",
    "UnknownMemoryError",
    "compute_memory_signature",
    "get_project_memory_dir",
    "parse_memory",
]
