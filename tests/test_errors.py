"""Tests for the OpenHarness exception hierarchy root — P3-T2 (D13.4).

The cross-cutting taxonomy lives in ``src/openharness/errors.py``:
:class:`OpenHarnessError` is the new root that ``OpenHarnessApiError`` /
``LoopError`` / ``ToolError`` / ``PermissionError`` / ``HookError`` all
descend from. Sub-unit 2a covers the root + the reparenting of
``OpenHarnessApiError``; the rest land in 2b-2e.

Each behavioral guarantee gets a dedicated assertion so the inheritance
chain breaks loudly when someone reorganizes blindly.
"""

from __future__ import annotations

import pytest

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)
from openharness.errors import OpenHarnessError


class TestOpenHarnessErrorRoot:
    def test_is_exception_subclass(self) -> None:
        # ``Exception`` (not BaseException) is the right root — KeyboardInterrupt
        # / SystemExit must propagate, not be caught by a project-wide handler.
        assert issubclass(OpenHarnessError, Exception)
        # Sanity: not a BaseException-direct-child (would catch KeyboardInterrupt).
        assert not issubclass(OpenHarnessError, KeyboardInterrupt)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(OpenHarnessError, match="root error"):
            raise OpenHarnessError("root error")

    def test_carries_message(self) -> None:
        err = OpenHarnessError("something went wrong")
        assert str(err) == "something went wrong"


class TestOpenHarnessApiErrorReparent:
    """``OpenHarnessApiError`` must now subclass ``OpenHarnessError``.

    Phase 2 had it directly subclassing ``Exception`` — the rename let
    ``LoopLimitExceeded`` and (future) ``ToolError`` / ``PermissionError`` /
    ``HookError`` join the same root without API-level coupling.
    """

    def test_api_error_is_subclass_of_root(self) -> None:
        assert issubclass(OpenHarnessApiError, OpenHarnessError)

    def test_api_error_instance_catchable_as_root(self) -> None:
        # Polymorphic catch: ``except OpenHarnessError`` covers ``OpenHarnessApiError``.
        with pytest.raises(OpenHarnessError):
            raise OpenHarnessApiError("api boom", status_code=500)

    def test_api_error_subclasses_still_carry_their_fields(self) -> None:
        # Reparenting must not break the existing 3 API errors' fields.
        auth = AuthenticationFailure("invalid key", status_code=401)
        assert auth.status_code == 401
        assert isinstance(auth, OpenHarnessError)

        rate = RateLimitFailure("slow down", status_code=429, retry_after=5.0)
        assert rate.status_code == 429
        assert rate.retry_after == 5.0
        assert isinstance(rate, OpenHarnessError)

        req = RequestFailure("bad request", status_code=400)
        assert req.status_code == 400
        assert isinstance(req, OpenHarnessError)

    def test_existing_api_path_still_works(self) -> None:
        # Backward-compat path (Phase 2 cli.py uses this; must keep working).
        from openharness.api import OpenHarnessApiError as ApiViaPackage

        assert ApiViaPackage is OpenHarnessApiError
