"""loop-runtime L2 · review-fix [1] — headless fail-closed must be WIRED (RED).

The L2 unit tests construct ``TierBasedPermissionChecker(..., headless=True)``
directly, so they never exercised the CLI wiring. The high-effort code review
found the central guarantee inert: `oh -p` builds the checker with the default
``headless=False`` (cli.py:833 passes no ``headless=``), so a mutating tool with
no allow rule is ALLOWED in production despite the plan promising fail-closed.

These end-to-end tests drive the real ``ask`` command via CliRunner (only the
API client is stubbed) and assert through ``--output-format stream-json`` —
``-p`` text mode prints only the final turn's text, but stream-json emits every
``tool_completed`` event, so the permission-denied tool result is observable.

Should be RED now: [1] (no headless wiring) + [5] (permissions env crashes load).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from engine.conftest import _StubApiClient
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    import pytest

    from openharness.protocols.stream_events import ApiStreamEvent


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _write_tool_use_turn(path: str, tool_use_id: str = "t1") -> ApiMessageCompleteEvent:
    """Turn-1: the LLM picks Write on an in-cwd path (a mutating tool)."""
    return ApiMessageCompleteEvent(
        message=ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    id=tool_use_id,
                    name="Write",
                    input={"path": path, "content": "x"},
                )
            ],
        ),
        usage=UsageSnapshot(input_tokens=20, output_tokens=10),
        stop_reason="tool_use",
    )


def _finish_turn(text: str) -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=30, output_tokens=8),
            stop_reason="end_turn",
        ),
    ]


def _tool_completed_lines(stdout: str) -> list[dict]:
    lines = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    return [ev for ev in lines if ev.get("type") == "tool_completed"]


class TestPrintModeFailClosedWired:
    def test_headless_denies_mutating_without_allow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The central L2 guarantee: `oh -p` + mutating Write + no allow rule
        # → the tool is DENIED (fail-closed), surfaced as a tool_completed
        # error carrying the fail-closed reason.
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                [_write_tool_use_turn(path="notes.txt")],
                _finish_turn("Understood — blocked."),
            ],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli_module.app,
                ["ask", "-p", "--output-format", "stream-json", "write notes.txt"],
            )

        assert result.exit_code == 0, result.stderr
        completed = _tool_completed_lines(result.stdout)
        assert completed, "expected a tool_completed event in stream-json output"
        assert any(ev["is_error"] and "fail-closed" in ev["output"] for ev in completed), (
            f"expected a fail-closed denial, got: {completed}"
        )

    def test_headless_allows_mutating_with_allow_rule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With an explicit allow rule (set via the documented env var, which
        # must parse — exercises [5] too), the same Write is permitted.
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_PERMISSIONS__ALLOW", "Write(*)")
        stub = _StubApiClient(
            events_per_turn=[
                [_write_tool_use_turn(path="notes.txt")],
                _finish_turn("Wrote it."),
            ],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli_module.app,
                ["ask", "-p", "--output-format", "stream-json", "write notes.txt"],
            )

        assert result.exit_code == 0, result.stderr
        completed = _tool_completed_lines(result.stdout)
        assert completed, "expected a tool_completed event in stream-json output"
        assert all(not ev["is_error"] for ev in completed), (
            f"allow rule should permit the Write, got error: {completed}"
        )
