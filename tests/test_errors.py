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


class TestLoopError:
    """``LoopError`` (P3-T2.2b) — control-flow layer of the agent loop.

    Concrete subclass: ``LoopLimitExceeded`` (in ``engine/errors.py``).
    Direct-raising of bare ``LoopError`` is allowed for now but rare —
    Phase 4 may add ``LoopCancelled`` / ``LoopBudgetExceeded`` siblings.
    """

    def test_is_subclass_of_root(self) -> None:
        from openharness.errors import LoopError

        assert issubclass(LoopError, OpenHarnessError)

    def test_can_be_raised_and_caught_at_root(self) -> None:
        from openharness.errors import LoopError

        with pytest.raises(OpenHarnessError):
            raise LoopError("loop misbehaved")

    def test_loop_error_is_not_an_api_error(self) -> None:
        # The whole point of the reparent: loop control-flow errors are
        # cross-cutting, not API-layer.
        from openharness.errors import LoopError

        assert not issubclass(LoopError, OpenHarnessApiError)


class TestToolError:
    """``ToolError`` (P3-T2.2c) — placeholder for P3-T3 tool dispatch errors.

    NOT for recoverable tool failures(those flow through
    :class:`ToolResult(is_error=True)` back to the LLM, per D8.5).
    ``ToolError`` is for *programming* errors:tool implementation crashed,
    dispatch wired wrong, registry produced an unexpected shape, etc.
    """

    def test_is_subclass_of_root(self) -> None:
        from openharness.errors import ToolError

        assert issubclass(ToolError, OpenHarnessError)

    def test_is_not_an_api_error(self) -> None:
        from openharness.errors import ToolError

        assert not issubclass(ToolError, OpenHarnessApiError)

    def test_can_be_raised_and_caught_at_root(self) -> None:
        from openharness.errors import ToolError

        with pytest.raises(OpenHarnessError):
            raise ToolError("tool dispatch crashed")


class TestPermissionError:
    """``PermissionError`` (P3-T2.2c) — placeholder for P3-T3 AuthZ subsystem
    errors.

    NOT for normal DENY decisions(those flow through ``ToolResult`` to the LLM
    so it can adapt). ``PermissionError`` is for *programming* errors in the
    decision engine:malformed config, contradictory rules, checker crashed.

    Caveat: Python has a builtin ``PermissionError`` (OS-level filesystem
    permission). Our class shadows it within ``openharness.errors``;
    callers always import by qualified name (``from openharness.errors import
    PermissionError``) so collisions are avoided.
    """

    def test_is_subclass_of_root(self) -> None:
        from openharness.errors import PermissionError as OhPermissionError

        assert issubclass(OhPermissionError, OpenHarnessError)

    def test_is_distinct_from_python_builtin(self) -> None:
        # Documents the intentional shadow: our class lives in
        # ``openharness.errors``, the builtin lives in ``builtins``.
        # Module identity is a more meaningful check than ``is not`` (which
        # mypy short-circuits as trivially true given the type info).
        import builtins

        from openharness.errors import PermissionError as OhPermissionError

        assert OhPermissionError.__module__ == "openharness.errors"
        assert builtins.PermissionError.__module__ == "builtins"

    def test_can_be_raised_and_caught_at_root(self) -> None:
        from openharness.errors import PermissionError as OhPermissionError

        with pytest.raises(OpenHarnessError):
            raise OhPermissionError("permission checker misconfigured")


class TestHookError:
    """``HookError`` (P3-T2.2c) — placeholder for P3-T4 middleware/hook crashes.

    NOT for hooks returning ``HookResult(decision="deny")`` (that's the
    documented user-extension path). ``HookError`` fires when a registered
    hook itself raises an unexpected exception during dispatch — the
    framework wraps and re-raises so the cli ``OnError`` chain (P3-T4) can
    surface it.
    """

    def test_is_subclass_of_root(self) -> None:
        from openharness.errors import HookError

        assert issubclass(HookError, OpenHarnessError)

    def test_is_not_an_api_error(self) -> None:
        from openharness.errors import HookError

        assert not issubclass(HookError, OpenHarnessApiError)

    def test_can_be_raised_and_caught_at_root(self) -> None:
        from openharness.errors import HookError

        with pytest.raises(OpenHarnessError):
            raise HookError("hook X crashed during PreToolUse")
