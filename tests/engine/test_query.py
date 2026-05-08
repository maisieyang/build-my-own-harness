"""Tests for run_query — P2-T4.4d (no-tool path).

Verifies the loop's behavior when the LLM does not request any tools:

1. Async-generator function shape (caller iterates, doesn't await).
2. API events stream through transparently (text deltas, retries, terminal).
3. Loop exits cleanly on every non-tool ``stop_reason``
   (``end_turn`` / ``max_tokens`` / ``stop_sequence``).
4. ``ApiMessageRequest`` is built with the right fields from QueryContext.
5. Caller's ``initial_messages`` list is not mutated.
6. ``stop_reason == "tool_use"`` raises the explicit P2-T4.4e tripwire.

Subsequent sub-units (4e/4f) replace the tripwire with real tool dispatch.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from engine.conftest import _AllowAllChecker, _StubApiClient
from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.tools import ToolRegistry

if TYPE_CHECKING:
    from openharness.api import OpenAICompatibleApiClient
    from openharness.protocols import ApiStreamEvent


def _end_turn_event(text: str = "hi") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
    )


def _tool_use_event(tool_name: str = "Read") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01",
                        "name": tool_name,
                        "input": {"path": "x"},
                    }
                ],
            },
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "tool_use",
        }
    )


def _make_context(
    *,
    api_client: object,
    tool_registry: ToolRegistry | None = None,
) -> QueryContext:
    return QueryContext(
        api_client=cast("OpenAICompatibleApiClient", api_client),
        tool_registry=tool_registry if tool_registry is not None else ToolRegistry(),
        permission_checker=_AllowAllChecker(),
        system_prompt="you are a test harness",
        cwd=Path("/tmp"),
        model="qwen-plus",
        max_tokens=512,
    )


class TestRunQueryStructure:
    def test_run_query_is_async_generator_function(self) -> None:
        # Same as the P2-T1.1c stub test -- still true now that the body landed.
        assert inspect.isasyncgenfunction(run_query)


class TestRunQueryNoToolPath:
    async def test_yields_api_events_transparently_then_exits(self) -> None:
        events: list[ApiStreamEvent] = [
            ApiTextDeltaEvent(text="hel"),
            ApiTextDeltaEvent(text="lo"),
            _end_turn_event(text="hello"),
        ]
        client = _StubApiClient(events_per_turn=[events])
        context = _make_context(api_client=client)

        collected: list[ApiStreamEvent] = []
        async for event in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            collected.append(event)

        # Same events come out, in the same order.
        assert collected == events
        assert client._turn == 1  # one API call

    @pytest.mark.parametrize(
        "stop_reason",
        ["end_turn", "max_tokens", "stop_sequence"],
    )
    async def test_exits_clean_on_any_non_tool_stop_reason(self, stop_reason: str) -> None:
        complete = ApiMessageCompleteEvent.model_validate(
            {
                "message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": stop_reason,
            }
        )
        client = _StubApiClient(events_per_turn=[[complete]])
        context = _make_context(api_client=client)

        collected = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        assert len(collected) == 1
        # Single API turn, no exception raised.
        assert client._turn == 1


class TestRunQueryRequestShape:
    async def test_request_carries_model_max_tokens_system_messages(self) -> None:
        client = _StubApiClient(events_per_turn=[[_end_turn_event()]])
        context = _make_context(api_client=client)
        initial = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]

        async for _ in run_query(initial, context):
            pass

        assert len(client.captured_requests) == 1
        request = client.captured_requests[0]
        assert request.model == "qwen-plus"
        assert request.max_tokens == 512
        assert request.system == "you are a test harness"
        assert request.messages == initial

    async def test_request_tools_populated_when_registry_non_empty(self) -> None:
        from tools.conftest import _FakeTool

        registry = ToolRegistry()
        registry.register(_FakeTool())
        client = _StubApiClient(events_per_turn=[[_end_turn_event()]])
        context = _make_context(api_client=client, tool_registry=registry)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        request = client.captured_requests[0]
        assert request.tools is not None
        assert [t.name for t in request.tools] == ["Fake"]

    async def test_request_tools_is_none_when_registry_empty(self) -> None:
        # Empty list collapses to None so we don't send a useless `tools: []`
        # to providers that may reject it.
        client = _StubApiClient(events_per_turn=[[_end_turn_event()]])
        context = _make_context(api_client=client)  # default empty registry

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        assert client.captured_requests[0].tools is None


class TestRunQueryDefensiveCopy:
    async def test_initial_messages_not_mutated(self) -> None:
        original = [ConversationMessage(role="user", content=[TextBlock(text="prior")])]
        snapshot = list(original)
        client = _StubApiClient(events_per_turn=[[_end_turn_event()]])
        context = _make_context(api_client=client)

        async for _ in run_query(original, context):
            pass

        # Even if the loop appends internally (P2-T4.4e+), the caller's list
        # must remain identical to the pre-call snapshot.
        assert original == snapshot


class TestRunQueryToolUseStubMarker:
    """The tool-dispatch path is a P2-T4.4e tripwire."""

    async def test_tool_use_stop_reason_raises_4e_marker(self) -> None:
        client = _StubApiClient(events_per_turn=[[_tool_use_event()]])
        context = _make_context(api_client=client)

        with pytest.raises(NotImplementedError, match=r"P2-T4\.4e"):
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                pass
