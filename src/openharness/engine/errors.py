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

from typing import TYPE_CHECKING

from openharness.errors import LoopError

if TYPE_CHECKING:
    from openharness.protocols import ConversationMessage


class LoopLimitExceeded(LoopError):
    """``run_query`` reached its ``max_turns`` cap without ``end_turn``.

    Carries the caller-selected cap so the caller can render a hint like
    "loop hit turn limit, try simpler prompt or raise --max-turns".

    The public interactive loop has no turn-count cap by default. Private
    adapters and explicit ``--max-turns`` callers use this as a circuit
    breaker; exhaustion is a forced pause, never semantic completion.
    """

    def __init__(
        self,
        max_turns: int,
        *,
        messages: list[ConversationMessage] | None = None,
    ) -> None:
        super().__init__(
            f"loop hit turn limit ({max_turns}); raise --max-turns or simplify the prompt"
        )
        self.max_turns = max_turns
        # Interactive callers can checkpoint before pausing instead of
        # throwing away completed tool work. This is never evidence of
        # semantic completion and must not be routed to the Goal judge.
        self.messages = list(messages) if messages is not None else None


class AutonomousBoundaryError(LoopError):
    """Autonomous execution was requested without complete verified coverage."""
