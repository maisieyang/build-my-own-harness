"""D48 T4 — ``oh chat`` /goal 续跑式条件循环接线集成 (RED).

驱动沿 test_chat_plan_mode 夹具;判官 stub 在 cli 命名空间 monkeypatch
(``cli_module.judge_goal_completion``),按调用次数出不同判决。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ConversationCompleteEvent,
    ToolExecutionCompletedEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.repl import (
    GOAL_FEEDBACK_PREFIX,
    GOAL_KICKOFF_PREFIX,
    build_goal_sentinel,
)
from openharness.services.goal_judge import GoalJudgeResult, GoalJudgeVerdict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.engine import QueryContext
    from openharness.protocols.stream_events import ApiStreamEvent


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


def _stub_input_sequence(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    iterator = iter(lines)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    import builtins

    monkeypatch.setattr(builtins, "input", _next)


def _install_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[list[ConversationMessage]]]:
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


def _install_judge(
    monkeypatch: pytest.MonkeyPatch, verdicts: list[tuple[bool, str]]
) -> list[tuple[str, str]]:
    """Stub the L3' judge; returns captured (condition, transcript) calls.
    Verdicts are consumed in order; the last one repeats."""
    calls: list[tuple[str, str]] = []

    async def _fake_judge(
        condition: str,
        transcript: str,
        *,
        api_client: object,
        model: str,
        timeout_seconds: float = 15.0,
    ) -> GoalJudgeResult:
        del api_client, model, timeout_seconds
        calls.append((condition, transcript))
        passed, feedback = verdicts[min(len(calls) - 1, len(verdicts) - 1)]
        return GoalJudgeResult(
            verdict=GoalJudgeVerdict.MET if passed else GoalJudgeVerdict.NOT_MET,
            reason=feedback,
        )

    monkeypatch.setattr(cli_module, "judge_goal_completion", _fake_judge)
    return calls


class TestGoalSetAndJudge:
    def test_set_kicks_off_immediately_and_met_clears(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D48.9(dogfood 修正):set 即开工 — 不需要任何后续用户输入,
        # kickoff turn 自动发起(CC "immediately start working" 指令同款).
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, messages = _install_capture(monkeypatch)
        judge_calls = _install_judge(monkeypatch, [(True, "evidence visible")])
        _stub_input_sequence(monkeypatch, ["/goal tests pass", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # 零人工输入即开工:kickoff turn 已跑,判官在其结束被调.
        assert len(contexts) == 1
        assert len(judge_calls) == 1
        condition, transcript = judge_calls[0]
        assert condition == "tests pass"
        assert "[assistant turn 1]" in transcript
        assert "goal met" in result.stdout
        # kickoff 消息以 goal 框架进 history,不回显为用户输入.
        kickoff_text = "".join(
            b.text for m in messages[0] for b in m.content if isinstance(b, TextBlock)
        )
        assert "[goal-status] set: tests pass" in kickoff_text
        assert GOAL_KICKOFF_PREFIX in kickoff_text
        assert f">>> {GOAL_KICKOFF_PREFIX}" not in result.stdout

    def test_goal_prompt_section_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        _install_judge(monkeypatch, [(True, "ok")])
        _stub_input_sequence(monkeypatch, ["/goal ship it", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "Session goal" in contexts[0].system_prompt
        assert "ship it" in contexts[0].system_prompt

    def test_judge_evidence_starts_at_current_goal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _install_capture(monkeypatch)
        judge_calls = _install_judge(monkeypatch, [(True, "ok")])
        _stub_input_sequence(
            monkeypatch,
            ["OLD_TASK_EVIDENCE", "/goal NEW_GOAL", "/exit"],
        )

        result = CliRunner().invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
        assert len(judge_calls) == 1
        condition, transcript = judge_calls[0]
        assert condition == "NEW_GOAL"
        assert "NEW_GOAL" in transcript
        assert "OLD_TASK_EVIDENCE" not in transcript


class TestAutoContinue:
    def test_not_met_auto_continues_with_checker_framing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, messages = _install_capture(monkeypatch)
        _install_judge(monkeypatch, [(False, "no test output yet"), (True, "done")])
        _stub_input_sequence(monkeypatch, ["/goal t", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # kickoff turn + 自动续 turn(共 2 次 run_query,全程零人工输入).
        assert len(contexts) == 2
        last = messages[1][-1]
        assert last.role == "user"
        text = "".join(b.text for b in last.content if isinstance(b, TextBlock))
        assert text.startswith(GOAL_FEEDBACK_PREFIX)
        assert "no test output yet" in text
        # D48.2:UI 显示 continuing 行,且不把反馈回显成用户输入.
        assert "goal not met" in result.stdout
        assert f">>> {GOAL_FEEDBACK_PREFIX}" not in result.stdout
        assert "goal met" in result.stdout

    def test_cap_pauses_and_manual_input_resets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_GOAL_MAX_AUTO_TURNS", "1")
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        _install_judge(monkeypatch, [(False, "still failing")])
        _stub_input_sequence(monkeypatch, ["/goal t", "again", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # kickoff→fail→续(1/1)→fail→暂停;人工 "again" 重置→续(1/1)→fail→暂停.
        assert len(contexts) == 4
        assert result.stdout.count("paused") == 2

    def test_judge_error_pauses_without_another_worker_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)

        async def _broken_judge(*args: object, **kwargs: object) -> GoalJudgeResult:
            del args, kwargs
            return GoalJudgeResult(
                verdict=GoalJudgeVerdict.ERROR,
                reason="provider timeout",
            )

        monkeypatch.setattr(cli_module, "judge_goal_completion", _broken_judge)
        _stub_input_sequence(monkeypatch, ["/goal t", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
        assert len(contexts) == 1
        assert "goal checker unavailable" in result.stdout
        assert "provider timeout" in result.stdout
        assert "goal not met — continuing" not in result.stdout

    def test_permission_confirmation_pauses_after_not_met_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts: list[QueryContext] = []

        async def _permission_blocked_run(
            initial_messages: list[ConversationMessage], context: QueryContext
        ) -> AsyncIterator[ApiStreamEvent]:
            contexts.append(context)
            blocker = "permission denied (requires confirmation): Bash runs arbitrary commands"
            yield ToolExecutionCompletedEvent(
                tool_use_id="t1",
                tool_name="Bash",
                output=blocker,
                is_error=True,
            )
            assistant = ConversationMessage(
                role="assistant", content=[TextBlock(text="I need permission to continue.")]
            )
            yield ApiMessageCompleteEvent(
                message=assistant,
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            yield ConversationCompleteEvent(messages=[*initial_messages, assistant])

        monkeypatch.setattr(cli_module, "run_query", _permission_blocked_run)
        judge_calls = _install_judge(monkeypatch, [(False, "verification command was not run")])
        _stub_input_sequence(monkeypatch, ["/goal tests pass", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
        assert len(contexts) == 1
        assert "goal blocked on permission" in result.stdout
        assert "Bash runs arbitrary commands" in result.stdout
        assert "goal not met — continuing" not in result.stdout
        assert judge_calls == []


class TestGoalCommandSurface:
    def test_bare_goal_shows_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _install_capture(monkeypatch)
        # kickoff turn 结束判官 fail → 续跑一轮后仍 fail…用大上限避免噪音:
        # 本测试只看裸 /goal 的状态输出,让判官第二轮就 pass 收敛.
        _install_judge(monkeypatch, [(False, "not yet"), (True, "ok")])
        _stub_input_sequence(monkeypatch, ["/goal", "/goal fix the bug", "/goal", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # 设定前:无 goal;达成清除后:又回到无 goal.
        assert result.stdout.count("no goal set") == 2
        assert "fix the bug" in result.stdout

    def test_clear_alias_stops_paused_goal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_GOAL_MAX_AUTO_TURNS", "1")
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        judge_calls = _install_judge(monkeypatch, [(False, "never")])
        # kickoff→fail→续(1/1)→fail→暂停 → /goal off 清除.
        _stub_input_sequence(monkeypatch, ["/goal t", "/goal off", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "goal cleared" in result.stdout
        assert len(contexts) == 2
        assert len(judge_calls) == 2
        # 清除后不再有判官调用或续 turn(/exit 前无新 turn).

    def test_conversation_clear_also_extinguishes_active_goal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_GOAL_MAX_AUTO_TURNS", "1")
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        judge_calls = _install_judge(monkeypatch, [(False, "never")])
        _stub_input_sequence(monkeypatch, ["/goal t", "/clear", "ordinary turn", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
        assert "conversation and active goal cleared" in result.stdout
        assert len(contexts) == 3  # kickoff + auto-continuation + ordinary turn
        assert len(judge_calls) == 2

    def test_plan_mode_turn_skips_judge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        contexts, _ = _install_capture(monkeypatch)
        judge_calls = _install_judge(monkeypatch, [(True, "ok")])
        # plan 态下设 goal:kickoff turn 在钳制内跑,turn 结束走审批菜单
        # ([3] 继续规划),判官不被调(D48.1 触发互斥).
        _stub_input_sequence(monkeypatch, ["/plan", "/goal t", "2", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert len(contexts) == 1  # kickoff 在 plan 态照样开工(钳制内探索)
        assert judge_calls == []


class TestGoalSentinelPersistence:
    """F17 (dogfood 2026-07-28) — 哨兵必须跨"内存→磁盘"这道缝.

    上面 ``TestGoalResume`` 两例喂的都是**手工构造**的 snapshot dict:
    它们证明 ``find_active_goal`` 认得 met/cleared 哨兵,却从没验证过这些
    哨兵**进不进得去** snapshot。真实缝在于:快照由引擎在 turn 结束写
    (``engine/query.py`` ``write_session_snapshot(final_messages)``),而
    判官与哨兵追加跑在引擎之外、turn 之后(``cli.py`` 判官段)——达成后
    直接退出,met 哨兵从未落盘,``--resume`` 复活已达成的 goal 并自动
    kickoff。这里的 ``run_query`` 替身照真引擎的时点落盘,把缝暴露出来。
    """

    @staticmethod
    def _install_capture_with_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.services.snapshot import write_session_snapshot

        async def _snapshotting_run_query(
            initial_messages: list[ConversationMessage], context: QueryContext
        ) -> AsyncIterator[ApiStreamEvent]:
            assistant = ConversationMessage(role="assistant", content=[TextBlock(text="reply")])
            final_messages = [*initial_messages, assistant]
            yield ApiMessageCompleteEvent(
                message=assistant,
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            # 引擎的落盘时点:turn 结束、返回给 REPL 之前.
            write_session_snapshot(
                cwd=context.cwd,
                tool_metadata={},
                messages=final_messages,
                context=context,
            )
            yield ConversationCompleteEvent(messages=final_messages)

        monkeypatch.setattr(cli_module, "run_query", _snapshotting_run_query)

    @staticmethod
    def _persisted_goal() -> str | None:
        """活跃 goal,按 ``--resume`` 会读到的那份磁盘快照判定。"""
        from pathlib import Path

        from openharness.repl import find_active_goal
        from openharness.services.snapshot import load_snapshot

        snapshot = load_snapshot(Path.cwd())
        messages = [ConversationMessage.model_validate(m) for m in snapshot["messages"]]
        return find_active_goal(messages)

    def test_met_sentinel_survives_to_disk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        self._install_capture_with_snapshot(monkeypatch)
        _install_judge(monkeypatch, [(True, "evidence visible")])
        # 达成即退出 —— 达成后没有第二个 turn 帮忙把哨兵捎上.
        _stub_input_sequence(monkeypatch, ["/goal tests pass", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "goal met" in result.stdout
        # 下次 --resume 读的就是这份:已达成的 goal 不能复活.
        assert self._persisted_goal() is None

    def test_cleared_sentinel_survives_to_disk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        self._install_capture_with_snapshot(monkeypatch)
        _install_judge(monkeypatch, [(False, "not yet")])
        _stub_input_sequence(monkeypatch, ["/goal tests pass", "/goal clear", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "goal cleared" in result.stdout
        assert self._persisted_goal() is None

    def test_active_goal_still_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 反向守卫:修 F17 不能把"活跃 goal 可恢复"(D48.7 本意)弄丢.
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_GOAL_MAX_AUTO_TURNS", "1")
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        self._install_capture_with_snapshot(monkeypatch)
        _install_judge(monkeypatch, [(False, "still failing")])
        # kickoff→fail→续(1/1)→fail→上限暂停,goal 仍活跃时退出.
        _stub_input_sequence(monkeypatch, ["/goal tests pass", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert self._persisted_goal() == "tests pass"


class TestGoalResume:
    def test_resume_restores_active_goal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _install_capture(monkeypatch)
        judge_calls = _install_judge(monkeypatch, [(True, "ok")])
        sentinel = build_goal_sentinel("set", "docs updated")
        monkeypatch.setattr(
            cli_module,
            "_load_resume_snapshot",
            lambda _cwd, resume_id=None: {
                "messages": [sentinel.model_dump()],
                "created_at": "2026-07-24T00:00:00Z",
                "git_head": None,
            },
        )
        _stub_input_sequence(monkeypatch, ["keep going", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat", "--resume"])
        assert result.exit_code == 0
        assert "goal restored" in result.stdout
        # 恢复的 goal 在下一 turn 结束被判官接管.
        assert len(judge_calls) == 1
        assert judge_calls[0][0] == "docs updated"

    def test_resume_ignores_extinguished_goal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _install_capture(monkeypatch)
        judge_calls = _install_judge(monkeypatch, [(True, "ok")])
        history = [build_goal_sentinel("set", "x"), build_goal_sentinel("met", "x")]
        monkeypatch.setattr(
            cli_module,
            "_load_resume_snapshot",
            lambda _cwd, resume_id=None: {
                "messages": [m.model_dump() for m in history],
                "created_at": "2026-07-24T00:00:00Z",
                "git_head": None,
            },
        )
        _stub_input_sequence(monkeypatch, ["hello", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat", "--resume"])
        assert result.exit_code == 0
        assert "goal restored" not in result.stdout
        assert judge_calls == []
