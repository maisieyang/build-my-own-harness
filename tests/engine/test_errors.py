"""Tests for engine error types — P2-T4 sub-unit 4b + P3-T2.2b reparent.

Three contract properties matter:

1. ``LoopLimitExceeded`` carries the ``max_turns`` value so callers can
   render an actionable hint.
2. It descends from :class:`LoopError` (P3-T2.2b reparent) — Phase 2's
   ``OpenHarnessApiError`` parent was technical debt acknowledged in D10.2;
   the loop is not an API layer thing.
3. It still descends from :class:`OpenHarnessError` (transitively via
   ``LoopError``) so cli.py's root-level catch covers it.
"""

from __future__ import annotations

import pytest

from openharness.api.errors import OpenHarnessApiError
from openharness.engine.errors import LoopLimitExceeded
from openharness.errors import LoopError, OpenHarnessError


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

    def test_inherits_from_loop_error(self) -> None:
        # P3-T2.2b: reparent under LoopError instead of OpenHarnessApiError.
        # The loop is not an API-layer concern.
        assert issubclass(LoopLimitExceeded, LoopError)

    def test_inherits_from_root_transitively(self) -> None:
        # Via LoopError, LoopLimitExceeded is still under OpenHarnessError —
        # cli.py's root-level catch reaches it.
        assert issubclass(LoopLimitExceeded, OpenHarnessError)

    def test_no_longer_inherits_from_api_error(self) -> None:
        # Breaking change tripwire: any code that did
        # ``isinstance(loop_err, OpenHarnessApiError)`` to detect a loop
        # error must now use ``isinstance(loop_err, LoopError)``.
        assert not issubclass(LoopLimitExceeded, OpenHarnessApiError)

    def test_can_be_caught_via_loop_error_parent(self) -> None:
        with pytest.raises(LoopError):
            raise LoopLimitExceeded(max_turns=20)

    def test_can_be_caught_via_root(self) -> None:
        with pytest.raises(OpenHarnessError):
            raise LoopLimitExceeded(max_turns=20)
