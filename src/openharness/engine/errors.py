"""Engine error hierarchy.

Per P3-T2.2b reparent (D13.4): :class:`LoopLimitExceeded` now subclasses
:class:`openharness.errors.LoopError` (cross-cutting taxonomy under the
``OpenHarnessError`` root). Phase 2 originally subclassed
``OpenHarnessApiError`` so the existing cli.py catch covered it (D10.2);
that was acknowledged technical debt — the loop is not an API-layer concern.

cli.py's catch-all changed from ``except OpenHarnessApiError`` to
``except OpenHarnessError`` in the same sub-unit so root-level coverage is
preserved. P3-T2.2d adds a dedicated ``except LoopError`` arm with a
loop-specific hint.
"""

from __future__ import annotations

from openharness.errors import LoopError


class LoopLimitExceeded(LoopError):
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


class AutonomousBoundaryError(LoopError):
    """Autonomous execution was requested without complete verified coverage."""
