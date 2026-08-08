"""``--max-turns`` — surfaced by the SWE-bench adapter (D40 M1 小批):
astropy__astropy-14182 died on the hard-fixed 20-turn cap while
``LoopLimitExceeded``'s message told the user to "raise --max-turns" —
a flag that did not exist. This wires the promised knob:
``oh ask --max-turns N`` → ``QueryContext.max_turns``.

Capture pattern mirrors test_run_ask_overrides.py (small per-file
helpers, no cross-file test imports).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click import unstyle
from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent

runner = CliRunner()

_COMMON_RUN_ASK_KWARGS: dict[str, object] = {
    "model_override": None,
    "max_tokens": 8192,
    "reviewer_posture_override": None,
    "execution_posture_override": None,
    "log_level_override": None,
    "log_format_override": None,
    "tool_result_cap_override": None,
    "auto_truncate_override": None,
}


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class _StubClient:
    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class _CapturedContext:
    def __init__(self) -> None:
        self.context: object | None = None


def _patch_capture(monkeypatch: pytest.MonkeyPatch, captured: _CapturedContext) -> None:
    monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _StubClient())

    async def _capturing_run_query(
        initial_messages: list[ConversationMessage],
        context: object,
    ) -> AsyncIterator[ApiStreamEvent]:
        captured.context = context
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)


class TestRunAskMaxTurns:
    async def test_max_turns_reaches_query_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        captured = _CapturedContext()
        _patch_capture(monkeypatch, captured)

        await cli_module._run_ask("hi", max_turns=42, **_COMMON_RUN_ASK_KWARGS)

        assert captured.context.max_turns == 42  # type: ignore[attr-defined]

    async def test_default_stays_twenty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        captured = _CapturedContext()
        _patch_capture(monkeypatch, captured)

        await cli_module._run_ask("hi", **_COMMON_RUN_ASK_KWARGS)

        assert captured.context.max_turns == 20  # type: ignore[attr-defined]


class TestAskCliFlag:
    def test_flag_flows_from_cli_to_query_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        captured = _CapturedContext()
        _patch_capture(monkeypatch, captured)

        result = runner.invoke(cli_module.app, ["ask", "hi", "--max-turns", "42"])

        assert result.exit_code == 0
        assert captured.context.max_turns == 42  # type: ignore[attr-defined]

    def test_help_documents_the_flag(self) -> None:
        result = runner.invoke(cli_module.app, ["ask", "--help"])

        assert result.exit_code == 0
        assert "--max-turns" in unstyle(result.output)
