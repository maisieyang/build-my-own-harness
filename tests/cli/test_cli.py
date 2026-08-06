"""Tests for the ``oh`` CLI surface (mocked client).

These tests exercise the full command path -- argument parsing, settings
loading, request construction, streaming, and error UX -- without ever
opening a network socket. The client is replaced via
:func:`monkeypatch.setattr` on :mod:`openharness.cli`'s ``_build_client``
seam, so the real ``OpenAICompatibleApiClient`` constructor (and the
underlying ``AsyncOpenAI``) never run.

Two assertion surfaces:

1. **CliRunner.exit_code / stdout / stderr** -- end-to-end user-visible
   behavior.
2. **The recorded request** captured by the stub client -- so we can
   verify ``--model`` overrides land in :class:`ApiMessageRequest` even
   when the response itself is trivial.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.api.errors import (
    AuthenticationFailure,
    RateLimitFailure,
    RequestFailure,
)
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# --------------------------------------------------------------------------- #
# Stub clients                                                                #
# --------------------------------------------------------------------------- #


class _RecordingStubClient:
    """Stand-in for :class:`OpenAICompatibleApiClient` that records the
    request it was handed and yields a pre-canned event sequence.

    Construction is lazy -- callers pass the events they want emitted; the
    captured ``last_request`` is exposed for assertions.
    """

    def __init__(self, events: list[ApiStreamEvent]) -> None:
        self._events = events
        self.last_request: ApiMessageRequest | None = None

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        for event in self._events:
            yield event


class _RaisingStubClient:
    """Stub whose ``stream_message`` raises a pre-set exception on
    iteration. Used to drive the differentiated error-UX paths."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        # The ``yield`` keeps mypy happy -- this is an async generator.
        # We raise before the first yield so the renderer never sees an
        # event, matching the "establish-stream failure" code path.
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]
        raise self._exc


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _hello_world_events(text: str = "hello") -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            stop_reason="end_turn",
        ),
    ]


def _incomplete_events(text: str = "partial") -> list[ApiStreamEvent]:
    """Events whose terminal stop_reason is NOT ``end_turn`` (output truncated
    by the model's token cap). The run raises no exception — it just didn't
    finish cleanly."""
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            stop_reason="max_tokens",
        ),
    ]


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


class TestHappyPath:
    """A configured shell + a working client streams text to stdout."""

    def test_streams_text_to_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("hi from stub"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "say hi"])

        assert result.exit_code == 0, result.stderr
        assert "hi from stub" in result.stdout
        assert stub.last_request is not None
        assert stub.last_request.messages[0].role == "user"
        assert stub.last_request.messages[0].content[0].text == "say hi"  # type: ignore[union-attr]

    def test_uses_settings_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 0
        assert stub.last_request is not None
        assert stub.last_request.model == "qwen-plus"

    def test_env_model_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MODEL", "qwen-max")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 0
        assert stub.last_request is not None
        assert stub.last_request.model == "qwen-max"

    def test_cli_model_flag_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MODEL", "qwen-max")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--model", "qwen-turbo"])

        assert result.exit_code == 0
        assert stub.last_request is not None
        assert stub.last_request.model == "qwen-turbo"

    def test_max_tokens_flag_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--max-tokens", "256"])

        assert result.exit_code == 0
        assert stub.last_request is not None
        assert stub.last_request.max_tokens == 256


# --------------------------------------------------------------------------- #
# Differentiated error UX (D5.6)                                              #
# --------------------------------------------------------------------------- #


class TestErrorUX:
    """Each error type maps to a one-line hint in stderr; exit code 1."""

    def test_missing_api_key_prints_config_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Autouse fixture clears env; do not re-populate.
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 1
        assert "Configuration error" in result.stderr
        assert "OPENHARNESS_API_KEY" in result.stderr
        # No Python traceback should leak in the default mode.
        assert "Traceback" not in result.stderr

    def test_authentication_failure_hints_at_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RaisingStubClient(AuthenticationFailure("invalid api key", status_code=401))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 1
        assert "Authentication failed" in result.stderr
        assert "401" in result.stderr
        assert "OPENHARNESS_API_KEY" in result.stderr

    def test_rate_limit_failure_hints_at_quota(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RaisingStubClient(RateLimitFailure("rate limited", status_code=429))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 1
        assert "Rate-limited" in result.stderr
        assert "429" in result.stderr

    def test_request_failure_shows_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RaisingStubClient(RequestFailure("internal server error", status_code=500))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 1
        assert "Request failed" in result.stderr
        assert "500" in result.stderr

    def test_loop_limit_exceeded_uses_loop_specific_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P3-T2.2d: ``LoopLimitExceeded`` (a ``LoopError`` subclass) must be
        # caught by the dedicated ``except LoopError`` arm, not the
        # OpenHarnessError catch-all. User sees "Loop error:" prefix instead
        # of generic "Error:". The message itself already names ``--max-turns``
        # as the remediation (no separate Hint line needed).
        from openharness.engine.errors import LoopLimitExceeded

        _set_minimum_env(monkeypatch)
        stub = _RaisingStubClient(LoopLimitExceeded(max_turns=20))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 1
        # Prefix that signals the category — distinct from generic "Error:".
        assert "Loop error" in result.stderr
        # Embedded message + remediation hint preserved.
        assert "20" in result.stderr
        assert "--max-turns" in result.stderr
        # No Python traceback should leak.
        assert "Traceback" not in result.stderr


# --------------------------------------------------------------------------- #
# Argument validation                                                         #
# --------------------------------------------------------------------------- #


class TestArgumentValidation:
    """Typer rejects malformed invocations before our code runs."""

    def test_missing_prompt_exits_nonzero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask"])
        # Click signals "missing argument" with exit code 2.
        assert result.exit_code == 2

    def test_invalid_max_tokens_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--max-tokens", "0"])
        # min=1 → Click reports "Invalid value".
        assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# Permission flags (P2-T6.6e)                                                 #
# --------------------------------------------------------------------------- #


class _CapturedContext:
    """Holds the QueryContext that ``cli._run_ask`` constructs.

    ``cli.run_query`` is monkeypatched to a function that records its
    ``context`` argument here, then yields a single end_turn event so
    ``render_stream`` completes cleanly. This is the only way to verify
    flag → permission_mode propagation without inspecting Typer internals.
    """

    def __init__(self) -> None:
        self.context: object | None = None


def _patch_run_query_capture(monkeypatch: pytest.MonkeyPatch, captured: _CapturedContext) -> None:
    async def _capturing_run_query(
        initial_messages: list[ConversationMessage],
        context: object,
    ) -> AsyncIterator[ApiStreamEvent]:
        del initial_messages
        captured.context = context
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)


class TestPermissionFlags:
    """``--auto`` / ``--dry-run`` thread permission_mode into QueryContext."""

    def test_default_mode_when_no_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.permissions import PermissionMode

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 0
        assert captured.context is not None
        assert captured.context.permission_mode is PermissionMode.DEFAULT  # type: ignore[attr-defined]

    def test_dry_run_flag_sets_dry_run_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.permissions import PermissionMode

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--dry-run"])

        assert result.exit_code == 0
        assert captured.context.permission_mode is PermissionMode.DRY_RUN  # type: ignore[attr-defined]

    def test_auto_flag_sets_auto_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.permissions import PermissionMode

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--auto"])

        assert result.exit_code == 0
        assert captured.context.permission_mode is PermissionMode.AUTO  # type: ignore[attr-defined]

    def test_auto_and_dry_run_mutually_exclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--auto", "--dry-run"])

        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr

    def test_env_var_permission_mode_propagates_when_no_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openharness.permissions import PermissionMode

        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_PERMISSION_MODE", "dry_run")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 0
        # Env var takes effect even without --dry-run flag.
        assert captured.context.permission_mode is PermissionMode.DRY_RUN  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Logging flags (P3-T5.5e)                                                    #
# --------------------------------------------------------------------------- #


class _CapturedLoggingConfig:
    """Records the (level, format) configure_logging was invoked with."""

    def __init__(self) -> None:
        self.level: str | None = None
        self.format: str | None = None


def _patch_configure_logging(
    monkeypatch: pytest.MonkeyPatch, captured: _CapturedLoggingConfig
) -> None:
    """Replace ``cli.configure_logging`` with a spy. The original would also
    work (it goes to stderr, no side-effects we care about), but the spy
    lets us assert on level/format propagation without parsing stderr."""

    def _spy(*, level: str, format: str, stream: object = None) -> None:
        del stream
        captured.level = level
        captured.format = format

    monkeypatch.setattr(cli_module, "configure_logging", _spy)


class TestLoggingFlags:
    """``--log-level`` / ``--log-format`` propagate from CLI → settings → configure_logging."""

    def test_default_level_and_format_when_no_flags_no_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedLoggingConfig()
        _patch_configure_logging(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 0
        assert captured.level == "WARNING"
        assert captured.format == "console"

    def test_env_var_log_level_propagates_when_no_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_LOG_LEVEL", "INFO")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedLoggingConfig()
        _patch_configure_logging(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])

        assert result.exit_code == 0
        assert captured.level == "INFO"
        assert captured.format == "console"  # format env not set → default

    def test_cli_log_level_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_LOG_LEVEL", "INFO")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedLoggingConfig()
        _patch_configure_logging(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--log-level", "DEBUG"])

        assert result.exit_code == 0
        assert captured.level == "DEBUG"

    def test_log_format_json_flag_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedLoggingConfig()
        _patch_configure_logging(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--log-format", "json"])

        assert result.exit_code == 0
        assert captured.format == "json"

    def test_invalid_log_level_rejected_by_typer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--log-level", "TRACE"])

        # Typer's Literal handling rejects unknown choices with exit 2
        # (Click's standard "Usage error" code).
        assert result.exit_code == 2

    def test_invalid_log_format_rejected_by_typer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--log-format", "xml"])

        assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# Compaction flags (P4-T4.4a/4b)                                              #
# --------------------------------------------------------------------------- #


class TestCompactionFlags:
    """``--tool-result-cap`` / ``--no-auto-truncate`` thread through to the
    QueryContext's hook_registry — verifying the Layer 1 default-registration
    path."""

    def test_default_registers_truncate_hook_on_post_tool_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openharness.compaction import TruncateToolResultHook

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        # Default: TruncateToolResultHook registered on PostToolUse.
        ctx = captured.context
        hooks = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        assert len(hooks) == 1
        assert isinstance(hooks[0], TruncateToolResultHook)

    def test_no_auto_truncate_flag_skips_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--no-auto-truncate"])
        assert result.exit_code == 0

        ctx = captured.context
        hooks = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        assert hooks == []  # no auto-registration

    def test_env_var_auto_truncate_false_skips_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # OPENHARNESS_AUTO_TRUNCATE=false should have the same effect as
        # --no-auto-truncate.
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_AUTO_TRUNCATE", "false")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        hooks = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        assert hooks == []

    def test_tool_result_cap_zero_skips_registration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # cap=0 is the documented "disabled" sentinel — no hook registered.
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--tool-result-cap", "0"])
        assert result.exit_code == 0

        ctx = captured.context
        hooks = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        assert hooks == []

    def test_tool_result_cap_propagates_to_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.compaction import TruncateToolResultHook

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--tool-result-cap", "500"])
        assert result.exit_code == 0

        ctx = captured.context
        hooks = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        assert len(hooks) == 1
        hook = hooks[0]
        assert isinstance(hook, TruncateToolResultHook)
        # private but stable for tests: cap_tokens carries the override.
        assert hook._cap_tokens == 500

    def test_env_var_tool_result_cap_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.compaction import TruncateToolResultHook

        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_TOOL_RESULT_CAP", "3000")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        hooks = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        hook = hooks[0]
        assert isinstance(hook, TruncateToolResultHook)
        assert hook._cap_tokens == 3000

    def test_cli_cap_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.compaction import TruncateToolResultHook

        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_TOOL_RESULT_CAP", "3000")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--tool-result-cap", "777"])
        assert result.exit_code == 0

        ctx = captured.context
        hooks = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        hook = hooks[0]
        assert isinstance(hook, TruncateToolResultHook)
        assert hook._cap_tokens == 777

    def test_negative_cap_rejected_by_typer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--tool-result-cap", "-5"])
        assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# Skills bootstrap (P5c-T3)                                                   #
# --------------------------------------------------------------------------- #


def _isolate_skills_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect both global and project skill dirs into ``tmp_path``.

    - ``Path.home()`` resolves via ``HOME`` env on Unix → reroute to
      ``tmp_path / "home"`` so the user's real ``~/.openharness/skills/``
      can't contaminate the test.
    - chdir into ``tmp_path / "project"`` so ``detect_environment().cwd``
      lands there → project skills go under ``tmp_path / "project" /
      ".openharness" / "skills"``.

    Returns the project root for caller convenience.
    """
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    return project


def _write_skill_file(directory: Path, name: str, description: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody for {name}\n",
        encoding="utf-8",
    )


class TestSkillsBootstrap:
    """``--no-skills`` flag and default skill discovery thread through to
    QueryContext + ToolRegistry + system_prompt. Verifies the L3+L4 bootstrap
    contract: catalog injected only when skills exist, LoadSkillTool
    registered only when skills exist, ``--no-skills`` short-circuits both.
    """

    def test_default_no_skill_dirs_skips_registration(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _isolate_skills_dirs(monkeypatch, tmp_path)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        # P9-T3 wrap: skill_store is LayeredStore (FilesystemSkillStore
        # base + empty plugin catalog). Behaviorally identical to the
        # bare FilesystemSkillStore when no plugins loaded.
        # Empty dirs → discover() yields nothing.
        assert ctx.skill_store.discover() == {}  # type: ignore[attr-defined]
        # LoadSkill NOT registered when no skills found.
        names = [t.name for t in ctx.tool_registry.list_tools()]  # type: ignore[attr-defined]
        assert "LoadSkill" not in names
        # System prompt has no skills section.
        assert "Available Skills" not in ctx.system_prompt  # type: ignore[attr-defined]

    def test_project_skills_register_LoadSkill(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        _write_skill_file(
            project / ".openharness" / "skills",
            "team-style",
            "Team coding conventions",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        # Skill discovered, LoadSkill registered, catalog in prompt.
        assert "team-style" in ctx.skill_store.discover()  # type: ignore[attr-defined]
        names = [t.name for t in ctx.tool_registry.list_tools()]  # type: ignore[attr-defined]
        assert "LoadSkill" in names
        assert "Available Skills" in ctx.system_prompt  # type: ignore[attr-defined]
        assert "team-style" in ctx.system_prompt  # type: ignore[attr-defined]
        assert "Team coding conventions" in ctx.system_prompt  # type: ignore[attr-defined]

    def test_global_skill_visible_when_no_project_skill(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _isolate_skills_dirs(monkeypatch, tmp_path)
        # Only global skill, no project skill.
        _write_skill_file(
            tmp_path / "home" / ".openharness" / "skills",
            "global-skill",
            "User-wide expertise",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        assert "global-skill" in ctx.skill_store.discover()  # type: ignore[attr-defined]

    def test_project_overrides_global(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        # Same name in both layers.
        _write_skill_file(
            tmp_path / "home" / ".openharness" / "skills",
            "shared",
            "global version of shared",
        )
        _write_skill_file(
            project / ".openharness" / "skills",
            "shared",
            "project version of shared",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        # System prompt carries the project description, not the global one.
        assert "project version of shared" in ctx.system_prompt  # type: ignore[attr-defined]
        assert "global version of shared" not in ctx.system_prompt  # type: ignore[attr-defined]

    def test_no_skills_flag_uses_empty_store(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:

        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        # Put a skill on disk — --no-skills must still ignore it.
        _write_skill_file(
            project / ".openharness" / "skills",
            "should-not-appear",
            "ignored by --no-skills",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--no-skills"])
        assert result.exit_code == 0

        ctx = captured.context
        # P9-T3 wrap: ``--no-skills`` puts EmptySkillStore as the
        # LayeredStore base; with no plugin skills loaded the wrapped
        # store behaves identically (discover() == {}).
        assert ctx.skill_store.discover() == {}  # type: ignore[attr-defined]
        names = [t.name for t in ctx.tool_registry.list_tools()]  # type: ignore[attr-defined]
        assert "LoadSkill" not in names
        assert "should-not-appear" not in ctx.system_prompt  # type: ignore[attr-defined]
        assert "Available Skills" not in ctx.system_prompt  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Slash commands (P5b-T3)                                                     #
# --------------------------------------------------------------------------- #


def _write_command_file(directory: Path, name: str, description: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )


class TestSlashCommands:
    """``/cmd args`` expansion + ``--no-commands`` flag + ``UnknownCommandError``
    UX. Reuses the ``_isolate_skills_dirs`` helper (HOME redirect + chdir to
    a tmp project root) because slash commands live under the same
    ``.openharness/`` directory convention.
    """

    def test_no_slash_prompt_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _isolate_skills_dirs(monkeypatch, tmp_path)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "regular prompt"])
        assert result.exit_code == 0
        # LLM saw the original prompt verbatim — no expansion attempted.
        assert stub.last_request is not None
        assert stub.last_request.messages[0].content[0].text == "regular prompt"  # type: ignore[union-attr]

    def test_slash_command_resolves_and_reaches_llm(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        _write_command_file(
            project / ".openharness" / "commands",
            "review",
            "Review pending changes",
            "Please review:\n\n{args}\nFocus on edge cases.",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/review last 3 commits"])
        assert result.exit_code == 0

        # The LLM's user message is the resolved body — slash + cmd name gone.
        assert stub.last_request is not None
        user_text = stub.last_request.messages[0].content[0].text  # type: ignore[union-attr]
        assert "Please review:" in user_text
        assert "last 3 commits" in user_text
        assert "Focus on edge cases." in user_text
        assert "{args}" not in user_text  # placeholder substituted
        assert not user_text.startswith("/review")  # slash prefix consumed

    def test_unknown_command_exits_with_catalog_in_stderr(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        _write_command_file(
            project / ".openharness" / "commands",
            "review",
            "Review pending changes",
            "Body",
        )

        # No LLM stub needed — error fires before any LLM call.
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/nonexistent some args"])
        assert result.exit_code == 1
        # Error message names the bad command + lists the available one.
        assert "Unknown command" in result.stderr
        assert "nonexistent" in result.stderr
        assert "review" in result.stderr

    def test_no_commands_flag_passes_slash_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        # Even with a real command on disk, --no-commands must skip lookup.
        _write_command_file(
            project / ".openharness" / "commands",
            "review",
            "should not fire",
            "would-expand body",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/review args", "--no-commands"])
        assert result.exit_code == 0
        # LLM sees the slash prefix verbatim — no expansion attempted.
        assert stub.last_request is not None
        user_text = stub.last_request.messages[0].content[0].text  # type: ignore[union-attr]
        assert user_text == "/review args"
        assert "would-expand body" not in user_text

    def test_project_command_overrides_global(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        _write_command_file(
            tmp_path / "home" / ".openharness" / "commands",
            "shared",
            "global desc",
            "global body for {args}",
        )
        _write_command_file(
            project / ".openharness" / "commands",
            "shared",
            "project desc",
            "project body for {args}",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/shared X"])
        assert result.exit_code == 0
        assert stub.last_request is not None
        user_text = stub.last_request.messages[0].content[0].text  # type: ignore[union-attr]
        assert "project body for X" in user_text
        assert "global body for X" not in user_text

    def test_command_without_args_resolves_with_empty_substitution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        _write_command_file(
            project / ".openharness" / "commands",
            "plan",
            "Plan something",
            "Make a plan:\n{args}\n",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/plan"])
        assert result.exit_code == 0
        assert stub.last_request is not None
        user_text = stub.last_request.messages[0].content[0].text  # type: ignore[union-attr]
        assert "Make a plan:" in user_text
        assert "{args}" not in user_text  # substituted with empty string


# --------------------------------------------------------------------------- #
# Sub-agent bootstrap (P6-T5)                                                 #
# --------------------------------------------------------------------------- #


class TestSubAgentBootstrap:
    """``Agent`` (SpawnAgent) is in the default tool registry; CLI propagates
    ``max_agent_depth`` from Settings into QueryContext; the LLM sees the
    Agent tool in its catalog by default.
    """

    def test_agent_tool_in_default_registry_visible_to_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Default oh ask invocation → LLM's tools list contains Agent.
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0
        assert stub.last_request is not None
        tool_names = {t.name for t in (stub.last_request.tools or [])}
        assert "Agent" in tool_names

    def test_max_agent_depth_propagates_from_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openharness.engine import QueryContext

        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MAX_AGENT_DEPTH", "5")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        assert isinstance(ctx, QueryContext)
        assert ctx.max_agent_depth == 5
        assert ctx.agent_depth == 0  # top-level oh ask runs at depth 0

    def test_default_max_agent_depth_is_three(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.engine import QueryContext

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        ctx = captured.context
        assert isinstance(ctx, QueryContext)
        assert ctx.max_agent_depth == 3  # Settings default

    def test_zero_max_agent_depth_disables_spawning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.engine import QueryContext

        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MAX_AGENT_DEPTH", "0")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0
        ctx = captured.context
        assert isinstance(ctx, QueryContext)
        # 0 + 1 > 0 → SpawnAgent will refuse any invocation. The Agent
        # tool is still in the catalog (LLM sees it); refusing is the
        # depth check's job at dispatch time.
        assert ctx.max_agent_depth == 0


# --------------------------------------------------------------------------- #
# Sandbox flags (P7b-T2)                                                      #
# --------------------------------------------------------------------------- #


class TestSandboxFlags:
    """Sandbox flags compile intent into a verified session boundary."""

    @staticmethod
    def _patch_backend(
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        captured_init: dict[str, object],
        captured_profile: list[object],
    ) -> object:
        from openharness.execution import (
            BoundaryVerification,
            EnforcedBoundary,
            ExecutionEffect,
        )

        class _Session:
            def __init__(self) -> None:
                self.boundary: EnforcedBoundary | None = None

            async def close(self) -> None:
                pass

        session = _Session()

        class _Backend:
            def __init__(self, **kwargs: object) -> None:
                captured_init.update(kwargs)

            async def open(self, profile: object) -> _Session:
                captured_profile.append(profile)
                effects = (
                    (ExecutionEffect.COMMAND,)
                    if name == "DockerCommandBackend"
                    else (
                        ExecutionEffect.COMMAND,
                        ExecutionEffect.FILE_READ,
                        ExecutionEffect.FILE_WRITE,
                        ExecutionEffect.FILE_SEARCH,
                    )
                )
                session.boundary = EnforcedBoundary(
                    profile_fingerprint=profile.fingerprint,  # type: ignore[attr-defined]
                    backend=name,
                    backend_version="test",
                    covered_effects=effects,
                    verification=BoundaryVerification.VERIFIED,
                )
                return session

        monkeypatch.setattr(cli_module, name, _Backend)
        return session

    def test_default_no_sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.execution.host import HostExecution

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0
        ctx = captured.context
        assert isinstance(ctx.execution_env, HostExecution)  # type: ignore[attr-defined]
        assert ctx.sandbox_session is None  # type: ignore[attr-defined]

    def test_sandbox_flag_enters_sandbox_context(self, monkeypatch: pytest.MonkeyPatch) -> None:

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        captured_init: dict[str, object] = {}
        captured_profile: list[object] = []
        session = self._patch_backend(
            monkeypatch, "SeatbeltBackend", captured_init, captured_profile
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--sandbox"])
        assert result.exit_code == 0

        assert "cwd" in captured_init
        assert captured_profile[0].network.enabled is False  # type: ignore[attr-defined]
        ctx = captured.context
        from openharness.execution import OneShotOverlaySession

        assert isinstance(ctx.sandbox_session, OneShotOverlaySession)  # type: ignore[attr-defined]
        assert ctx.sandbox_session.boundary is session.boundary  # type: ignore[attr-defined]

    def test_sandbox_network_flag_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        captured_init: dict[str, object] = {}
        captured_profile: list[object] = []
        self._patch_backend(monkeypatch, "DockerCommandBackend", captured_init, captured_profile)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "hi",
                "--sandbox",
                "--sandbox-backend",
                "docker-command",
                "--sandbox-network",
                "bridge",
                "--sandbox-memory",
                "512m",
                "--sandbox-cpus",
                "0.5",
                "--sandbox-image",
                "ubuntu:latest",
            ],
        )
        assert result.exit_code == 0
        assert captured_init["memory"] == "512m"
        assert captured_init["cpus"] == 0.5
        assert captured_init["image"] == "ubuntu:latest"
        assert captured_profile[0].network.enabled is True  # type: ignore[attr-defined]

    def test_env_var_enables_sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_SANDBOX_ENABLED", "true")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        session = self._patch_backend(monkeypatch, "SeatbeltBackend", {}, [])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0
        ctx = captured.context
        from openharness.execution import OneShotOverlaySession

        assert isinstance(ctx.sandbox_session, OneShotOverlaySession)  # type: ignore[attr-defined]
        assert ctx.sandbox_session.boundary is session.boundary  # type: ignore[attr-defined]

    def test_default_runtime_is_runc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # P7c-T2: default sandbox_runtime is "runc" (Phase 7b
        # behavior preserved).
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        captured_init: dict[str, object] = {}
        self._patch_backend(monkeypatch, "DockerCommandBackend", captured_init, [])

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "hi", "--sandbox", "--sandbox-backend", "docker-command"],
        )
        assert result.exit_code == 0
        assert captured_init["runtime"] == "runc"

    def test_sandbox_runtime_flag_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # P7c-T2: --sandbox-runtime overrides Settings + env var.
        _set_minimum_env(monkeypatch)
        # Even with env var set, CLI flag wins.
        monkeypatch.setenv("OPENHARNESS_SANDBOX_RUNTIME", "runc")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        captured_init: dict[str, object] = {}
        self._patch_backend(monkeypatch, "DockerCommandBackend", captured_init, [])

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "hi",
                "--sandbox",
                "--sandbox-backend",
                "docker-command",
                "--sandbox-runtime",
                "runsc",
            ],
        )
        assert result.exit_code == 0
        assert captured_init["runtime"] == "runsc"

    def test_no_sandbox_flag_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.execution.host import HostExecution

        _set_minimum_env(monkeypatch)
        # Env says enable, but CLI says no.
        monkeypatch.setenv("OPENHARNESS_SANDBOX_ENABLED", "true")
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi", "--no-sandbox"])
        assert result.exit_code == 0
        ctx = captured.context
        # HostExecution wins because --no-sandbox CLI flag overrides env.
        assert isinstance(ctx.execution_env, HostExecution)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# P5d-T4: ModeBundle integration                                              #
# --------------------------------------------------------------------------- #


def _write_bundle_file(
    directory: Path,
    name: str,
    description: str,
    *,
    system_prompt: str | None = None,
    tools_whitelist: list[str] | None = None,
    deny_paths: list[str] | None = None,
    hooks: list[str] | None = None,
) -> None:
    """Author a ModeBundle markdown file with the requested overrides."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if system_prompt is not None:
        lines.append("system_prompt: |")
        for body_line in system_prompt.splitlines():
            lines.append(f"  {body_line}")
    if tools_whitelist is not None:
        lines.append("tools:")
        lines.append(f"  whitelist: {tools_whitelist!r}".replace("'", '"'))
    if deny_paths is not None:
        lines.append("deny_paths:")
        for p in deny_paths:
            lines.append(f"  - {p!r}".replace("'", '"'))
    if hooks is not None:
        lines.append(f"hooks: {hooks!r}".replace("'", '"'))
    lines.append("---")
    (directory / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestBundles:
    """``mode:`` field on a slash command resolves a ModeBundle and applies
    its 4-layer overrides to the QueryContext. ``UnknownBundleError`` UX
    surfaces a friendly error for the typo case.
    """

    def test_slash_command_without_mode_runs_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Regression: P5b's slash-command flow keeps working when no
        # ``mode:`` is present — no bundle load attempted.
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        _write_command_file(
            project / ".openharness" / "commands",
            "plain",
            "plain cmd",
            "expanded body",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/plain args"])
        assert result.exit_code == 0
        # No bundle → registry is the base ToolRegistry, NOT a WhitelistRegistry.
        from openharness.bundles import WhitelistRegistry

        ctx = captured.context
        assert not isinstance(ctx.tool_registry, WhitelistRegistry)  # type: ignore[attr-defined]

    def test_mode_field_applies_all_four_layers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from openharness.bundles import WhitelistRegistry
        from openharness.bundles.hooks import audit_log, deny_writes

        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        # Command references a bundle.
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "review.md").write_text(
            "---\nname: review\ndescription: code review\nmode: code-review\n---\n"
            "Review:\n{args}\n",
            encoding="utf-8",
        )
        # Bundle with all 4 layers.
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "code-review",
            "read-only code review mode",
            system_prompt="You are a code reviewer. Read-only.",
            tools_whitelist=["Read", "Grep"],
            deny_paths=["secrets/**"],
            hooks=["audit_log", "deny_writes"],
        )
        (project / "AGENTS.md").write_text(
            "Follow the target project's review policy.\n",
            encoding="utf-8",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/review the diff"])
        assert result.exit_code == 0, result.stderr
        ctx = captured.context
        # Layer 1: the bundle replaces the harness-owned base prompt while the
        # target-project instruction layer remains present.
        assert ctx.system_prompt.startswith("You are a code reviewer. Read-only.")  # type: ignore[attr-defined]
        assert "## Project Instructions" in ctx.system_prompt  # type: ignore[attr-defined]
        assert "Follow the target project's review policy." in ctx.system_prompt  # type: ignore[attr-defined]
        # Layer 2: tool_registry is the whitelist wrapper; only Read+Grep visible.
        assert isinstance(ctx.tool_registry, WhitelistRegistry)  # type: ignore[attr-defined]
        names = sorted(t.name for t in ctx.tool_registry.list_tools())  # type: ignore[attr-defined]
        assert names == ["Grep", "Read"]
        # Layer 3a: deny_paths reach the permission_checker (via Settings).
        assert "secrets/**" in ctx.permission_checker._deny_paths  # type: ignore[attr-defined]
        # Layer 3b: bundle's hooks are in the hook_registry.
        post = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        pre = ctx.hook_registry.get("PreToolUse")  # type: ignore[attr-defined]
        assert audit_log in post
        assert deny_writes in pre

    def test_unknown_bundle_exits_with_friendly_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        # Command refs a bundle that doesn't exist.
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "review.md").write_text(
            "---\nname: review\ndescription: x\nmode: nonexistent\n---\nbody\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/review args"])
        assert result.exit_code == 1
        assert "Unknown bundle" in result.stderr
        assert "nonexistent" in result.stderr

    def test_bundle_without_system_prompt_rebuilds_against_whitelist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # When bundle has whitelist but no system_prompt, the system prompt
        # is rebuilt against the EFFECTIVE registry — the LLM sees only the
        # whitelisted tools in its catalog.
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "ro.md").write_text(
            "---\nname: ro\ndescription: ro\nmode: readonly\n---\nbody\n",
            encoding="utf-8",
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "readonly",
            "read-only tools",
            tools_whitelist=["Read"],
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/ro x"])
        assert result.exit_code == 0
        ctx = captured.context
        # System prompt was rebuilt — catalog should mention Read but NOT Write.
        assert "**Read**" in ctx.system_prompt  # type: ignore[attr-defined]
        assert "**Write**" not in ctx.system_prompt  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# P5e-T3: Plugin hook discovery CLI integration                               #
# --------------------------------------------------------------------------- #


class TestPluginHookDiscovery:
    """``--enable-plugin-hooks`` flag + discovered hooks reach the bundle's
    hook resolution path. With the flag off (default), a bundle's
    ``hooks:`` field referencing a plugin name raises UnknownBundleError.
    """

    def test_flag_off_default_unknown_plugin_hook_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Bundle references "slack_notify"; plugin discovery is OFF →
        # name not in BUILTIN_HOOKS → UnknownBundleError → exit 1.
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "review.md").write_text(
            "---\nname: review\ndescription: x\nmode: code-review\n---\nbody\n",
            encoding="utf-8",
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "code-review",
            "review mode",
            hooks=["slack_notify"],
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/review args"])
        assert result.exit_code == 1
        assert "Unknown hook" in result.stderr
        assert "slack_notify" in result.stderr

    def test_flag_on_discovers_plugin_and_resolves(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Stub discover_plugin_hooks to return a fake plugin; flag on
        # so the bundle's hook_names resolves it.
        from openharness.bundles.hook_plugins import HookSpec

        async def fake_plugin(ctx: object) -> None:
            del ctx
            return None

        stub_catalog = {
            "slack_notify": HookSpec(event="PostToolUse", hook=fake_plugin),
        }

        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "review.md").write_text(
            "---\nname: review\ndescription: x\nmode: code-review\n---\nbody\n",
            encoding="utf-8",
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "code-review",
            "review mode",
            hooks=["audit_log", "slack_notify"],
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        monkeypatch.setattr(cli_module, "discover_plugin_hooks", lambda: stub_catalog)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/review args", "--enable-plugin-hooks"])
        assert result.exit_code == 0, result.stderr
        ctx = captured.context
        post = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        # Both audit_log (built-in) and fake_plugin (plugin) registered.
        assert fake_plugin in post

    def test_env_var_enables_discovery(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # OPENHARNESS_ENABLE_PLUGIN_HOOKS=true with no CLI flag → flag on.
        from openharness.bundles.hook_plugins import HookSpec

        async def fake_plugin(ctx: object) -> None:
            del ctx
            return None

        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_ENABLE_PLUGIN_HOOKS", "true")
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "x.md").write_text(
            "---\nname: x\ndescription: x\nmode: m\n---\nbody\n", encoding="utf-8"
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "m",
            "m",
            hooks=["my_hook"],
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        monkeypatch.setattr(
            cli_module,
            "discover_plugin_hooks",
            lambda: {"my_hook": HookSpec(event="PreToolUse", hook=fake_plugin)},
        )
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/x args"])
        assert result.exit_code == 0
        ctx = captured.context
        assert fake_plugin in ctx.hook_registry.get("PreToolUse")  # type: ignore[attr-defined]

    def test_no_flag_overrides_env_var(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # env=true but --no-enable-plugin-hooks → CLI wins → discovery off.
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_ENABLE_PLUGIN_HOOKS", "true")
        project = _isolate_skills_dirs(monkeypatch, tmp_path)
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "x.md").write_text(
            "---\nname: x\ndescription: x\nmode: m\n---\nbody\n", encoding="utf-8"
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "m",
            "m",
            hooks=["slack_notify"],
        )

        # discover_plugin_hooks should NOT be called; if it is, we'd
        # see a TypeError from the lambda. Use a sentinel that raises.
        def _should_not_call() -> object:
            raise AssertionError("discover_plugin_hooks called despite --no-enable-plugin-hooks")

        monkeypatch.setattr(cli_module, "discover_plugin_hooks", _should_not_call)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/x args", "--no-enable-plugin-hooks"])
        # discovery off → slack_notify unknown → exit 1.
        assert result.exit_code == 1
        assert "Unknown hook" in result.stderr


# --------------------------------------------------------------------------- #
# P5f-T2: Filesystem hook plugin CLI integration                              #
# --------------------------------------------------------------------------- #


class TestFilesystemHookPlugins:
    """``~/.openharness/hooks/*.py`` discovery merges into the same
    catalog as entry-point plugins when ``--enable-plugin-hooks`` is on.
    """

    def test_filesystem_hook_resolves_when_flag_on(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)

        # Write a real .py hook file under the project's hooks dir.
        hooks_dir = project / ".openharness" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "my_audit.py").write_text(
            "from openharness.bundles import hook_spec\n"
            "@hook_spec('PostToolUse')\n"
            "async def my_audit(ctx):\n"
            "    return None\n",
            encoding="utf-8",
        )

        # Bundle references the filesystem-discovered hook.
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "x.md").write_text(
            "---\nname: x\ndescription: x\nmode: m\n---\nbody\n",
            encoding="utf-8",
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "m",
            "uses filesystem hook",
            hooks=["my_audit"],
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/x args", "--enable-plugin-hooks"])
        assert result.exit_code == 0, result.stderr
        ctx = captured.context
        post = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        # Hook from filesystem .py file registered.
        assert any(getattr(h, "__name__", "") == "my_audit" for h in post)

    def test_entry_point_shadows_filesystem_on_same_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Plugin name appears in BOTH entry-point catalog AND filesystem
        # discovery — entry-point version wins (D22.4 first-wins).
        from openharness.bundles import HookSpec

        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)

        async def entry_point_version(ctx: object) -> None:
            del ctx
            return None

        # Filesystem version of the same plugin name.
        hooks_dir = project / ".openharness" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "dup.py").write_text(
            "from openharness.bundles import hook_spec\n"
            "@hook_spec('PreToolUse')\n"  # different event!
            "async def shared_name(ctx):\n"
            "    return None\n",
            encoding="utf-8",
        )

        # Bundle references the shared name.
        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "x.md").write_text(
            "---\nname: x\ndescription: x\nmode: m\n---\nbody\n",
            encoding="utf-8",
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "m",
            "test",
            hooks=["shared_name"],
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        # Entry-point returns the same name with a DIFFERENT event.
        monkeypatch.setattr(
            cli_module,
            "discover_plugin_hooks",
            lambda: {
                "shared_name": HookSpec(event="PostToolUse", hook=entry_point_version),
            },
        )
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "/x args", "--enable-plugin-hooks"])
        assert result.exit_code == 0
        ctx = captured.context
        # Entry-point version (PostToolUse) wins, filesystem (PreToolUse)
        # is shadowed.
        post = ctx.hook_registry.get("PostToolUse")  # type: ignore[attr-defined]
        pre = ctx.hook_registry.get("PreToolUse")  # type: ignore[attr-defined]
        assert entry_point_version in post
        # Filesystem version not registered on PreToolUse — entry-point
        # shadowed it.
        assert not any(getattr(h, "__name__", "") == "shared_name" for h in pre)

    def test_filesystem_not_loaded_when_flag_off(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Both discovery functions skipped when flag off (sentinel).
        _set_minimum_env(monkeypatch)
        project = _isolate_skills_dirs(monkeypatch, tmp_path)

        # Write a real .py hook that would load if discovery ran.
        hooks_dir = project / ".openharness" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "h.py").write_text(
            "from openharness.bundles import hook_spec\n"
            "@hook_spec('PostToolUse')\n"
            "async def fs_hook(ctx):\n"
            "    return None\n",
            encoding="utf-8",
        )

        cmd_path = project / ".openharness" / "commands"
        cmd_path.mkdir(parents=True, exist_ok=True)
        (cmd_path / "x.md").write_text(
            "---\nname: x\ndescription: x\nmode: m\n---\nbody\n",
            encoding="utf-8",
        )
        _write_bundle_file(
            project / ".openharness" / "bundles",
            "m",
            "m",
            hooks=["fs_hook"],
        )

        # Sentinel: both discovery funcs should NOT be called.
        def _should_not_call_entry() -> object:
            raise AssertionError("discover_plugin_hooks invoked despite flag off")

        def _should_not_call_fs(**kwargs: object) -> object:
            del kwargs
            raise AssertionError("discover_filesystem_hook_plugins invoked despite flag off")

        monkeypatch.setattr(cli_module, "discover_plugin_hooks", _should_not_call_entry)
        monkeypatch.setattr(cli_module, "discover_filesystem_hook_plugins", _should_not_call_fs)

        runner = CliRunner()
        # Default flag off — bundle refs unknown hook → exit 1.
        result = runner.invoke(cli_module.app, ["ask", "/x args"])
        assert result.exit_code == 1
        assert "Unknown hook" in result.stderr


# --------------------------------------------------------------------------- #
# L1 headless entry — print mode (loop-runtime-L1-plan.md)                     #
# --------------------------------------------------------------------------- #


class TestPrintMode:
    """`oh -p "<goal>"` — non-interactive headless atom (loop-runtime L1).

    T1 (thinnest slice): the ``-p/--print`` flag exists, ``--output-format``
    defaults to ``text``, the final assistant text reaches stdout, and the
    process exits 0 on a clean ``end_turn`` run.
    """

    def test_print_mode_text_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("hi from stub"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "say hi"])

        assert result.exit_code == 0, result.stderr
        assert "hi from stub" in result.stdout

    def test_print_mode_long_flag_and_explicit_text_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("hi from stub"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app, ["ask", "--print", "--output-format", "text", "say hi"]
        )

        assert result.exit_code == 0, result.stderr
        assert "hi from stub" in result.stdout

    # ---- T2: run-level two-tier exit code (end_turn → 0, else → non-0) ---- #

    def test_print_mode_end_turn_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Contract: a clean ``end_turn`` run exits 0 in print mode."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "go"])

        assert result.exit_code == 0, result.stderr

    def test_print_mode_incomplete_stop_reason_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The genuine T2 gap: a run that ends on a non-``end_turn`` terminal
        stop_reason (here ``max_tokens``) raises no exception but did NOT
        finish cleanly — print mode must surface that as a non-zero exit."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_incomplete_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "go"])

        assert result.exit_code != 0

    # ---- T3: --output-format json (single final result object) ---- #

    def test_print_mode_json_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``-p --output-format json`` emits ONE result object on stdout with
        the locked field set (result / stop_reason / usage / num_turns /
        session_id; cost_usd=null — v1 has no pricing layer, L1 T0)."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("the answer"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "--output-format", "json", "go"])

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["type"] == "result"
        assert obj["result"] == "the answer"
        assert obj["stop_reason"] == "end_turn"
        assert obj["usage"] == {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4}
        assert obj["cost_usd"] is None
        assert obj["num_turns"] == 1
        assert isinstance(obj["session_id"], str) and obj["session_id"]

    def test_print_mode_json_incomplete_still_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """json mode still honors the run-level exit code: a non-end_turn run
        emits the json object on stdout AND exits non-zero (T2 ∩ T3)."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_incomplete_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "--output-format", "json", "go"])

        assert result.exit_code != 0
        obj = json.loads(result.stdout)
        assert obj["stop_reason"] == "max_tokens"

    # ---- stream-json still gated until T4 ---- #

    def test_print_mode_stream_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T4: ``-p --output-format stream-json`` emits one JSON object per
        event (newline-delimited), terminated by a ``result`` object."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("streamed"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app, ["ask", "-p", "--output-format", "stream-json", "go"]
        )

        assert result.exit_code == 0, result.stderr
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert any(obj["type"] == "assistant_delta" for obj in lines)
        assert lines[-1]["type"] == "result"
        assert lines[-1]["stop_reason"] == "end_turn"
        assert lines[-1]["session_id"]

    def test_output_format_requires_print_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--output-format`` is meaningless without ``-p`` and is rejected."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "--output-format", "json", "go"])

        assert result.exit_code == 2
        assert "only applies in headless print mode" in result.stderr

    # ---- T5: headless permission posture (fail-closed + permission_mode 透传) ---- #
    # Characterization/regression tests (NOT RED-first): v1's engine already
    # fail-closes ASK→DENY for non-AUTO modes (query.py Three-Axis G), and
    # _run_ask already threads permission_mode + the real TierBasedPermissionChecker.
    # These lock the print-mode wiring against the exact regression upstream hit
    # (run_print_mode that noop-allowed + dropped permission_mode — reference §7.3).
    # The engine's ASK→DENY deny semantics are covered by tests/engine/test_query.py.

    def test_print_mode_threads_default_permission_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`-p` without `--auto` stays DEFAULT (fail-closed) — NOT silently
        auto-allowed. In DEFAULT mode the engine denies ASK (mutating) tools."""
        from openharness.permissions import PermissionMode

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "go"])

        assert result.exit_code == 0, result.stderr
        assert captured.context.permission_mode is PermissionMode.DEFAULT  # type: ignore[attr-defined]

    def test_print_mode_auto_threads_auto_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`-p --auto` threads AUTO into the engine context (the loop-runtime
        '圈地': trust in-cwd actions; sensitive paths still Tier-1 denied)."""
        from openharness.permissions import PermissionMode

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "--auto", "go"])

        assert result.exit_code == 0, result.stderr
        assert captured.context.permission_mode is PermissionMode.AUTO  # type: ignore[attr-defined]

    def test_print_mode_uses_real_permission_checker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`-p` wires the real TierBasedPermissionChecker — NOT a noop that
        allows everything (the upstream run_print_mode bug, §7.3)."""
        from openharness.permissions import TierBasedPermissionChecker

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "go"])

        assert result.exit_code == 0, result.stderr
        assert isinstance(
            captured.context.permission_checker,  # type: ignore[attr-defined]
            TierBasedPermissionChecker,
        )

    def test_print_mode_request_failure_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contract: an infra error exits non-zero in print mode (existing
        exception chain already maps RequestFailure → exit 1)."""
        _set_minimum_env(monkeypatch)
        stub = _RaisingStubClient(RequestFailure("boom", status_code=500))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "go"])

        assert result.exit_code != 0
