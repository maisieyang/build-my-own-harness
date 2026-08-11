"""The public front door has one agent-starting shape: bare ``oh``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent

from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot

runner = CliRunner()


def _set_min_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class _StubClient:
    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _capture_first_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> list[list[ConversationMessage]]:
    seen: list[list[ConversationMessage]] = []

    async def _fake_run_query(
        initial_messages: list[ConversationMessage],
        context: object,
    ) -> AsyncIterator[ApiStreamEvent]:
        del context
        seen.append(list(initial_messages))
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(cli_module, "run_query", _fake_run_query)
    return seen


def _stub_inputs(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    """Feed REPL inputs; the sentinel "<EOF>" raises EOFError once (使
    Ctrl+D 语义可以在序列中间被模拟);耗尽后持续 EOFError。"""
    it = iter(lines)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            line = next(it)
        except StopIteration as exc:
            raise EOFError from exc
        if line == "<EOF>":
            raise EOFError
        return line

    import builtins

    monkeypatch.setattr(builtins, "input", _next)


class TestRemovedBranches:
    def test_positional_prompt_is_rejected(self) -> None:
        result = runner.invoke(cli_module.app, ["解释一下引擎循环"])

        assert result.exit_code == 2
        assert "No such command" in result.output

    def test_root_print_flag_is_rejected(self) -> None:
        result = runner.invoke(cli_module.app, ["-p", "列出三个 git 命令"])

        assert result.exit_code == 2
        assert "No such option" in result.output

    def test_ask_command_is_rejected(self) -> None:
        result = runner.invoke(cli_module.app, ["ask", "hello"])

        assert result.exit_code == 2
        assert "No such command" in result.output

    def test_chat_does_not_accept_an_initial_prompt(self) -> None:
        result = runner.invoke(cli_module.app, ["chat", "hello"])

        assert result.exit_code == 2
        assert "unexpected extra argument" in result.output.lower()

    def test_root_argv_preprocessor_is_gone(self) -> None:
        assert not hasattr(cli_module, "_preprocess_root_argv")


class TestCtrlDDoublePress:
    """D43.4 — 首个 EOF 提示,连续第二个退出,成功输入重置。"""

    def test_single_eof_hints_and_stays(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _stub_inputs(monkeypatch, ["<EOF>", "/exit"])

        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0, result.output
        assert "again to exit" in result.stdout  # 提示出现
        # 且 /exit 之前会话还活着(说明第一个 EOF 没有退出)

    def test_double_eof_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _stub_inputs(monkeypatch, [])  # 立即连续 EOF

        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0, result.output

    def test_successful_input_resets_the_arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        seen = _capture_first_messages(monkeypatch)
        _stub_inputs(monkeypatch, ["<EOF>", "hi", "<EOF>", "<EOF>"])

        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0, result.output
        assert len(seen) == 1  # "hi" 被正常处理(EOF 后武装被重置)
        assert result.stdout.count("again to exit") == 2  # 两次单发 EOF 各提示一次
