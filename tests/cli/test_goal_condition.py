"""loop-runtime L3' (soft gate) — ``--goal-condition`` CLI flag + validation
+ single-run wiring.

Two layers: T3 (flag + validation, mirrors ``--verify``'s own validation
tests in ``test_repair_loop.py``) and the ``_run_ask`` wiring that dispatches
a single (non-repair-loop) run's json branch to the semantic judge instead
of the command gate. The repair *loop* driving multiple attempts off this
gate is exercised separately in ``test_repair_loop.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import openharness.cli as cli_module
from engine.conftest import _StubApiClient
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


class _RecordingStubClient:
    def __init__(self, events: list[ApiStreamEvent]) -> None:
        self._events = events

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        for event in self._events:
            yield event


def _hello_world_events(text: str = "hello") -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            stop_reason="end_turn",
        ),
    ]


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class TestGoalConditionRequiresPrintMode:
    def test_goal_condition_without_print_mode_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "--goal-condition", "the README mentions the new feature", "go"],
        )

        assert result.exit_code == 2
        assert "--goal-condition" in result.stderr
        assert "print mode" in result.stderr


class TestGoalConditionRequiresJsonOutput:
    """Unlike --verify, --goal-condition is restricted to --output-format
    json (not stream-json) for this vertical slice -- the semantic gate
    is only wired into the json result path (see _run_ask), so accepting
    stream-json here would silently no-op rather than run the judge."""

    def test_goal_condition_with_text_output_format_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--goal-condition", "condition text", "go"],
        )

        assert result.exit_code == 2
        assert "--goal-condition" in result.stderr
        assert "json" in result.stderr

    def test_goal_condition_with_stream_json_output_format_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "stream-json",
                "--goal-condition",
                "condition text",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "--goal-condition" in result.stderr
        assert "json" in result.stderr


class TestGoalConditionAndVerifyMutuallyExclusive:
    def test_both_gates_together_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "json",
                "--verify",
                "true",
                "--goal-condition",
                "condition text",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr


class TestMaxIterRequiresVerifyOrGoalCondition:
    def test_max_iter_without_either_gate_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "json", "--max-iter", "2", "go"],
        )

        assert result.exit_code == 2
        assert "--verify" in result.stderr
        assert "--goal-condition" in result.stderr


class TestGoalConditionSingleRunWiring:
    """A single run (no --max-iter repair loop) with --goal-condition must
    dispatch its json branch's gate call to the semantic judge, not the
    (unset) command gate -- proving the _run_ask wiring itself, independent
    of the repair loop's retry behavior (covered in test_repair_loop.py)."""

    def test_verification_reflects_judge_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        # Turn 1: the main assistant response. Turn 2: the judge's call
        # (made by maybe_run_semantic_verification via summarize()) -- same
        # stream_message interface, so one stub client serves both.
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events("assistant did the work"),
                _hello_world_events('{"score": 1, "reason": "condition satisfied"}'),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "json",
                "--goal-condition",
                "the assistant did the work",
                "go",
            ],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["verification"]["passed"] is True
        assert obj["verification"]["feedback"] == "condition satisfied"
        assert obj["verification"]["steps"] == []
        assert len(stub.captured_requests) == 2

    def test_judge_failure_surfaces_in_verification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events("assistant did something unrelated"),
                _hello_world_events('{"score": 0, "reason": "condition not met"}'),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        _set_minimum_env(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "json",
                "--goal-condition",
                "the assistant did the work",
                "go",
            ],
        )

        assert result.exit_code == 0, result.stderr  # single run: gate failure alone isn't non-zero
        obj = json.loads(result.stdout)
        assert obj["verification"]["passed"] is False
        assert obj["verification"]["feedback"] == "condition not met"


class TestGoalConditionRepairLoopPassesOnSecondAttempt:
    """--goal-condition counterpart to test_repair_loop.py's
    TestRepairLoopPassesOnSecondAttempt: a judge that fails once then passes
    proves the repair loop actually re-runs and re-judges, not just L3's
    hard gate. Each attempt costs two stub calls (main turn, then the judge's
    own summarize() call), so attempt N and N+1 land at indices 2N and 2N+1."""

    def test_fails_first_then_passes_second_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events("attempt 1"),
                _hello_world_events('{"score": 0, "reason": "condition not yet met"}'),
                _hello_world_events("attempt 2"),
                _hello_world_events('{"score": 1, "reason": "condition satisfied"}'),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "json",
                "--goal-condition",
                "the goal is met",
                "--max-iter",
                "3",
                "goal text",
            ],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["attempts"] == 2
        assert obj["verification"]["passed"] is True
        assert obj["verification"]["feedback"] == "condition satisfied"
        assert len(stub.captured_requests) == 4


class TestGoalConditionRepairLoopCapHit:
    def test_always_failing_judge_stops_at_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events("try 1"),
                _hello_world_events('{"score": 0, "reason": "still not met"}'),
                _hello_world_events("try 2"),
                _hello_world_events('{"score": 0, "reason": "still not met"}'),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "json",
                "--goal-condition",
                "the goal is met",
                "--max-iter",
                "2",
                "goal text",
            ],
        )

        assert result.exit_code != 0
        obj = json.loads(result.stdout)
        assert obj["attempts"] == 2
        assert obj["verification"]["passed"] is False
        assert len(stub.captured_requests) == 4


class TestGoalConditionRepairPromptThreadsJudgeReasonIntoNextAttempt:
    def test_second_attempt_prompt_includes_judge_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events("try 1"),
                _hello_world_events('{"score": 0, "reason": "UNIQUE_JUDGE_REASON_MARKER"}'),
                _hello_world_events("try 2"),
                _hello_world_events('{"score": 1, "reason": "done"}'),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "json",
                "--goal-condition",
                "the goal is met",
                "--max-iter",
                "3",
                "the original goal",
            ],
        )

        assert len(stub.captured_requests) == 4
        first_prompt = stub.captured_requests[0].messages[-1].content[0].text
        second_main_prompt = stub.captured_requests[2].messages[-1].content[0].text
        assert first_prompt == "the original goal"
        assert "the original goal" in second_main_prompt
        assert "UNIQUE_JUDGE_REASON_MARKER" in second_main_prompt
        assert "attempt 2" in second_main_prompt


class TestMutualExclusionEnforcedInternally:
    """Review finding: --verify/--goal-condition mutual exclusion was
    CLI-validation-only -- a direct internal caller bypassing ask()'s
    validation with both set would silently short-circuit the command gate.
    _run_ask and _run_repair_loop must each guard their own precondition."""

    async def test_run_ask_rejects_both_gates_set(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            await cli_module._run_ask(
                "goal",
                model_override=None,
                max_tokens=8192,
                permission_mode_override=None,
                log_level_override=None,
                log_format_override=None,
                tool_result_cap_override=None,
                auto_truncate_override=None,
                verify=["true"],
                goal_condition="some condition",
            )

    async def test_run_repair_loop_rejects_both_gates_set(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            await cli_module._run_repair_loop(
                "goal",
                max_iter=3,
                verify=["true"],
                verify_timeout=600.0,
                goal_condition="some condition",
            )


class _SlowJudgeStubClient:
    """First call (main turn) returns immediately; second call (the judge's,
    made via summarize()) sleeps past a short --goal-condition-timeout --
    proves that flag (not the unrelated --verify-timeout) bounds the judge."""

    def __init__(self, sleep_seconds: float) -> None:
        self._sleep_seconds = sleep_seconds
        self._call_count = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self._call_count += 1
        if self._call_count == 1:
            for event in _hello_world_events("done"):
                yield event
            return
        await asyncio.sleep(self._sleep_seconds)
        for event in _hello_world_events('{"score": 1, "reason": "n/a"}'):
            yield event


class TestGoalConditionTimeoutIsIndependentFromVerifyTimeout:
    def test_goal_condition_timeout_bounds_the_judge_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _SlowJudgeStubClient(sleep_seconds=0.3)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--output-format",
                "json",
                "--goal-condition",
                "the goal is met",
                "--goal-condition-timeout",
                "0.05",
                "--verify-timeout",
                "600",
                "go",
            ],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["verification"]["passed"] is False
        feedback = obj["verification"]["feedback"].lower()
        assert "timeout" in feedback or "timeouterror" in feedback
