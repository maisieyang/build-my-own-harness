"""D43 正门收尾 — 根命令位置 prompt / 根命令 -p / 旗面瘦身(hidden)/
Ctrl+D 双按。Helpers mirror test_compact_repl.py(仓库惯例:每文件自带
小 helper,不跨测试文件 import)。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click import unstyle
from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent

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
    """Patch run_query, record initial_messages of every invocation."""
    seen: list[list[ConversationMessage]] = []

    async def _fake_run_query(
        initial_messages: list[ConversationMessage],
        context: object,
    ) -> AsyncIterator[ApiStreamEvent]:
        # snapshot, not reference — the chat REPL mutates one history list
        # across turns; storing the reference would alias every entry to
        # the final state (踩过:seen[0][-1] 变成了第二轮的消息)
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


class TestRootPositionalPrompt:
    """D43.1 — `oh "x"` 带首条消息进 REPL。"""

    def test_prompt_submitted_as_first_message_then_repl_continues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        seen = _capture_first_messages(monkeypatch)
        _stub_inputs(monkeypatch, ["再补充一点", "/exit"])

        result = runner.invoke(
            cli_module.app, cli_module._preprocess_root_argv(["解释一下引擎循环"])
        )

        assert result.exit_code == 0, result.output
        # 首轮 = 位置 prompt;第二轮 = REPL 里的追问(会话没被赶出门)
        assert len(seen) == 2
        first_turn_text = seen[0][-1].content[0].text  # type: ignore[union-attr]
        assert first_turn_text == "解释一下引擎循环"

    def test_bare_oh_still_enters_plain_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        seen = _capture_first_messages(monkeypatch)
        _stub_inputs(monkeypatch, ["/exit"])

        result = runner.invoke(cli_module.app, cli_module._preprocess_root_argv([]))

        assert result.exit_code == 0, result.output
        assert seen == []  # 没有自动提交任何消息


class TestRootPrintMode:
    """D43.2 — `oh -p "x"` = headless 机器门挂根命令。"""

    def test_root_p_emits_json_envelope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _capture_first_messages(monkeypatch)

        result = runner.invoke(
            cli_module.app,
            cli_module._preprocess_root_argv(
                ["-p", "列出三个 git 命令", "--output-format", "json"]
            ),
        )

        assert result.exit_code == 0, result.output
        assert '"type": "result"' in result.stdout

    def test_root_p_without_prompt_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)

        result = runner.invoke(cli_module.app, cli_module._preprocess_root_argv(["-p"]))

        assert result.exit_code == 2


class TestFlagDiet:
    """D43.3 — 配置旗退出 help 展示,解析照常(hidden ≠ 删除)。"""

    def test_retired_subcommands_are_absent(self) -> None:
        assert {"autopilot", "run"}.isdisjoint(cli_module._known_subcommands())

    def test_config_flags_hidden_from_ask_help(self) -> None:
        result = runner.invoke(cli_module.app, ["ask", "--help"])
        assert result.exit_code == 0
        for flag in ("--sandbox-image", "--enable-web", "--compact-threshold", "--llm-focus-state"):
            assert flag not in result.output, f"{flag} 应已 hidden"

    def test_contract_flags_still_visible(self) -> None:
        result = runner.invoke(cli_module.app, ["ask", "--help"])
        help_text = unstyle(result.output)
        for flag in ("--max-turns", "--isolate", "--output-format"):
            assert flag in help_text, f"{flag} 是合同旗,必须可见"

    def test_retired_repair_loop_flags_are_absent(self) -> None:
        result = runner.invoke(cli_module.app, ["ask", "--help"])
        for flag in ("--verify", "--goal-condition", "--max-iter", "--decompose", "--resume-run"):
            assert flag not in result.output

    def test_hidden_flag_still_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
        _capture_first_messages(monkeypatch)

        result = runner.invoke(
            cli_module.app,
            ["ask", "hi", "--compact-threshold", "0.5", "--enable-memory"],
        )

        assert result.exit_code == 0, result.output  # 兼容不破


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


class TestPreprocessRouting:
    """D43 known-subcommand-first 路由表(main() 入口的 argv 重写)。"""

    def test_routing_table(self) -> None:
        pp = cli_module._preprocess_root_argv
        assert pp([]) == []
        assert pp(["--version"]) == ["--version"]
        assert pp(["chat"]) == ["chat"]
        assert pp(["memory", "list"]) == ["memory", "list"]
        assert pp(["ask", "hi"]) == ["ask", "hi"]
        assert pp(["解释引擎"]) == ["chat", "解释引擎"]
        assert pp(["-m", "qwen-max", "解释引擎"]) == ["chat", "-m", "qwen-max", "解释引擎"]
        assert pp(["-p", "goal", "--output-format", "json"]) == [
            "ask",
            "-p",
            "goal",
            "--output-format",
            "json",
        ]

    def test_subcommand_name_as_prompt_routes_to_subcommand(self) -> None:
        # D43 已知限制: 与子命令同名的引号 prompt 会路由到子命令 (CC 同款取舍)
        assert cli_module._preprocess_root_argv(["memory"]) == ["memory"]
