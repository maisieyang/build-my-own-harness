"""End-to-end mock integration — P2-T6.6f.

Verifies the full Phase 2 chain in one test fixture: cli.py constructs a
real :class:`QueryContext` from real Settings + real
:func:`create_default_tool_registry` + real :class:`DenyListChecker`,
then drives :func:`run_query`. The only fake is the API client (so we
don't burn quota), but every other layer runs:

- The 5 base tools are registered and reachable.
- ``Bash`` actually subprocesses commands.
- ``DenyListChecker`` actually rejects dangerous patterns.
- ``--dry-run`` actually short-circuits.
- ``_stream_render`` actually paints the output.

Three scenarios exercised:

1. **Happy path**: LLM picks Bash, real subprocess runs, output flows back.
2. **Permission denied**: LLM picks Bash with ``rm -rf /``, deny-list
   rejects it before subprocess; LLM sees the rejection.
3. **Dry-run**: LLM picks Bash with any command; ``--dry-run`` emits
   "would call" without subprocessing.
"""

from __future__ import annotations

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


def _bash_tool_use_turn(command: str, tool_use_id: str = "t1") -> ApiMessageCompleteEvent:
    """Turn-1 message_complete: the LLM picks Bash with ``command``."""
    return ApiMessageCompleteEvent(
        message=ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    id=tool_use_id,
                    name="Bash",
                    input={"command": command},
                )
            ],
        ),
        usage=UsageSnapshot(input_tokens=20, output_tokens=10),
        stop_reason="tool_use",
    )


def _finish_turn(text: str) -> list[ApiStreamEvent]:
    """Turn-2 events: the LLM writes a closing text + end_turn."""
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=30, output_tokens=8),
            stop_reason="end_turn",
        ),
    ]


class TestLoopIntegrationHappyPath:
    """Bash actually runs, output flows back, the LLM's final text appears."""

    def test_real_bash_executes_and_output_renders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                [_bash_tool_use_turn(command="echo integration-test-marker")],
                _finish_turn("Done."),
            ],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "run that command"])

        assert result.exit_code == 0, result.stderr
        # The [Bash] tool prefix lines from _stream_render.
        assert "[Bash] command='echo integration-test-marker'" in result.stdout
        # Real subprocess output flows into the rendered "[Bash] →" line.
        assert "integration-test-marker" in result.stdout
        # LLM's turn-2 response.
        assert "Done." in result.stdout


class TestLoopIntegrationDenyList:
    """DenyListChecker rejects catastrophic commands; loop continues."""

    def test_dangerous_bash_command_rejected_with_permission_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[
                [_bash_tool_use_turn(command="rm -rf /")],
                _finish_turn("OK, I won't do that."),
            ],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "delete everything"])

        assert result.exit_code == 0, result.stderr
        # Loop emitted ToolExecutionCompleted with is_error=True; render
        # painted the [Bash error] marker + the descriptive deny message.
        assert "[Bash error]" in result.stdout
        assert "action denied" in result.stdout
        assert "Bash pattern" in result.stdout
        # LLM's recovery text still appears -- loop didn't crash.
        assert "OK, I won't do that." in result.stdout


class TestLoopIntegrationDryRun:
    """--dry-run skips real execution and emits 'would call' events."""

    def test_dry_run_skips_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        # Use a command that WOULD have visible side effects if it ran.
        # Under --dry-run no subprocess is spawned, so we never see the
        # would-be marker in the stdout.
        stub = _StubApiClient(
            events_per_turn=[
                [_bash_tool_use_turn(command="echo not-actually-printed")],
                _finish_turn("Done planning."),
            ],
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.headless_app,
            ["run", "plan it", "--dry-run"],
        )

        assert result.exit_code == 0, result.stderr
        # The render shows the synthetic completion produced by run_query
        # under DRY_RUN, not the subprocess output.
        assert "[Bash] → would call Bash" in result.stdout
        # And the LLM's planning text still flows through.
        assert "Done planning." in result.stdout
