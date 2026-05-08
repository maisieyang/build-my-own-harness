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

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from openharness import cli as cli_module
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
