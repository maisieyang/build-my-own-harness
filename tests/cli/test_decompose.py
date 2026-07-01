"""loop-runtime L5 — ``--decompose`` CLI flag + validation + orchestration.

T2: flag existence + validation only (mirrors --max-iter/--goal-condition's
own validation tests). T3 (the actual decompose-then-sequential-repair-loop
orchestration) is exercised separately below in this same file.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from engine.conftest import _StubApiClient
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

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


class TestDecomposeRequiresPrintMode:
    def test_decompose_without_print_mode_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "--decompose", "--verify", "true", "go"],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--decompose" in result.stderr


class TestDecomposeRequiresJsonOutput:
    def test_decompose_with_text_output_format_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--decompose", "--verify", "true", "go"],
        )

        assert result.exit_code == 2
        assert "--decompose" in result.stderr
        assert "json" in result.stderr


class TestDecomposeRequiresAGate:
    def test_decompose_without_verify_or_goal_condition_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--decompose", "--output-format", "json", "go"],
        )

        assert result.exit_code == 2
        assert "--verify" in result.stderr
        assert "--goal-condition" in result.stderr


class TestDecomposeRequiresNoResume:
    """Review fix: --decompose must reject --resume/--resume-id the same
    way --max-iter already does (fresh-context guarantee)."""

    def test_decompose_with_resume_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--decompose",
                "--verify",
                "true",
                "--output-format",
                "json",
                "--resume",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--decompose" in result.stderr
        assert "--resume" in result.stderr

    def test_decompose_with_resume_id_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--decompose",
                "--verify",
                "true",
                "--output-format",
                "json",
                "--resume-id",
                "some-session-id",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--decompose" in result.stderr
        assert "--resume" in result.stderr


class TestDecomposeOrchestration:
    """T3: decompose_goal runs once, then each sub-goal drives its own
    _run_repair_loop sequentially, sharing the parent's --verify/--max-iter."""

    def test_all_sub_goals_succeed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events('["step one", "step two", "step three"]'),  # decomposer
                _hello_world_events("did step one"),
                _hello_world_events("did step two"),
                _hello_world_events("did step three"),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--decompose",
                "--verify",
                "true",
                "--output-format",
                "json",
                "build the feature",
            ],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["decompose"]["sub_goals"] == ["step one", "step two", "step three"]
        assert len(obj["decompose"]["results"]) == 3
        assert all(r["succeeded"] for r in obj["decompose"]["results"])
        # Review fix: usage must be SUMMED across every sub-goal that ran,
        # not just the last one -- each _hello_world_events call reports
        # input_tokens=3/output_tokens=1, and there are 3 sub-goal runs
        # (the decomposer call itself doesn't count -- it isn't a
        # _run_repair_loop attempt).
        assert obj["usage"]["input_tokens"] == 3 * 3
        assert obj["usage"]["output_tokens"] == 1 * 3

    def test_decompose_failure_feedback_is_surfaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Review fix: decompose_result.feedback (the parse-failure reason)
        must appear in the emitted json, not just a generic stderr pointer."""
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[_hello_world_events("this is not valid json")],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--decompose",
                "--verify",
                "true",
                "--output-format",
                "json",
                "build the feature",
            ],
        )

        assert result.exit_code != 0
        obj = json.loads(result.stdout)
        assert obj["decompose"]["feedback"]
        assert "json" in obj["decompose"]["feedback"].lower()

    def test_fails_fast_on_first_failing_sub_goal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events('["step one", "step two", "step three"]'),  # decomposer
                _hello_world_events("did step one"),
                _hello_world_events("did step two"),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--decompose",
                "--verify",
                "false",
                "--output-format",
                "json",
                "build the feature",
            ],
        )

        assert result.exit_code != 0
        obj = json.loads(result.stdout)
        assert obj["decompose"]["sub_goals"] == ["step one", "step two", "step three"]
        assert len(obj["decompose"]["results"]) == 1
        assert obj["decompose"]["results"][0]["succeeded"] is False
        # only the decomposer call + one sub-goal attempt happened
        assert len(stub.captured_requests) == 2

    def test_decompose_failure_runs_no_sub_goals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[_hello_world_events("this is not valid json")],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--decompose",
                "--verify",
                "true",
                "--output-format",
                "json",
                "build the feature",
            ],
        )

        assert result.exit_code != 0
        obj = json.loads(result.stdout)
        assert obj["decompose"]["sub_goals"] == []
        assert obj["decompose"]["results"] == []
        assert len(stub.captured_requests) == 1
