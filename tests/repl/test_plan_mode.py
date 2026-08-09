"""plan-mode D47 — REPL 层纯单元 (RED).

repl.py 的设计契约是"unit-testable without a TTY"——plan 模式的状态机
原语(ChatMode / 菜单选项解析 / capability shaping / 状态栏 mode 标识)全部
落在这层,cli._run_chat 只做接线.

这些测试固定 plan mode 与 canonical authorization 分离后的当前契约.
"""

from __future__ import annotations

from openharness.repl import (
    BUILTIN_SLASH_COMMANDS,
    PLAN_MENU_TEXT,
    PlanMenuChoice,
    format_status_bar,
    parse_plan_menu_choice,
    shape_plan_tool_registry,
)
from openharness.tools import create_default_tool_registry


class TestSlashMenuHasPlan:
    def test_plan_is_a_builtin_command(self) -> None:
        names = [c.name for c in BUILTIN_SLASH_COMMANDS]
        assert "/plan" in names


class TestParsePlanMenuChoice:
    def test_valid_choices(self) -> None:
        assert parse_plan_menu_choice("1") is PlanMenuChoice.APPROVE
        assert parse_plan_menu_choice("2") is PlanMenuChoice.KEEP_PLANNING
        assert parse_plan_menu_choice("3") is PlanMenuChoice.DISCARD

    def test_whitespace_tolerated(self) -> None:
        assert parse_plan_menu_choice("  2  ") is PlanMenuChoice.KEEP_PLANNING

    def test_invalid_returns_none(self) -> None:
        assert parse_plan_menu_choice("") is None
        assert parse_plan_menu_choice("yes") is None
        assert parse_plan_menu_choice("4") is None

    def test_menu_text_lists_all_three(self) -> None:
        for key in ("1", "2", "3"):
            assert key in PLAN_MENU_TEXT


class TestPlanCapabilityShaping:
    def test_plan_view_exposes_only_read_only_non_delegated_tools(self) -> None:
        base = create_default_tool_registry()
        # Prove the domain exclusion is independent of the legacy read-only bit.
        base.get("Agent").is_read_only = True

        shaped = shape_plan_tool_registry(base)

        assert [tool.name for tool in shaped.list_tools()] == ["Read", "Grep"]
        assert [schema.name for schema in shaped.to_api_schema()] == ["Read", "Grep"]

    def test_plan_view_does_not_mutate_the_default_registry(self) -> None:
        base = create_default_tool_registry()
        expected = [tool.name for tool in base.list_tools()]

        shaped = shape_plan_tool_registry(base)

        assert shaped is not base
        assert [tool.name for tool in base.list_tools()] == expected
        assert "Write" in expected
        assert "Bash" in expected
        assert "Agent" in expected


class TestStatusBarMode:
    def test_plan_mode_shown(self) -> None:
        bar = format_status_bar(
            model="m", used_tokens=1000, context_window=100_000, threshold_ratio=None, mode="plan"
        )
        assert "plan" in bar

    def test_default_mode_omitted(self) -> None:
        bar = format_status_bar(
            model="m", used_tokens=1000, context_window=100_000, threshold_ratio=None, mode=None
        )
        assert "plan" not in bar


class TestApprovalMessage:
    def test_menu_does_not_promise_auto_execution(self) -> None:
        assert "execute" not in PLAN_MENU_TEXT.lower()
        assert "return to default mode" in PLAN_MENU_TEXT
