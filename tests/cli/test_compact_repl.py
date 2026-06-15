"""Tests for the ``/compact`` REPL command + the compact CLI flags.

Covers:

1. ``--no-auto-compact`` propagates to ``QueryContext.compact_enabled=False``
2. ``--compact-threshold 0.5`` propagates to
   ``QueryContext.compact_threshold_ratio==0.5``
3. ``/compact`` REPL command on empty history → friendly no-op
4. ``/compact`` REPL command with history → calls
   ``services.compact.full_compact`` and reports token deltas
5. ``--help`` output mentions the new flags

**Phase 17 D37.3**: the parallel ``--no-extract`` flag tests that
this file used to host were removed when the Phase 11 extraction
stack was deleted.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ConversationCompleteEvent,
)
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.stream_events import ApiStreamEvent


def _set_min_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class _StubClient:
    async def stream_message(self, request: object) -> AsyncIterator[ApiStreamEvent]:
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _stub_inputs(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    it = iter(lines)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError from exc

    import builtins

    monkeypatch.setattr(builtins, "input", _next)


def _capture_query_context() -> tuple[list[object], object]:
    """Return (captured_contexts, replacement run_query coroutine).

    The coroutine yields a minimal ``ConversationCompleteEvent`` so the
    REPL's history-capture path is exercised.
    """
    captured: list[object] = []

    async def _fake_run_query(
        initial_messages: list[ConversationMessage], context: object
    ) -> AsyncIterator[ApiStreamEvent]:
        captured.append(context)
        # Echo back the last message as if assistant responded.
        new_msg = ConversationMessage(role="assistant", content=[TextBlock(text="ack")])
        new_messages = [*initial_messages, new_msg]
        yield ApiMessageCompleteEvent(
            message=new_msg,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )
        yield ConversationCompleteEvent(messages=new_messages)

    return captured, _fake_run_query


# --------------------------------------------------------------------------- #
# Flag propagation — ask                                                      #
# --------------------------------------------------------------------------- #


class TestAskCompactExtractFlags:
    def test_no_auto_compact_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        captured, fake = _capture_query_context()
        monkeypatch.setattr(cli_module, "run_query", fake)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "--no-auto-compact", "hi"])
        assert result.exit_code == 0, result.stderr
        assert len(captured) == 1
        assert captured[0].compact_enabled is False  # type: ignore[attr-defined]

    def test_compact_threshold_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        captured, fake = _capture_query_context()
        monkeypatch.setattr(cli_module, "run_query", fake)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "--compact-threshold", "0.5", "hi"])
        assert result.exit_code == 0, result.stderr
        assert captured[0].compact_threshold_ratio == 0.5  # type: ignore[attr-defined]

    def test_threshold_invalid_above_one_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "--compact-threshold", "1.5", "hi"])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Flag propagation — chat                                                     #
# --------------------------------------------------------------------------- #


class TestChatCompactExtractFlags:
    def test_chat_no_auto_compact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        captured, fake = _capture_query_context()
        monkeypatch.setattr(cli_module, "run_query", fake)
        _stub_inputs(monkeypatch, ["hi", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat", "--no-auto-compact"])
        assert result.exit_code == 0, result.stderr
        assert len(captured) >= 1
        assert captured[0].compact_enabled is False  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# /compact REPL command                                                       #
# --------------------------------------------------------------------------- #


class TestCompactReplCommand:
    def test_compact_on_empty_history_friendly_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _stub_inputs(monkeypatch, ["/compact", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "no conversation to compact" in result.stdout

    def test_compact_with_history_invokes_full_compact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First turn populates history, /compact then replaces it.
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _, fake = _capture_query_context()
        monkeypatch.setattr(cli_module, "run_query", fake)

        # full_compact is in services.compact; replace at point of use
        # inside cli._run_chat (it imports at the call site).
        from openharness.services import compact as compact_module

        call_count = {"n": 0}

        async def _fake_full_compact(
            messages: list[ConversationMessage], **_kwargs: object
        ) -> tuple[list[ConversationMessage], bool]:
            call_count["n"] += 1
            summary = ConversationMessage(role="user", content=[TextBlock(text="Summary")])
            return [summary], True

        monkeypatch.setattr(compact_module, "full_compact", _fake_full_compact)

        _stub_inputs(monkeypatch, ["hi", "/compact", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0, result.stderr
        assert call_count["n"] == 1
        assert "compacted:" in result.stdout

    def test_compact_failure_friendly_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _, fake = _capture_query_context()
        monkeypatch.setattr(cli_module, "run_query", fake)

        from openharness.services import compact as compact_module

        async def _boom(*_a: object, **_kw: object) -> object:
            raise RuntimeError("compact bus error")

        monkeypatch.setattr(compact_module, "full_compact", _boom)

        _stub_inputs(monkeypatch, ["hi", "/compact", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        # REPL must NOT die on /compact failure
        assert result.exit_code == 0
        assert "/compact failed" in result.stderr


# --------------------------------------------------------------------------- #
# Help text                                                                   #
# --------------------------------------------------------------------------- #


class TestHelpMentionsNewFlags:
    def test_ask_help_lists_new_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Wide terminal so the flags don't wrap; strip ANSI because under
        # force_terminal (CI sets GITHUB_ACTIONS) Rich styles the help text.
        monkeypatch.setenv("COLUMNS", "200")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
        assert "--no-auto-compact" in plain
        assert "--compact-threshold" in plain

    def test_chat_help_lists_new_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLUMNS", "200")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
        assert "--no-auto-compact" in plain
        assert "--compact-threshold" in plain

    def test_chat_help_text_mentions_compact_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _stub_inputs(monkeypatch, ["/help", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "/compact" in result.stdout
