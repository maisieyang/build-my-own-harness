"""Engine error hierarchy.

Per D10.2 (P2-T4 Three-Axis): :class:`LoopLimitExceeded` subclasses
:class:`OpenHarnessApiError` so the existing CLI ``except OpenHarnessApiError``
catch covers it without changes. The naming mismatch ("API" + "loop limit") is
acknowledged technical debt -- Phase 3 will introduce hooks errors and at
that point a base ``OpenHarnessError`` rename refactor is the right move.
"""

from __future__ import annotations

from openharness.api.errors import OpenHarnessApiError


class LoopLimitExceeded(OpenHarnessApiError):
    """``run_query`` reached its ``max_turns`` cap without ``end_turn``.

    Carries the loop's hard cap so the caller can render a hint like
    "loop hit turn limit, try simpler prompt or raise --max-turns".

    Per ``decisions/06-phase-2-boundary.md`` D6.1, this is the **safety
    floor** -- not a normal exit path. Phase 4 may add cost-cap on top
    but the turn counter remains load-bearing.
    """

    def __init__(self, max_turns: int) -> None:
        super().__init__(
            f"loop hit turn limit ({max_turns}); raise --max-turns or simplify the prompt"
        )
        self.max_turns = max_turns
