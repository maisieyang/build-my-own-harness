"""Shared fixtures for engine tests.

- ``_StubApiClient``: structurally satisfies the ``stream_message`` shape
  and emits a pre-recorded sequence of events per turn. Captures the
  ``ApiMessageRequest`` instances ``run_query`` builds so tests can assert
  on the loop's request shape.

Promoted to ``conftest.py`` (per the same D8.9 reasoning) so siblings share
without an extraction step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


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
