"""Shared fixtures for engine tests.

- ``_AllowAllChecker`` / ``_DenyChecker``: minimal :class:`PermissionChecker`
  satisfiers used by every ``run_query`` test (P2-T4.4d-4f) plus the
  ``QueryContext`` construction tests in 4c.
- ``_StubApiClient``: structurally satisfies the ``stream_message`` shape
  and emits a pre-recorded sequence of events per turn. Captures the
  ``ApiMessageRequest`` instances ``run_query`` builds so tests can assert
  on the loop's request shape.

Promoted to ``conftest.py`` (per the same D8.9 reasoning) so siblings share
without an extraction step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.permissions import DecisionResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic import BaseModel

    from openharness.protocols import ApiMessageRequest, ApiStreamEvent
    from openharness.tools.base import ToolExecutionContext


class _AllowAllChecker:
    """Permission checker that always returns ``DecisionResult.allow()``.

    Used by tests that exercise the happy-path tool-dispatch flow.
    Structurally satisfies :class:`PermissionChecker` -- no inheritance needed.
    P3-T3.3d:upgraded from bare ``Decision`` to ``DecisionResult``.
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DecisionResult:
        del tool_name, args, context
        return DecisionResult.allow()


class _DenyChecker:
    """Permission checker that always returns ``DecisionResult.deny(...)``.

    Used by tests that exercise the permission-denial recovery path.
    P3-T3.3d:upgraded from bare ``Decision`` to ``DecisionResult``.
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DecisionResult:
        del tool_name, args, context
        return DecisionResult.deny("stub denial for test")


class _StubApiClient:
    """Test stub: yields a pre-recorded event sequence per turn.

    Constructed with ``events_per_turn = [[turn1_events...], [turn2_events...]]``.
    Each call to :meth:`stream_message` consumes the next inner list. Captures
    every ``ApiMessageRequest`` ``run_query`` constructs in ``captured_requests``
    so tests can assert on the request shape (model, system, tools, messages).
    """

    def __init__(self, events_per_turn: list[list[ApiStreamEvent]]) -> None:
        self._events_per_turn = events_per_turn
        self._turn = 0
        self.captured_requests: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.captured_requests.append(request)
        if self._turn >= len(self._events_per_turn):
            raise AssertionError(
                f"_StubApiClient called for turn {self._turn + 1} but only "
                f"{len(self._events_per_turn)} turns were configured"
            )
        events = self._events_per_turn[self._turn]
        self._turn += 1
        for event in events:
            yield event
