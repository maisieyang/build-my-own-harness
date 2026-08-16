"""Deterministic Tool Result ingress budgeting.

This package limits each new Tool Result with head/tail truncation and an
explicit loss marker. Whole-request budgeting, old-result clearing, semantic
Summary, and the one-shot Prompt Too Long recompilation live in
``openharness.services.compact`` and ``openharness.engine.query``.

Public API:

    from openharness.compaction import count_tokens, head_tail_truncate
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
