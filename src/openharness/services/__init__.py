"""Services subsystem — Phase 11 (Summarization Substrate).

Stateful LLM-orchestration workflows. **Separated from
:mod:`openharness.compaction`** (deterministic byte-level primitives:
token counting, head/tail truncate, microcompact hook) by intent:
``compaction/`` shrinks bytes; ``services/`` calls the LLM to "凝结
认知" (condense cognition).

Per ``decisions/26-phase-11-boundary.md`` D29.1: one shared
:func:`summarize` primitive, three downstream triggers:

1. **Full compact L4** — context overflow → 9-slot LLM summary
   (:mod:`openharness.services.compact`, P11-T3)
2. **`extract_memories_from_turn`** — per-turn secondary pass that
   proposes 0-3 durable memories (:mod:`openharness.services.extract`,
   P11-T4)
3. **`/compact` slash command** — manual user trigger for L4 (CLI
   layer, P11-T5)

Plus the deterministic checkpoint writer
(:mod:`openharness.services.session_memory`, P11-T2) that L4's
escalation predecessor (L3) reads to avoid the LLM call entirely
when a fresh checkpoint exists.

Sub-units land incrementally (see ``tasks/phase-11-plan.md``):

- P11-T1 (this commit area): :func:`summarize` primitive
- P11-T2: ``session_memory.py``
- P11-T3: ``compact.py`` (L2/L3/L4 escalation)
- P11-T4: ``extract.py``
- P11-T5/T6/T7: CLI + tail debts + E2E

Public API (so far):

    from openharness.services import summarize
"""

from __future__ import annotations

from openharness.services.compact import (
    CompactResult,
    auto_compact_if_needed,
    estimate_message_tokens,
    full_compact,
    get_context_window,
    threshold_tokens,
    try_context_collapse,
    try_session_memory_compaction,
)
from openharness.services.extract import (
    EXTRACTION_SYSTEM_PROMPT,
    ExtractionResult,
    extract_memories_from_turn,
    has_memory_writes_since,
)
from openharness.services.focus_state import (
    FOCUS_STATE_SYSTEM_PROMPT,
    FocusState,
    infer_focus_state,
)
from openharness.services.session_memory import (
    get_session_memory_dir,
    read_session_memory,
    update_session_memory_file,
)
from openharness.services.snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    SnapshotCwdMismatch,
    SnapshotError,
    SnapshotMalformed,
    SnapshotNotFound,
    SnapshotVersionMismatch,
    get_snapshot_dir,
    load_snapshot,
    write_session_snapshot,
)
from openharness.services.summarize import summarize

__all__ = [
    "EXTRACTION_SYSTEM_PROMPT",
    "FOCUS_STATE_SYSTEM_PROMPT",
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_VERSION",
    "CompactResult",
    "ExtractionResult",
    "FocusState",
    "SnapshotCwdMismatch",
    "SnapshotError",
    "SnapshotMalformed",
    "SnapshotNotFound",
    "SnapshotVersionMismatch",
    "auto_compact_if_needed",
    "estimate_message_tokens",
    "extract_memories_from_turn",
    "full_compact",
    "get_context_window",
    "get_session_memory_dir",
    "get_snapshot_dir",
    "has_memory_writes_since",
    "infer_focus_state",
    "load_snapshot",
    "read_session_memory",
    "summarize",
    "threshold_tokens",
    "try_context_collapse",
    "try_session_memory_compaction",
    "update_session_memory_file",
    "write_session_snapshot",
]
