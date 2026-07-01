"""loop-runtime L4 T3+T4 — ``--max-iter`` CLI flag + the repair loop itself.

T3: flag existence + validation only.
T4: the actual outer repair loop — fresh-context re-invocation of ``_run_ask``
per attempt, repair-prompt threading, cap-hit reporting, exit code.
"""

from __future__ import annotations

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


def _events_with_usage(text: str, *, input_tokens: int, output_tokens: int) -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=input_tokens, output_tokens=output_tokens),
            stop_reason="end_turn",
        ),
    ]


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class TestMaxIterRequiresVerify:
    def test_max_iter_without_verify_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestMaxIterRequiresJsonOutputFormat:
    def test_max_iter_with_text_output_format_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--verify", "true", "--max-iter", "2", "go"],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "json" in result.stderr

    def test_max_iter_with_stream_json_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--verify",
                "true",
                "--output-format",
                "stream-json",
                "--max-iter",
                "2",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "json" in result.stderr


class TestMaxIterDefaultIsBackwardCompatible:
    def test_default_max_iter_one_behaves_like_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Omitting --max-iter entirely must not change L3's existing
        single-shot --verify behavior (regression lock)."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "json", "--verify", "true", "go"],
        )

        assert result.exit_code == 0, result.stderr

    def test_explicit_max_iter_one_with_verify_and_json_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
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
                "--max-iter",
                "1",
                "go",
            ],
        )

        assert result.exit_code == 0, result.stderr


class TestRepairLoopPassesOnSecondAttempt:
    def test_fails_first_then_passes_second_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A verify command that's stateful across attempts (fails once, then
        passes) proves the loop actually re-runs and re-verifies rather than
        stopping after one shot."""
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                _hello_world_events("attempt 1"),
                _hello_world_events("attempt 2"),
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        with runner.isolated_filesystem():
            verify_cmd = (
                'python3 -c "'
                "import pathlib;"
                "p = pathlib.Path('counter.txt');"
                "n = int(p.read_text()) + 1 if p.exists() else 1;"
                "p.write_text(str(n));"
                "import sys; sys.exit(0 if n >= 2 else 1)"
                '"'
            )
            result = runner.invoke(
                cli_module.app,
                [
                    "ask",
                    "-p",
                    "--output-format",
                    "json",
                    "--verify",
                    verify_cmd,
                    "--max-iter",
                    "3",
                    "goal text",
                ],
            )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["attempts"] == 2
        assert obj["verification"]["passed"] is True
        assert len(stub.captured_requests) == 2


class TestRepairLoopCapHit:
    def test_always_failing_verify_stops_at_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[_hello_world_events("try 1"), _hello_world_events("try 2")]
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
                "--verify",
                "false",
                "--max-iter",
                "2",
                "goal text",
            ],
        )

        assert result.exit_code != 0
        obj = json.loads(result.stdout)
        assert obj["attempts"] == 2
        assert obj["verification"]["passed"] is False
        assert len(stub.captured_requests) == 2


class TestRepairPromptThreadsFailureIntoNextAttempt:
    def test_second_attempt_prompt_includes_first_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[_hello_world_events("try 1"), _hello_world_events("try 2")]
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
                "--verify",
                "false",
                "--max-iter",
                "2",
                "the original goal",
            ],
        )

        assert len(stub.captured_requests) == 2
        first_prompt = stub.captured_requests[0].messages[-1].content[0].text
        second_prompt = stub.captured_requests[1].messages[-1].content[0].text
        assert first_prompt == "the original goal"
        assert "the original goal" in second_prompt
        assert "false" in second_prompt
        assert "attempt 2" in second_prompt
        assert second_prompt != first_prompt


class TestMaxIterRejectsResume:
    """Review finding: --max-iter + --resume silently breaks the fresh-context
    guarantee (every attempt after the first would reload the prior attempt's
    own just-written snapshot). Must be rejected outright."""

    def test_max_iter_with_resume_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--verify",
                "true",
                "--output-format",
                "json",
                "--max-iter",
                "2",
                "--resume",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--resume" in result.stderr

    def test_max_iter_with_resume_id_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--verify",
                "true",
                "--output-format",
                "json",
                "--max-iter",
                "2",
                "--resume-id",
                "abc123",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--resume" in result.stderr


class TestRunRepairLoopRejectsEmptyVerify:
    """Review finding: _run_repair_loop is independently callable and its
    only guard against an empty ``verify`` was a bare ``assert`` deep inside
    the loop (stripped under -O, and only fires on attempt 2+). It must
    validate its own precondition at entry with a real exception."""

    async def test_empty_verify_raises_value_error_immediately(self) -> None:
        with pytest.raises(ValueError, match="non-empty verify"):
            await cli_module._run_repair_loop(
                "goal",
                max_iter=3,
                verify=[],
                verify_timeout=600.0,
                model_override=None,
                max_tokens=8192,
                permission_mode_override=None,
                log_level_override=None,
                log_format_override=None,
                tool_result_cap_override=None,
                auto_truncate_override=None,
                no_skills=False,
                no_commands=False,
                sandbox_override=None,
                sandbox_image_override=None,
                sandbox_network_override=None,
                sandbox_memory_override=None,
                sandbox_cpus_override=None,
                sandbox_runtime_override=None,
                enable_plugin_hooks_override=None,
                enable_plugins_override=None,
                enable_memory_override=None,
                enable_web_override=None,
                compact_threshold_override=None,
                no_auto_compact=False,
                resume=False,
                resume_id=None,
                llm_focus_state_override=None,
            )


class TestRepairLoopAggregatesUsageAcrossAttempts:
    """Review finding: the final json's usage/num_turns reflected only the
    last attempt, silently dropping tokens spent on earlier failed attempts."""

    def test_usage_summed_across_all_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                _events_with_usage("try 1", input_tokens=100, output_tokens=20),
                _events_with_usage("try 2", input_tokens=50, output_tokens=10),
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
                "--verify",
                "false",
                "--max-iter",
                "2",
                "goal text",
            ],
        )

        obj = json.loads(result.stdout)
        assert obj["usage"]["input_tokens"] == 150
        assert obj["usage"]["output_tokens"] == 30
        assert obj["usage"]["total_tokens"] == 180
        assert obj["num_turns"] == 2
