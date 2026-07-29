"""plan-mode D47 — REPL 层纯单元 (RED).

repl.py 的设计契约是"unit-testable without a TTY"——plan 模式的状态机
原语(ChatMode / 菜单选项解析 / 权限 overlay / 状态栏 mode 标识)全部
落在这层,cli._run_chat 只做接线.

这些测试现在应当 RED:ChatMode / PlanMenuChoice / overlay 均未实现.
"""

from __future__ import annotations

from openharness.permissions.rules import (
    PermissionRules,
    plan_mode_preset,
)
from openharness.repl import (
    BUILTIN_SLASH_COMMANDS,
    PLAN_MENU_TEXT,
    ChatMode,
    PlanMenuChoice,
    format_status_bar,
    overlay_plan_permissions,
    parse_plan_menu_choice,
)


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


class TestOverlayPlanPermissions:
    def test_plan_mode_appends_deny_preset(self) -> None:
        base = PermissionRules(allow=("Bash(git status:*)",))
        result = overlay_plan_permissions(base, mode=ChatMode.PLAN, grant=None)
        for rule in plan_mode_preset():
            assert rule in result.deny
        # 既有规则保留 — overlay 是叠加不是替换.
        assert "Bash(git status:*)" in result.allow

    def test_approve_grant_is_identity(self) -> None:
        base = PermissionRules()
        result = overlay_plan_permissions(base, mode=ChatMode.DEFAULT, grant=PlanMenuChoice.APPROVE)
        assert result is base

    def test_manual_grant_is_identity(self) -> None:
        base = PermissionRules(allow=("Read(*)",))
        result = overlay_plan_permissions(base, mode=ChatMode.DEFAULT, grant=PlanMenuChoice.APPROVE)
        assert result is base

    def test_default_no_grant_is_identity(self) -> None:
        base = PermissionRules()
        assert overlay_plan_permissions(base, mode=ChatMode.DEFAULT, grant=None) is base


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
