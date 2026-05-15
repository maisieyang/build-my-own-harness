"""Compaction subsystem — P4 (context management).

Two-layer defense per ``decisions/10-phase-4-boundary.md``:

- **Layer 1** (proactive, per-tool-result):
  :class:`TruncateToolResultHook` cuts oversized ``tool_result`` outputs
  head+tail with a middle marker. PostToolUse hook (P4-T2, not yet).
- **Layer 2** (reactive, per-prompt-too-long error):
  ``engine/query.py`` catches :class:`PromptTooLongFailure`, drops the
  oldest ``tool_use``/``tool_result`` pair from messages, retries up to
  3 times per turn (P4-T3, not yet).

Sub-units land incrementally:

- 1a (this commit):``count_tokens`` — tiktoken with byte-ratio fallback
- 2 (next):``head_tail_truncate`` + ``TruncateToolResultHook``
- 3:``PromptTooLongFailure`` + engine reactive handling
- 4:CLI / Settings + end-to-end smoke
- 5:coverage + retro

Public API:

    from openharness.compaction import count_tokens
"""

from __future__ import annotations

from openharness.compaction.hook import TruncateToolResultHook
from openharness.compaction.tokenize import count_tokens
from openharness.compaction.truncate import head_tail_truncate

__all__ = [
    "TruncateToolResultHook",
    "count_tokens",
    "head_tail_truncate",
]
