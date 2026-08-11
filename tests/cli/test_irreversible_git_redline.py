"""loop-runtime L8 — irreversible git action red line, end-to-end.

Mirrors ``test_print_mode_failclosed.py``'s style: drive the real ``ask``
command via CliRunner (only the API client is stubbed), assert through
``--output-format stream-json`` so the permission-denied tool_completed
event is observable. Even with an explicit ``Bash(*)`` allow rule AND
``--auto`` (AUTO permission mode), a Bash call to ``git commit``/``git
push`` must be denied.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from engine.conftest import _StubApiClient
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    import pytest

    from openharness.protocols.stream_events import ApiStreamEvent


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _git_commit_turn(tool_use_id: str = "t1") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent(
        message=ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    id=tool_use_id,
                    name="Bash",
                    input={"command": "git commit -m 'autonomous commit'"},
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


class TestIrreversibleGitRedlineEndToEnd:
    def test_git_commit_denied_even_with_bash_allow_and_auto(
        self,
        monkeypatch: pytest.MonkeyPatch,
        verified_seatbelt_backend: None,
    ) -> None:
        del verified_seatbelt_backend
        _set_minimum_env(monkeypatch)
        # The semantic red line must hold under the canonical workspace profile
        # and the exact auto reviewer.
        stub = _StubApiClient(
            events_per_turn=[
                [_git_commit_turn()],
                _finish_turn("Understood — blocked."),
            ],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli_module.headless_app,
                [
                    "run",
                    "-p",
                    "--auto",
                    "--sandbox",
                    "--sandbox-backend",
                    "seatbelt",
                    "--output-format",
                    "stream-json",
                    "commit my changes",
                ],
            )

        assert result.exit_code == 0, result.stderr
        completed = _tool_completed_lines(result.stdout)
        assert completed, "expected a tool_completed event in stream-json output"
        assert any(ev["is_error"] and "irreversible" in ev["output"].lower() for ev in completed), (
            f"expected an irreversible-action denial, got: {completed}"
        )
