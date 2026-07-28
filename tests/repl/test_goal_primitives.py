"""D48 T2 — repl.py goal 原语 (RED).

纯函数层:命令解析(set/show/clear+5 别名,CC 同款)、GoalState、姿态
prompt 模板、续跑消息(D48.2 判官反馈框架,不冒充用户)、transcript 哨兵
(D48.7 构造→识别 roundtrip)、状态栏 goal 标识。

这些测试现在应当 RED:goal 原语均未实现.
"""

from __future__ import annotations

import pytest

from openharness.protocols.content import TextBlock, ToolResultBlock
from openharness.protocols.messages import ConversationMessage
from openharness.repl import (
    BUILTIN_SLASH_COMMANDS,
    GOAL_FEEDBACK_PREFIX,
    GoalState,
    build_goal_continuation,
    build_goal_sentinel,
    find_active_goal,
    format_status_bar,
    goal_prompt_section,
    parse_goal_command,
)


class TestParseGoalCommand:
    def test_bare_goal_is_show(self) -> None:
        cmd = parse_goal_command("/goal")
        assert cmd.action == "show"
        assert cmd.condition is None

    @pytest.mark.parametrize("alias", ["clear", "stop", "off", "reset", "none", "cancel"])
    def test_clear_aliases(self, alias: str) -> None:
        assert parse_goal_command(f"/goal {alias}").action == "clear"

    def test_clear_alias_case_insensitive(self) -> None:
        assert parse_goal_command("/goal Clear").action == "clear"

    def test_set_with_condition(self) -> None:
        cmd = parse_goal_command("/goal all tests pass and lint is clean")
        assert cmd.action == "set"
        assert cmd.condition == "all tests pass and lint is clean"

    def test_condition_starting_with_alias_like_word_is_set(self) -> None:
        # "clearly ..." 不是 clear 别名 — 只有独词精确匹配才是 clear.
        cmd = parse_goal_command("/goal clearly document the API")
        assert cmd.action == "set"

    def test_whitespace_tolerated(self) -> None:
        cmd = parse_goal_command("  /goal   fix the bug  ")
        assert cmd.action == "set"
        assert cmd.condition == "fix the bug"


class TestGoalState:
    def test_defaults(self) -> None:
        state = GoalState(condition="tests pass", set_at=123.0, tokens_at_start=500)
        assert state.iterations == 0
        assert state.last_reason is None


class TestPromptAndContinuation:
    def test_prompt_section_embeds_condition(self) -> None:
        section = goal_prompt_section("the CHANGELOG has an entry")
        assert "the CHANGELOG has an entry" in section
        assert "goal" in section.lower()

    def test_continuation_frames_checker_not_user(self) -> None:
        msg = build_goal_continuation("tests pass", "pytest was never run")
        assert msg.startswith(GOAL_FEEDBACK_PREFIX)
        assert "pytest was never run" in msg
        assert "tests pass" in msg


class TestGoalSentinel:
    def test_set_sentinel_roundtrip(self) -> None:
        history = [build_goal_sentinel("set", "tests pass")]
        assert find_active_goal(history) == "tests pass"

    def test_met_extinguishes(self) -> None:
        history = [
            build_goal_sentinel("set", "tests pass"),
            build_goal_sentinel("met", "tests pass"),
        ]
        assert find_active_goal(history) is None

    def test_cleared_extinguishes(self) -> None:
        history = [
            build_goal_sentinel("set", "tests pass"),
            build_goal_sentinel("cleared", "tests pass"),
        ]
        assert find_active_goal(history) is None

    def test_latest_set_wins_after_met(self) -> None:
        history = [
            build_goal_sentinel("set", "old goal"),
            build_goal_sentinel("met", "old goal"),
            build_goal_sentinel("set", "new goal"),
        ]
        assert find_active_goal(history) == "new goal"

    def test_ordinary_messages_ignored(self) -> None:
        history = [
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="hi")]),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="t1", content="out", is_error=False)],
            ),
        ]
        assert find_active_goal(history) is None


class TestStatusBarGoal:
    def test_goal_active_shown(self) -> None:
        bar = format_status_bar(
            model="m",
            used_tokens=0,
            context_window=100_000,
            threshold_ratio=None,
            goal_active=True,
        )
        assert "goal" in bar

    def test_goal_inactive_omitted(self) -> None:
        bar = format_status_bar(
            model="m",
            used_tokens=0,
            context_window=100_000,
            threshold_ratio=None,
            goal_active=False,
        )
        assert "goal" not in bar


class TestSlashMenu:
    def test_goal_is_a_builtin_command(self) -> None:
        assert "/goal" in [c.name for c in BUILTIN_SLASH_COMMANDS]
