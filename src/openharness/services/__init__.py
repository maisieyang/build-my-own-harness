"""Services subsystem — stateful LLM-orchestration workflows.

**Separated from :mod:`openharness.compaction`** (deterministic
byte-level primitives: token counting, head/tail truncate,
microcompact hook) by intent: ``compaction/`` shrinks bytes;
``services/`` calls the LLM to "凝结认知" (condense cognition).

Per ``decisions/26-phase-11-boundary.md`` D29.1: one shared
:func:`summarize` primitive feeding downstream consumers — full
compact L4, the ``/compact`` slash command, and (until Phase 17
D37.3 retired it) the Phase 11 ``extract_memories_from_turn``
secondary pass. Memory writes are now the main LLM's responsibility
inline via Write + Edit tools per D36.10/D36.11.

Plus the deterministic checkpoint writer
(:mod:`openharness.services.session_memory`) that L4's escalation
predecessor (L3) reads to avoid the LLM call entirely when a fresh
checkpoint exists.

Public modules:

- :mod:`.summarize` — shared LLM summarization primitive
- :mod:`.compact` — L2/L3/L4 escalation
- :mod:`.session_memory` — deterministic per-turn checkpoint
- :mod:`.snapshot` — full session snapshot writer + reader
- :mod:`.focus_state` — Phase 13 secondary pass for task focus state
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
    "FOCUS_STATE_SYSTEM_PROMPT",
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_VERSION",
    "CompactResult",
    "FocusState",
    "SnapshotCwdMismatch",
    "SnapshotError",
    "SnapshotMalformed",
    "SnapshotNotFound",
    "SnapshotVersionMismatch",
    "auto_compact_if_needed",
    "estimate_message_tokens",
    "full_compact",
    "get_context_window",
    "get_session_memory_dir",
    "get_snapshot_dir",
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
