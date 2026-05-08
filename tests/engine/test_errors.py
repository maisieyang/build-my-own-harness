"""Tests for engine error types — P2-T4 sub-unit 4b.

Two contract properties matter:

1. ``LoopLimitExceeded`` carries the ``max_turns`` value so callers can
   render an actionable hint.
2. It descends from :class:`OpenHarnessApiError` so the existing CLI catch
   block reaches it without modification (D10.2).
"""

from __future__ import annotations

import pytest

from openharness.api.errors import OpenHarnessApiError
from openharness.engine.errors import LoopLimitExceeded


class TestLoopLimitExceeded:
    def test_carries_max_turns(self) -> None:
        exc = LoopLimitExceeded(max_turns=20)
        assert exc.max_turns == 20

    def test_message_mentions_limit_and_remediation(self) -> None:
        exc = LoopLimitExceeded(max_turns=5)
        message = str(exc)
        assert "5" in message
        # Hint should point at the user's lever for raising the cap.
        assert "max-turns" in message

    def test_inherits_from_open_harness_api_error(self) -> None:
        # D10.2: existing CLI ``except OpenHarnessApiError`` covers this.
        assert issubclass(LoopLimitExceeded, OpenHarnessApiError)

    def test_can_be_caught_via_parent(self) -> None:
        with pytest.raises(OpenHarnessApiError):
            raise LoopLimitExceeded(max_turns=20)
