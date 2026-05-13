"""Hook lifecycle events — P3-T4.4a foundation type.

The 5-event taxonomy is locked by ``decisions/08`` D13.1 + P3-T4 Three-Axis:

- ``PreToolUse``  — before tool dispatch (can modify args or deny)
- ``PostToolUse`` — after tool dispatch (can modify result)
- ``PreApiCall``  — before LLM API call (can modify request or deny)
- ``PostApiCall`` — after LLM API call (mostly observe;modify in P4+)
- ``OnError``     — exception path (observe only;suppress in P4+)

Per-event field semantics + how each event consumes ``HookResult`` is
documented in ``hooks/result.py``;the executor (4d) honors only the
relevant fields per event.

``HookContext`` dataclasses (5 per-event types) + the ``Hook`` callable
type alias land in 4b — this module ships only the ``HookEvent`` Literal.
"""

from __future__ import annotations

from typing import Literal

# Closed 5-value Literal. Adding / removing a value:
# 1. Update boundary decision (decisions/08 D13.1)
# 2. Update Three-Axis F per-event semantics table
# 3. Update tests in tests/hooks/test_result.py::TestHookEventLiteral
HookEvent = Literal[
    "PreToolUse",
    "PostToolUse",
    "PreApiCall",
    "PostApiCall",
    "OnError",
]
