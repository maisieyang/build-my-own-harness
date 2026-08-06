"""D48 T2 — repl.py goal 原语 (RED).

纯函数层:命令解析(set/show/clear+5 别名,CC 同款)、GoalState、姿态
prompt 模板、续跑消息(D48.2 判官反馈框架,不冒充用户)、transcript 哨兵
(D48.7 构造→识别 roundtrip)、状态栏 goal 标识。

这些测试现在应当 RED:goal 原语均未实现.
"""

from __future__ import annotations

import pytest

from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    ExternalToolPolicy,
    PermissionDelta,
    PermissionDeltaRequest,
    RuntimePermissionProfile,
)
from openharness.protocols.content import TextBlock, ToolResultBlock
from openharness.protocols.messages import ConversationMessage
from openharness.repl import (
    BUILTIN_SLASH_COMMANDS,
    GOAL_FEEDBACK_PREFIX,
    GoalState,
    build_goal_continuation,
    build_goal_kickoff,
    build_goal_sentinel,
    build_plan_approval_sentinel,
    find_active_goal,
    format_permissions_status,
    format_status_bar,
    goal_evidence_messages,
    goal_prompt_section,
    parse_goal_command,
)
from openharness.tools import ExecutionDomain, ExternalEffectSurface


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

    def test_plan_approval_sentinel_guides_goal_command_quality(self) -> None:
        msg = build_plan_approval_sentinel()
        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))

        assert "concrete /goal condition" in text
        assert "runnable verification commands" in text
        assert "stop bounds" in text

    def test_goal_kickoff_mentions_permission_blockers_without_temp_files(self) -> None:
        msg = build_goal_kickoff("tests pass")

        assert "tests pass" in msg
        assert "Bash is permission-denied" in msg
        assert "Do not create temporary files" in msg

    def test_goal_continuation_keeps_blocker_strategy(self) -> None:
        msg = build_goal_continuation("tests pass", "pytest was never run")

        assert "pytest was never run" in msg
        assert "Bash is permission-denied" in msg
        assert "Do not create temporary files" in msg


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

    def test_evidence_starts_at_latest_matching_set(self) -> None:
        old = ConversationMessage(role="user", content=[TextBlock(text="old evidence")])
        current = build_goal_sentinel("set", "new goal")
        reply = ConversationMessage(role="assistant", content=[TextBlock(text="new evidence")])

        evidence = goal_evidence_messages([old, current, reply], "new goal")

        assert evidence == [current, reply]

    def test_evidence_falls_back_for_legacy_or_compacted_history(self) -> None:
        history = [ConversationMessage(role="assistant", content=[TextBlock(text="summary")])]

        assert goal_evidence_messages(history, "active goal") == history

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

    def test_permissions_is_a_builtin_command(self) -> None:
        assert "/permissions" in [c.name for c in BUILTIN_SLASH_COMMANDS]


class TestPermissionStatus:
    def test_configured_intent_and_installed_facts_are_separate(self) -> None:
        profile = RuntimePermissionProfile(name="workspace")
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="seatbelt",
            backend_version="1",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
        )

        status = format_permissions_status(
            profile=profile,
            external_policy=profile.external_tools,
            boundary=boundary,
            tool_domains={ExecutionDomain.LOCAL_DATA: ("Bash", "Read")},
            external_surfaces={ExternalEffectSurface.MCP: ("Github.create_issue",)},
            mcp_server_postures={"Github": "sandbox=required, environment=minimal, trust=trusted"},
            trusted_control_status={
                "hooks": "enabled; trusted in-process authority",
                "plugins": "disabled",
            },
            legacy_mode="default",
        )

        assert "Configured intent" in status
        assert "workspace" in status
        assert profile.fingerprint[:12] in status
        assert "Installed facts" in status
        assert "seatbelt" in status
        assert "command" in status
        assert "Bash, Read" in status
        assert "mcp=ask" in status
        assert "mcp: ask; tools=Github.create_issue" in status
        assert "not covered by local sandbox" in status
        assert "web: ask; not registered; not covered by local sandbox" in status
        assert "browser: ask; not registered; not covered by local sandbox" in status
        assert "computer_use: ask; not registered; not covered by local sandbox" in status
        assert "sandbox=required, environment=minimal, trust=trusted" in status
        assert "Trusted control plane" in status
        assert "hooks: enabled; trusted in-process authority" in status
        assert "plugins: disabled" in status

    def test_legacy_runtime_does_not_claim_an_installed_boundary(self) -> None:
        status = format_permissions_status(
            profile=None,
            external_policy=ExternalToolPolicy(),
            boundary=None,
            tool_domains={},
            external_surfaces={},
            mcp_server_postures={},
            trusted_control_status={"hooks": "disabled", "plugins": "disabled"},
            legacy_mode="auto",
        )

        assert "canonical profile: not configured" in status
        assert "legacy mode: auto" in status
        assert "verified boundary: none" in status

    def test_parked_request_is_visible_after_returning_to_the_session(self) -> None:
        profile = RuntimePermissionProfile(name="workspace")
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="seatbelt",
            backend_version="1",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
            network_rules=("deny-all",),
        )
        request = PermissionDeltaRequest.create(
            tool_use_id="tool-1",
            tool_name="Bash",
            final_arguments={"command": "uv sync"},
            profile=profile,
            boundary=boundary,
            delta=PermissionDelta.network_domain("pypi.org"),
            crossing=BoundaryViolation(
                dimension="network.domain",
                requested="pypi.org:443",
                evidence="not allowed",
            ),
            data_sources=("sandbox-visible data",),
            data_destinations=("pypi.org:443",),
        )

        status = format_permissions_status(
            profile=profile,
            external_policy=profile.external_tools,
            boundary=boundary,
            tool_domains={},
            external_surfaces={},
            mcp_server_postures={},
            trusted_control_status={"hooks": "disabled", "plugins": "disabled"},
            legacy_mode="default",
            parked_request=request,
        )

        assert "Parked permission request" in status
        assert request.request_id in status
        assert 'final arguments: {"command":"uv sync"}' in status
        assert "network_domain=pypi.org" in status
        assert "sandbox-visible data -> pypi.org:443" in status
