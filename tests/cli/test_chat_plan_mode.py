"""plan-mode D47 — ``oh chat`` 接线集成 (RED).

驱动方式沿 test_chat.py house 式:stub ``input``(非 TTY legacy 路径,
菜单同样从这里读)+ 捕获 ``run_query`` 的 ``QueryContext``.接线断言
直接对捕获的 ``context.permission_checker.evaluate`` 发真调用——比读
私有属性稳,也正是 D47 T2 acceptance 要的"plan 态工具调用实际走 deny".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.permissions import Decision
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ConversationCompleteEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.tools import ToolExecutionContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest

    from openharness.engine import QueryContext
    from openharness.protocols.stream_events import ApiStreamEvent


class _PathArgs(BaseModel):
    path: str


class _CmdArgs(BaseModel):
    command: str


class _AgentArgs(BaseModel):
    description: str
    prompt: str


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class _ChatStubClient:
    async def stream_message(self, request: object) -> AsyncIterator[ApiStreamEvent]:
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ack")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _stub_input_events(monkeypatch: pytest.MonkeyPatch, events: list[str | BaseException]) -> None:
    iterator = iter(events)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            item = next(iterator)
        except StopIteration as exc:
            raise EOFError from exc
        if isinstance(item, BaseException):
            raise item
        return item

    import builtins

    monkeypatch.setattr(builtins, "input", _next)


def _stub_input_sequence(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    _stub_input_events(monkeypatch, lines)


def _install_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[list[ConversationMessage]]]:
    """Stub run_query; capture (context, initial_messages) per turn."""
    contexts: list[Any] = []
    messages: list[list[ConversationMessage]] = []

    async def _capturing_run_query(
        initial_messages: list[ConversationMessage], context: QueryContext
    ) -> AsyncIterator[ApiStreamEvent]:
        contexts.append(context)
        messages.append(list(initial_messages))
        assistant = ConversationMessage(role="assistant", content=[TextBlock(text="reply")])
        yield ApiMessageCompleteEvent(
            message=assistant,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )
        yield ConversationCompleteEvent(messages=[*initial_messages, assistant])

    monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)
    return contexts, messages


def _write_denied(context: Any, tmp_path: Path) -> bool:
    result = context.permission_checker.evaluate(
        "Write",
        _PathArgs(path=str(tmp_path / "x.py")),
        ToolExecutionContext(cwd=tmp_path),
    )
    return result.decision is Decision.DENY  # type: ignore[no-any-return]


def _plan_action_denied(context: Any, tool_name: str, args: BaseModel, tmp_path: Path) -> bool:
    assert context.action_deny_policy is not None
    return (
        context.action_deny_policy.evaluate(
            tool_name,
            args,
            ToolExecutionContext(cwd=tmp_path),
        )
        is not None
    )


class TestEnterPlanMode:
    def test_plan_turn_clamps_mutating_tools(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        # /plan → one exploratory turn → menu appears → keep planning → exit.
        _stub_input_sequence(monkeypatch, ["/plan", "explore the codebase", "2", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "plan mode" in result.stdout
        assert len(contexts) == 1
        ctx = contexts[0]
        # The model-facing registry is capability-shaped, not permission-overlaid.
        assert [tool.name for tool in ctx.tool_registry.list_tools()] == ["Read", "Grep"]
        assert ctx.dispatch_tool_registry is not None
        assert {tool.name for tool in ctx.dispatch_tool_registry.list_tools()} >= {
            "Write",
            "Bash",
            "Agent",
        }
        assert ctx.permission_checker._permissions.deny == ()
        # Forged/cached calls still hit the deny-only dispatch guard.
        assert _plan_action_denied(
            ctx,
            "Write",
            _PathArgs(path=str(tmp_path / "x.py")),
            tmp_path,
        )
        assert _plan_action_denied(ctx, "Bash", _CmdArgs(command="ls"), tmp_path)
        assert _plan_action_denied(
            ctx,
            "Agent",
            _AgentArgs(description="bypass", prompt="edit files"),
            tmp_path,
        )
        assert not _plan_action_denied(
            ctx,
            "Read",
            _PathArgs(path=str(tmp_path / "x.py")),
            tmp_path,
        )

    def test_plan_with_trailing_prompt_enters_plan_and_runs_prompt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, messages = _install_capture(monkeypatch)
        _stub_input_sequence(monkeypatch, ["/plan explore the approval behavior", "2", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "plan mode" in result.stdout
        assert len(contexts) == 1
        assert [tool.name for tool in contexts[0].tool_registry.list_tools()] == ["Read", "Grep"]
        text = "".join(b.text for b in messages[0][-1].content if isinstance(b, TextBlock))
        assert text == "explore the approval behavior"
        assert "plan mode" in contexts[0].system_prompt.lower()

    def test_plan_prompt_injected_only_in_plan_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        del tmp_path
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        # default turn → /plan → plan turn → keep planning → exit.
        _stub_input_sequence(monkeypatch, ["hello", "/plan", "make a plan", "2", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert len(contexts) == 2
        # T4: 姿态注入只在 plan 态 — 组装层断言(D47.6).
        assert "plan mode" not in contexts[0].system_prompt.lower()
        assert "plan mode" in contexts[1].system_prompt.lower()
        assert "execution starts only after" not in contexts[1].system_prompt.lower()
        assert "approval returns the session to default mode" in contexts[1].system_prompt.lower()

    def test_repeat_plan_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        _stub_input_sequence(monkeypatch, ["/plan", "/plan", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "already in plan mode" in result.stdout
        assert contexts == []  # 命令是 REPL 内建,不烧 LLM turn

    def test_help_mentions_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/help", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "/plan" in result.stdout


class TestApprovalMenu:
    def test_approve_manual_returns_to_default_without_auto_execution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, messages = _install_capture(monkeypatch)
        # Approval exits plan mode without auto-execution; the next user
        # message starts the default turn.
        _stub_input_sequence(
            monkeypatch, ["/plan", "draft the plan", "1", "turn this into a /goal", "/exit"]
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "plan approved" in result.stdout
        assert len(contexts) == 2  # plan turn + user-authored follow-up turn
        last = messages[1][-1]
        assert last.role == "user"
        text = "".join(b.text for b in last.content if isinstance(b, TextBlock))
        assert text == "turn this into a /goal"
        all_text = "\n".join(
            b.text for message in messages[1] for b in message.content if isinstance(b, TextBlock)
        )
        assert "[plan-status] approved:" in all_text
        assert "convert the approved plan into a concrete /goal condition" in all_text
        assert "runnable verification commands" in all_text
        assert "stop bounds" in all_text
        # deny 钳制已撤(交互 DEFAULT 姿态 in-cwd 写恢复 ALLOW).
        assert not _write_denied(contexts[1], tmp_path)
        # follow-up turn 的 system prompt 不再带 plan 姿态.
        assert "plan mode" not in contexts[1].system_prompt.lower()
        # D47 T4: 姿态注入是 turn 级 system prompt,不落进消息历史(历史是
        # 唯一被 snapshot 持久化的对话状态).
        for turn_messages in messages:
            for message in turn_messages:
                for block in message.content:
                    if isinstance(block, TextBlock):
                        assert "You are in plan mode" not in block.text

    def test_approve_accept_edits_does_not_arm_hidden_execution_grant(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        _stub_input_sequence(
            monkeypatch, ["/plan", "draft the plan", "1", "one more message", "/exit"]
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert len(contexts) == 2  # plan / user-authored follow-up
        follow_rules = contexts[1].permission_checker._permissions
        assert "Edit(*)" not in follow_rules.allow
        assert follow_rules.deny == ()  # plan deny 也不残留
        assert not _write_denied(contexts[1], tmp_path)

    def test_discard_returns_to_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        _stub_input_sequence(monkeypatch, ["/plan", "explore", "3", "back to normal", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "discarded" in result.stdout
        assert len(contexts) == 2
        # 放弃后回地面:钳制撤除,无自动执行 turn(第二个 turn 是用户消息).
        assert not _write_denied(contexts[1], tmp_path)

    def test_invalid_menu_input_reprompts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _install_capture(monkeypatch)
        _stub_input_sequence(monkeypatch, ["/plan", "explore", "junk", "3", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "1-3" in result.stdout  # re-prompt hint

    def test_menu_eof_fails_closed_to_discard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 非 TTY / 输入耗尽:菜单读到 EOF → 视为放弃(fail-closed),不挂死,
        # 不批准任何执行.随后 REPL 的双 Ctrl+D 语义正常退出.
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        _stub_input_sequence(monkeypatch, ["/plan", "explore"])  # menu hits EOF

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "discarded" in result.stdout
        assert len(contexts) == 1  # 没有偷跑执行 turn

    def test_menu_ctrl_c_keeps_planning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, messages = _install_capture(monkeypatch)
        _stub_input_events(
            monkeypatch,
            ["/plan", "explore", KeyboardInterrupt(), "still planning", "2", "/exit"],
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert len(contexts) == 2
        assert _plan_action_denied(
            contexts[1],
            "Write",
            _PathArgs(path=str(tmp_path / "x.py")),
            tmp_path,
        )
        followup = messages[1][-1]
        assert followup.role == "user"
        text = "".join(b.text for b in followup.content if isinstance(b, TextBlock))
        assert text == "still planning"
