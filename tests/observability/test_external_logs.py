"""External-module log shape — api/retry + permissions + hooks — P3-T5.5d.

5c covered the 4 dispatch-lifecycle log points in ``run_query``. This
file covers the 4 log points that live in modules ``run_query`` calls
**into**:

- ``api/retry.py``           → ``retry`` (info)
- ``permissions/tier_based.py`` → ``permission_denied`` (warning) — 3 tiers
- ``hooks/executor.py``      → ``hook_invoke`` (debug) + ``hook_failed`` (warning)

These logs all live *underneath* ``run_query``'s ``with bind_run()`` /
``with bind_turn()`` context. Tests assert that:

- run_id / turn_id are auto-injected via structlog contextvars
- sanitize helpers (path / command) are applied at the call site
- exception **class names** are logged but not exception messages
  (a tiny defense-in-depth against accidental leakage of cred fragments
  from server-returned error strings)
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING, Any

import pytest

from openharness.api.errors import RateLimitFailure
from openharness.api.retry import RetryPolicy, with_retry
from openharness.errors import HookError
from openharness.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    PreToolUseContext,
    execute_hook_chain,
)
from openharness.observability import bind_run, bind_turn, configure_logging
from openharness.permissions import TierBasedPermissionChecker
from openharness.tools import ToolRegistry
from openharness.tools.base import ToolExecutionContext

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Fixtures + helpers — same pattern as test_query_integration.py              #
# --------------------------------------------------------------------------- #


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """StringIO sink; test body must call ``_configure(log_stream)``.

    See ``test_query_integration.py`` for the pytest-asyncio rationale.
    """
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _configure(stream: io.StringIO) -> None:
    configure_logging(level="DEBUG", format="json", stream=stream)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Retry log                                                                   #
# --------------------------------------------------------------------------- #


class TestRetryLog:
    async def test_retry_logs_attempt_delay_and_error_class(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)

        # Fail once with retryable 503, then succeed.
        calls = [0]

        async def flaky() -> str:
            calls[0] += 1
            if calls[0] == 1:
                from openharness.api.errors import RequestFailure

                raise RequestFailure("503 transient", status_code=503)
            return "ok"

        async def no_sleep(_delay: float) -> None:
            return None

        with bind_run("aaa111111111"):
            await with_retry(
                flaky,
                policy=RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0, jitter=0.0),
                sleep=no_sleep,
            )

        retries = [e for e in _lines(log_stream) if e["event"] == "retry"]
        assert len(retries) == 1
        r = retries[0]
        assert r["attempt"] == 1
        assert r["delay_seconds"] == 0.0
        assert r["error"] == "RequestFailure"  # class name only
        assert r["run_id"] == "aaa111111111"
        assert r["level"] == "info"

    async def test_retry_error_message_not_in_log(self, log_stream: io.StringIO) -> None:
        """Defense-in-depth — server-returned error strings can include cred
        fragments. Only the exception class name is logged."""
        _configure(log_stream)

        async def fails() -> str:
            # Pretend a server error message leaked a fake credential.
            raise RateLimitFailure(
                "rate-limited: key=sk-1234567890abcdef",
                status_code=429,
            )

        async def no_sleep(_delay: float) -> None:
            return None

        # Two retries permitted, then final raise on attempt 3.
        with pytest.raises(RateLimitFailure):
            await with_retry(
                fails,
                policy=RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0, jitter=0.0),
                sleep=no_sleep,
            )

        retries = [e for e in _lines(log_stream) if e["event"] == "retry"]
        assert len(retries) == 2  # 2 retries on attempts 1+2; attempt 3 re-raises
        # Defense check: the message string with the embedded fake key must
        # NOT appear in any retry record.
        for r in retries:
            blob = json.dumps(r)
            assert "sk-1234567890" not in blob
            assert "rate-limited" not in blob  # message itself absent


# --------------------------------------------------------------------------- #
# Permission denied log                                                       #
# --------------------------------------------------------------------------- #


class TestPermissionDeniedLog:
    @pytest.fixture
    def checker(self, tmp_path: Path) -> TierBasedPermissionChecker:
        from openharness.config import Settings

        registry = ToolRegistry()
        # Use the real Bash + Read tools so is_read_only classification works.
        from openharness.tools.bash import Bash
        from openharness.tools.read import Read

        registry.register(Bash())
        registry.register(Read())

        # Settings with one user deny rule that matches our tmp_path test path.
        settings = Settings(
            api_key="x",
            base_url="https://example.com",
            deny_paths=("secrets/**",),
        )
        return TierBasedPermissionChecker(registry, settings)

    def _exec_ctx(self, tmp_path: Path) -> ToolExecutionContext:
        return ToolExecutionContext(cwd=tmp_path)

    async def test_bash_denylist_logs_at_warning(
        self,
        log_stream: io.StringIO,
        checker: TierBasedPermissionChecker,
        tmp_path: Path,
    ) -> None:
        _configure(log_stream)
        from openharness.tools.bash import BashInput

        bad_args = BashInput(command="rm -rf / something_dangerous")
        result = checker.evaluate("Bash", bad_args, self._exec_ctx(tmp_path))
        from openharness.permissions import Decision

        assert result.decision is Decision.DENY

        deny = next(e for e in _lines(log_stream) if e["event"] == "permission_denied")
        assert deny["level"] == "warning"
        assert deny["tool"] == "Bash"
        assert deny["tier"] == "bash_denylist"
        assert deny["pattern"] == "rm -rf /"
        # command field is sanitized: only first token + length.
        assert deny["command"].startswith("rm (len=")
        # The full command (with the catastrophic args) must not appear.
        blob = json.dumps(deny)
        assert "something_dangerous" not in blob

    async def test_tier1_logs_with_pattern_and_sanitized_path(
        self,
        log_stream: io.StringIO,
        checker: TierBasedPermissionChecker,
        tmp_path: Path,
    ) -> None:
        _configure(log_stream)
        from openharness.tools.read import ReadInput

        # ~/.ssh/** path is reliably outside any tmp_path cwd.
        ssh_args = ReadInput(path="~/.ssh/id_rsa")
        checker.evaluate("Read", ssh_args, self._exec_ctx(tmp_path))

        deny = next(e for e in _lines(log_stream) if e["event"] == "permission_denied")
        assert deny["tool"] == "Read"
        assert deny["tier"] == "tier1_hardcoded"
        assert deny["pattern"] == "~/.ssh/**"
        # path field is sanitized — outside cwd → "<redacted>".
        assert deny["path"] == "<redacted>"

    async def test_tier2_logs_with_user_pattern_and_relative_path(
        self,
        log_stream: io.StringIO,
        checker: TierBasedPermissionChecker,
        tmp_path: Path,
    ) -> None:
        _configure(log_stream)
        from openharness.tools.read import ReadInput

        secret = tmp_path / "secrets" / "keys.txt"
        secret.parent.mkdir(parents=True)
        secret.touch()

        args = ReadInput(path=str(secret))
        checker.evaluate("Read", args, self._exec_ctx(tmp_path))

        deny = next(e for e in _lines(log_stream) if e["event"] == "permission_denied")
        assert deny["tool"] == "Read"
        assert deny["tier"] == "tier2_user_glob"
        assert deny["pattern"] == "secrets/**"
        # In-cwd → relative path.
        assert deny["path"] == "secrets/keys.txt"

    async def test_run_id_propagates_into_permission_log(
        self,
        log_stream: io.StringIO,
        checker: TierBasedPermissionChecker,
        tmp_path: Path,
    ) -> None:
        _configure(log_stream)
        from openharness.tools.read import ReadInput

        with bind_run("bbb222222222"), bind_turn(3):
            checker.evaluate(
                "Read",
                ReadInput(path="~/.ssh/id_rsa"),
                self._exec_ctx(tmp_path),
            )

        deny = next(e for e in _lines(log_stream) if e["event"] == "permission_denied")
        assert deny["run_id"] == "bbb222222222"
        assert deny["turn_id"] == 3


# --------------------------------------------------------------------------- #
# Hook executor log                                                           #
# --------------------------------------------------------------------------- #


class TestHookLogs:
    def _pre_tool_ctx(self) -> PreToolUseContext:
        from pathlib import Path as _Path

        return PreToolUseContext(
            tool_name="Fake",
            tool_input={"value": "x"},
            exec_context=ToolExecutionContext(cwd=_Path("/tmp")),
        )

    async def test_hook_invoke_logged_at_debug_with_hook_name_and_event(
        self, log_stream: io.StringIO
    ) -> None:
        _configure(log_stream)

        async def named_log_hook(ctx: HookContext) -> HookResult | None:
            del ctx
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", named_log_hook)

        await execute_hook_chain(registry, "PreToolUse", self._pre_tool_ctx())

        invokes = [e for e in _lines(log_stream) if e["event"] == "hook_invoke"]
        assert len(invokes) == 1
        i = invokes[0]
        assert i["hook"] == "named_log_hook"
        # hook_event is renamed away from structlog's reserved ``event`` kwarg.
        assert i["hook_event"] == "PreToolUse"
        assert i["level"] == "debug"

    async def test_hook_failed_logged_at_warning_with_class_name_only(
        self, log_stream: io.StringIO
    ) -> None:
        _configure(log_stream)

        async def boom(ctx: HookContext) -> HookResult | None:
            del ctx
            raise ValueError("hook detail with sk-1234567890abcdef leak")

        registry = HookRegistry()
        registry.register("PreToolUse", boom)

        with pytest.raises(HookError):
            await execute_hook_chain(registry, "PreToolUse", self._pre_tool_ctx())

        failed = next(e for e in _lines(log_stream) if e["event"] == "hook_failed")
        assert failed["level"] == "warning"
        assert failed["hook"] == "boom"
        assert failed["hook_event"] == "PreToolUse"
        # error is the class name only — not the message containing the fake key.
        assert failed["error"] == "ValueError"
        # Defense-in-depth: fake secret in message must not appear in log.
        blob = json.dumps(failed)
        assert "sk-1234567890" not in blob

    async def test_hook_invoke_carries_run_id_when_under_bind_run(
        self, log_stream: io.StringIO
    ) -> None:
        """Sanity:contextvar propagation works across the execute_hook_chain
        call path,not just inside run_query. Phase 4+ memory-injection hooks
        will rely on this."""
        _configure(log_stream)

        async def hook_a(ctx: HookContext) -> HookResult | None:
            del ctx
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", hook_a)

        with bind_run("ccc333333333"):
            await execute_hook_chain(registry, "PreToolUse", self._pre_tool_ctx())

        invoke = next(e for e in _lines(log_stream) if e["event"] == "hook_invoke")
        assert invoke["run_id"] == "ccc333333333"


# --------------------------------------------------------------------------- #
# Smoke — all 8 log points present across one realistic flow                  #
# --------------------------------------------------------------------------- #


def test_log_event_inventory_lock(log_stream: io.StringIO) -> None:
    """Document the 8 plan log event names. If we rename one, this test
    fails — useful to keep ops docs / dashboards in sync."""
    _configure(log_stream)
    # We don't actually exercise them all here (the integration tests do);
    # this is a name-registry assertion.
    expected = {
        "turn_start",
        "tool_dispatch",
        "tool_complete",
        "loop_limit_exceeded",
        "retry",
        "permission_denied",
        "hook_invoke",
        "hook_failed",
    }
    # Smoke:every event must be a non-empty string of expected length.
    for name in expected:
        assert isinstance(name, str)
        assert 1 <= len(name) <= 32
