"""loop-runtime L3 T2+T3+T4 — ``--verify``/``--verify-timeout`` CLI flags.

T2: flag existence + validation + threading through ``_run_ask``.
T3: folding the verification result into ``--output-format json``'s
``result`` object as a ``verification`` key.
T4: same folding into ``--output-format stream-json``'s terminal ``result``
line, and confirming the mid-stream exception path is untouched (no
verification run, no extra crash — the ``error`` terminator still fires).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.api.errors import RequestFailure
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


class _RaisingStubClient:
    """Stub whose ``stream_message`` raises before yielding — drives the
    mid-stream exception / ``error``-terminator path."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]
        raise self._exc


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


class TestVerifyRequiresPrintMode:
    def test_verify_without_print_mode_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "--verify", "true", "go"])

        assert result.exit_code == 2
        assert "only applies in headless print mode" in result.stderr

    def test_verify_with_text_output_format_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--verify`` needs a structured output format to surface into
        (T3/T4) — plain ``text`` mode has nowhere to put the result."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app, ["ask", "-p", "--verify", "true", "--output-format", "text", "go"]
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--output-format json or stream-json" in result.stderr


class TestVerifyFlagAccepted:
    def test_repeatable_verify_flag_does_not_break_json_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2 scope: the flags parse and thread through without crashing.
        The ``verification`` key itself is T3's job — not asserted here."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("the answer"))
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
                "--verify",
                "false",
                "--verify-timeout",
                "5",
                "go",
            ],
        )

        assert result.exit_code == 0, result.stderr


class TestVerifyFoldedIntoJson:
    """T3: verification result folds into ``--output-format json``'s
    ``result`` object as a ``verification`` key."""

    def test_passing_verify_yields_passed_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "json", "--verify", "true", "go"],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["verification"]["passed"] is True
        assert len(obj["verification"]["steps"]) == 1
        assert obj["verification"]["steps"][0]["command"] == "true"
        assert obj["verification"]["feedback"]

    def test_failing_verify_yields_passed_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "json", "--verify", "false", "go"],
        )

        # Exit code stays run-level only (end_turn -> 0) — verification is a
        # pure JSON signal, it must NOT flip the process exit code.
        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["verification"]["passed"] is False

    def test_no_verify_flag_yields_null_verification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not requesting verification is distinct from requesting it and
        failing — the key must be ``null``, not a failed result."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "--output-format", "json", "go"])

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["verification"] is None

    def test_repeated_verify_flags_run_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_events = _hello_world_events("done")
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(stub_events)
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
                "--verify",
                "true",
                "go",
            ],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["verification"]["passed"] is True
        assert len(obj["verification"]["steps"]) == 2


class TestVerifyFoldedIntoStreamJson:
    """T4: same ``verification`` folding, but into stream-json's terminal
    ``result`` line (last ndjson line, ``type == "result"``)."""

    def test_passing_verify_yields_passed_true_in_terminal_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("streamed"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "stream-json", "--verify", "true", "go"],
        )

        assert result.exit_code == 0, result.stderr
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert lines[-1]["type"] == "result"
        assert lines[-1]["verification"]["passed"] is True
        assert len(lines[-1]["verification"]["steps"]) == 1

    def test_failing_verify_yields_passed_false_in_terminal_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("streamed"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "stream-json", "--verify", "false", "go"],
        )

        assert result.exit_code == 0, result.stderr
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert lines[-1]["type"] == "result"
        assert lines[-1]["verification"]["passed"] is False

    def test_no_verify_flag_yields_null_verification_in_terminal_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("streamed"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app, ["ask", "-p", "--output-format", "stream-json", "go"]
        )

        assert result.exit_code == 0, result.stderr
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert lines[-1]["type"] == "result"
        assert lines[-1]["verification"] is None

    def test_mid_stream_exception_still_terminates_with_error_and_skips_verification(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the agent run itself raises, verification must NOT run (nothing
        to verify) and the stream must still end with an ``error`` line —
        not hang, not crash on top of the original exception."""
        _set_minimum_env(monkeypatch)
        stub = _RaisingStubClient(RequestFailure("boom", status_code=500))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "stream-json", "--verify", "true", "go"],
        )

        assert result.exit_code != 0
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert lines[-1]["type"] == "error"


class TestEndToEnd:
    """T5: real multi-step fail-fast through the full CLI, and the locked
    exit-code decision (verification never flips the process exit code)."""

    def test_three_verify_flags_fail_fast_e2e(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli_module.app,
                [
                    "ask",
                    "-p",
                    "--output-format",
                    "json",
                    "--verify",
                    "true",
                    "--verify",
                    "false",
                    "--verify",
                    "python3 -c \"open('third-ran.txt', 'w').close()\"",
                    "go",
                ],
            )

            assert result.exit_code == 0, result.stderr
            obj = json.loads(result.stdout)
            assert obj["verification"]["passed"] is False
            assert len(obj["verification"]["steps"]) == 2
            import os

            assert not os.path.exists("third-ran.txt"), (
                "fail-fast must not run the third --verify step"
            )

    def test_verification_failure_never_flips_exit_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Locked decision (confirmed via AskUserQuestion during /plan):
        exit code stays a pure run-level signal (end_turn -> 0). A failed
        verification is JSON-only — L4 reads ``verification.passed`` itself,
        it is not smuggled into the exit code."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events("done"))
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--output-format", "json", "--verify", "false", "go"],
        )

        assert result.exit_code == 0, "stop_reason=end_turn must exit 0 regardless of verify"
        obj = json.loads(result.stdout)
        assert obj["stop_reason"] == "end_turn"
        assert obj["verification"]["passed"] is False
