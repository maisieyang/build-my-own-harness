"""End-to-end smoke for Phase 4 compaction via the ``oh ask`` CLI — P4-T4.4c.

The whole stack:CLI flag → Settings → ``_run_ask`` → hook_registry
registration → ``run_query`` → ``_dispatch_one`` → real ``Bash`` tool
producing oversized output → ``PostToolUse`` chain runs
``TruncateToolResultHook`` → next turn's API request carries the
truncated string in its ``tool_result``.

Mocked seams (just 2 — same pattern as P3-T5 observability smoke):

- ``_build_client`` → multi-turn stub providing a tool_use turn then end_turn
- ``create_default_tool_registry`` → registry holding a single fake tool
  whose output is intentionally large

The smoke asserts the observable contract:

1. ``tool_truncated`` log event fires on the oversized tool turn (the
   trace consumer sees truncation happen).
2. The second turn's request to the LLM carries a ``ToolResultBlock``
   whose content is **shorter than the original** (Layer 1 fired before
   messages were appended).
3. ``--no-auto-truncate`` reverses: no log, full content reaches the
   request.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock, ToolResultBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.tools import ToolRegistry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# ~50k chars → ~12.5k tokens with byte-ratio fallback → ~16k after
# Phase 11's 4/3 padding. Sized to:
# - Still trigger Phase 4's L1 truncation (default cap_tokens=10_000)
# - Stay UNDER Phase 11's L0 threshold (qwen-plus 32k * 0.83 = 26.5k)
#   so the new L2 context-collapse doesn't fire and contaminate L1
#   contract tests. T5 will add --no-auto-compact for the right way
#   to opt out of L2-L4.
_BIG_OUTPUT = "x" * 50_000


class _BigInput(BaseModel):
    value: str = "hello"


class _BigOutputTool(BaseTool[_BigInput]):
    """Fake tool that ignores its input and returns a huge string —
    forces Layer 1 truncation to fire."""

    name = "BigOutput"
    description = "Returns a deliberately oversized string."
    input_model = _BigInput

    async def execute(
        self,
        args: _BigInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(output=_BIG_OUTPUT)


class _MultiTurnStubClient:
    """Yields a tool_use turn then an end_turn turn. Captures requests so
    tests can inspect ``messages`` after each call."""

    def __init__(self, events_per_turn: list[list[ApiStreamEvent]]) -> None:
        self._events_per_turn = events_per_turn
        self._turn = 0
        self.captured_requests: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.captured_requests.append(request)
        events = self._events_per_turn[self._turn]
        self._turn += 1
        for event in events:
            yield event


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _big_tool_use_complete() -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_big",
                        "name": "BigOutput",
                        "input": {"value": "smoke"},
                    }
                ],
            },
            "usage": {"input_tokens": 5, "output_tokens": 5},
            "stop_reason": "tool_use",
        }
    )


def _end_turn_complete(text: str = "done") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent(
        message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
        usage=UsageSnapshot(input_tokens=5, output_tokens=2),
        stop_reason="end_turn",
    )


def _big_tool_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_BigOutputTool())
    return r


def _extract_tool_result_content(req: ApiMessageRequest) -> str:
    """Find the ToolResultBlock in the request's messages and return its content."""
    for msg in req.messages:
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                return block.content
    raise AssertionError("no ToolResultBlock found in request messages")


# --------------------------------------------------------------------------- #
# The smokes                                                                  #
# --------------------------------------------------------------------------- #


class TestCompactionE2E:
    def test_default_truncates_big_tool_output_inline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default flags — Layer 1 fires, log emitted, turn-2 request
        carries the truncated string."""
        _set_minimum_env(monkeypatch)
        stub = _MultiTurnStubClient([[_big_tool_use_complete()], [_end_turn_complete("done")]])
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
        monkeypatch.setattr(cli_module, "create_default_tool_registry", _big_tool_registry)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "smoke", "--auto", "--log-level", "INFO", "--log-format", "json"],
        )
        assert result.exit_code == 0, result.stderr

        # Parse stderr JSONL — must contain a tool_truncated event.
        log_lines: list[dict[str, Any]] = [
            json.loads(line) for line in result.stderr.splitlines() if line.strip().startswith("{")
        ]
        trunc_events = [e for e in log_lines if e["event"] == "tool_truncated"]
        assert len(trunc_events) == 1
        e = trunc_events[0]
        assert e["tool_use_id"] == "toolu_big"
        assert e["tool_name"] == "BigOutput"
        assert e["cap_tokens"] == 10_000  # default

        # Turn 2 request must carry truncated content, not full _BIG_OUTPUT.
        assert len(stub.captured_requests) == 2
        truncated_content = _extract_tool_result_content(stub.captured_requests[1])
        assert "[truncated" in truncated_content
        assert len(truncated_content) < len(_BIG_OUTPUT)
        # Confirm "x" * 80_000 itself does not appear anywhere in the request.
        assert _BIG_OUTPUT not in truncated_content

    def test_no_auto_truncate_passes_full_output_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--no-auto-truncate: hook not registered, full content reaches turn 2."""
        _set_minimum_env(monkeypatch)
        stub = _MultiTurnStubClient([[_big_tool_use_complete()], [_end_turn_complete("done")]])
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
        monkeypatch.setattr(cli_module, "create_default_tool_registry", _big_tool_registry)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "smoke",
                "--auto",
                "--log-level",
                "INFO",
                "--log-format",
                "json",
                "--no-auto-truncate",
            ],
        )
        assert result.exit_code == 0, result.stderr

        # No tool_truncated log event.
        log_lines = [
            json.loads(line) for line in result.stderr.splitlines() if line.strip().startswith("{")
        ]
        assert [e for e in log_lines if e["event"] == "tool_truncated"] == []

        # Turn 2 request carries the FULL big output.
        assert len(stub.captured_requests) == 2
        content = _extract_tool_result_content(stub.captured_requests[1])
        assert content == _BIG_OUTPUT  # untouched

    def test_custom_cap_via_flag_truncates_at_that_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--tool-result-cap 500 → much tighter truncation."""
        _set_minimum_env(monkeypatch)
        stub = _MultiTurnStubClient([[_big_tool_use_complete()], [_end_turn_complete("done")]])
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
        monkeypatch.setattr(cli_module, "create_default_tool_registry", _big_tool_registry)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "smoke",
                "--auto",
                "--log-level",
                "INFO",
                "--log-format",
                "json",
                "--tool-result-cap",
                "500",
            ],
        )
        assert result.exit_code == 0, result.stderr

        log_lines = [
            json.loads(line) for line in result.stderr.splitlines() if line.strip().startswith("{")
        ]
        trunc = next(e for e in log_lines if e["event"] == "tool_truncated")
        assert trunc["cap_tokens"] == 500
        # Truncated content much shorter than at default cap.
        truncated = _extract_tool_result_content(stub.captured_requests[1])
        # 500 tokens ≈ 500-2000 chars depending on tokenizer;definitely < 5000.
        assert len(truncated) < 5000
