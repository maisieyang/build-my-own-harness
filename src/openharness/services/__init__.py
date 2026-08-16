"""Services subsystem — stateful LLM-orchestration workflows.

**Separated from :mod:`openharness.compaction`** (per-result token counting and
ingress truncation) by intent: ``compaction/`` budgets each new Tool Result;
``services/`` semantically compacts accumulated Conversation history.

Per ``decisions/26-phase-11-boundary.md`` D29.1: one shared
:func:`summarize` primitive feeding downstream consumers — full
compact L4, the ``/compact`` slash command, and (until Phase 17
D37.3 retired it) the Phase 11 ``extract_memories_from_turn``
secondary pass. Memory writes are now the main LLM's responsibility
inline via Write + Edit tools per D36.10/D36.11.

Public modules:

- :mod:`.summarize` — shared LLM summarization primitive
- :mod:`.compact` — thresholding, Tool Result cleanup, and LLM compaction
- :mod:`.snapshot` — full session snapshot writer + reader
- :mod:`.focus_state` — Phase 13 secondary pass for task focus state
"""

from __future__ import annotations

from openharness.services.compact import (
    CompactResult,
    FullCompactError,
    auto_compact_if_needed,
    compact_for_request_budget,
    estimate_message_tokens,
    estimate_request_input_tokens,
    full_compact,
    get_context_window,
    request_input_token_budget,
    threshold_tokens,
)
from openharness.services.focus_state import (
    FOCUS_STATE_SYSTEM_PROMPT,
    FocusState,
    infer_focus_state,
)
from openharness.services.snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    SnapshotClearError,
    SnapshotCwdMismatch,
    SnapshotError,
    SnapshotMalformed,
    SnapshotNotFound,
    SnapshotVersionMismatch,
    append_messages_to_snapshot,
    clear_conversation_snapshot,
    get_snapshot_dir,
    load_snapshot,
    update_permission_runtime_snapshot,
    write_session_snapshot,
)
from openharness.services.summarize import summarize

__all__ = [
    "FOCUS_STATE_SYSTEM_PROMPT",
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_VERSION",
    "CompactResult",
    "FocusState",
    "FullCompactError",
    "SnapshotClearError",
    "SnapshotCwdMismatch",
    "SnapshotError",
    "SnapshotMalformed",
    "SnapshotNotFound",
    "SnapshotVersionMismatch",
    "append_messages_to_snapshot",
    "auto_compact_if_needed",
    "clear_conversation_snapshot",
    "compact_for_request_budget",
    "estimate_message_tokens",
    "estimate_request_input_tokens",
    "full_compact",
    "get_context_window",
    "get_snapshot_dir",
    "infer_focus_state",
    "load_snapshot",
    "request_input_token_budget",
    "summarize",
    "threshold_tokens",
    "update_permission_runtime_snapshot",
    "write_session_snapshot",
]
