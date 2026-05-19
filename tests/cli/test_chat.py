"""Tests for ``oh chat`` — P6+-T2.

The REPL is driven by ``input()`` which we stub via monkeypatch.
``run_query`` is stubbed via the existing ``_patch_run_query_capture``
pattern so we capture the QueryContext + watch history grow across
turns.
"""

from __future__ import annotations

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


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class _ChatStubClient:
    """Stand-in for the LLM client; just satisfies the iterator
    protocol so run_query doesn't crash."""

    async def stream_message(self, request: object) -> AsyncIterator[ApiStreamEvent]:
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ack")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _stub_input_sequence(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    """Replace builtins.input with one that returns successive lines.
    Raising EOFError after exhaustion (which the REPL treats as exit).
    """
    iterator = iter(lines)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    # ``input`` is called inside asyncio.to_thread, so patching the
    # builtin namespace is what reaches the call site.
    import builtins

    monkeypatch.setattr(builtins, "input", _next)


# --------------------------------------------------------------------------- #
# Exit paths                                                                  #
# --------------------------------------------------------------------------- #


class TestChatExit:
    def test_exit_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0

    def test_quit_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/quit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0

    def test_eof_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        # Empty input list → first call raises EOFError.
        _stub_input_sequence(monkeypatch, [])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0


# --------------------------------------------------------------------------- #
# Built-in slash commands                                                     #
# --------------------------------------------------------------------------- #


class TestBuiltinSlashCommands:
    def test_help_prints_commands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/help", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "/clear" in result.stdout
        assert "/exit" in result.stdout

    def test_clear_resets_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        # Three turns: greeting, /clear, follow-up. After /clear, the
        # third turn's request should NOT include the first turn's
        # message — we verify by inspecting captured run_query calls.
        captured_initial_messages: list[list[ConversationMessage]] = []

        async def _capturing_run_query(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            del context
            # Snapshot the list as seen at call time.
            captured_initial_messages.append(list(initial_messages))
            # Emit a minimal turn: complete + conversation event.
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            # The engine would emit ConversationCompleteEvent at end —
            # we simulate that here.
            yield ConversationCompleteEvent(
                messages=[
                    *initial_messages,
                    ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
                ]
            )

        monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)
        _stub_input_sequence(monkeypatch, ["hello", "/clear", "world", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # Two turns ran (first + third). The third turn's initial
        # messages contain ONLY the "world" message (clear erased prior).
        assert len(captured_initial_messages) == 2
        third_turn_messages = captured_initial_messages[1]
        # Should be exactly 1 user message ("world"), not accumulated history.
        assert len(third_turn_messages) == 1
        assert third_turn_messages[0].role == "user"


# --------------------------------------------------------------------------- #
# Multi-turn history accumulation                                             #
# --------------------------------------------------------------------------- #


class TestMultiTurnHistory:
    def test_history_grows_across_turns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        captured_initial_messages: list[list[ConversationMessage]] = []

        async def _capturing_run_query(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            del context
            captured_initial_messages.append(list(initial_messages))
            assistant = ConversationMessage(role="assistant", content=[TextBlock(text="reply")])
            yield ApiMessageCompleteEvent(
                message=assistant,
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            yield ConversationCompleteEvent(messages=[*initial_messages, assistant])

        monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)
        _stub_input_sequence(monkeypatch, ["first prompt", "second prompt", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # First turn sees 1 user message; second turn sees the full
        # history (user + assistant + user).
        assert len(captured_initial_messages) == 2
        assert len(captured_initial_messages[0]) == 1
        assert len(captured_initial_messages[1]) == 3
        # Roles in turn 2: user, assistant, user.
        roles = [m.role for m in captured_initial_messages[1]]
        assert roles == ["user", "assistant", "user"]
