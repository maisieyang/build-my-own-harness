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

P10-T1 sub-units (all shipped in this commit area):

- 1a: :class:`Memory` dataclass + :class:`MemoryType` /
  :class:`MemoryScope` enums + :func:`compute_memory_signature`.
- 1b: :func:`parse_memory` via ``read_frontmatter_dict``.
- 1c: :func:`get_project_memory_dir` + :class:`UnknownMemoryError` /
  :class:`MemoryParseError`.

Subsequent tasks:

- T2: :class:`FilesystemMemoryStore` (6th ``markdown_store`` consumer).
- T3: relevance scoring + usage-tracking atomic rewrite.
- T4: ``prompts/`` refactor + CLAUDE.md cascade + memory injection.
- T5: ``oh memory list / show / path`` CLI.
- T6: E2E smoke + invariant verification + retro.

Public API:

    from openharness.memory import (
        Memory, MemoryType, MemoryScope,
        compute_memory_signature, parse_memory,
        get_project_memory_dir,
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

__all__ = [
    "Memory",
    "MemoryParseError",
    "MemoryScope",
    "MemoryType",
    "UnknownMemoryError",
    "compute_memory_signature",
    "get_project_memory_dir",
    "parse_memory",
]
