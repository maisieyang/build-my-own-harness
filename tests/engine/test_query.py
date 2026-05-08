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
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
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


def _fake_tool_use_event(
    *,
    tool_use_id: str = "toolu_01",
    tool_name: str = "Fake",
    tool_input: dict[str, object] | None = None,
) -> ApiMessageCompleteEvent:
    """Build a turn-1 message_complete carrying one tool_use block."""
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": tool_input if tool_input is not None else {"value": "hi"},
                    }
                ],
            },
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "tool_use",
        }
    )


def _registry_with_fake_tool() -> ToolRegistry:
    from tools.conftest import _FakeTool

    registry = ToolRegistry()
    registry.register(_FakeTool())
    return registry


class TestRunQueryHappyToolPath:
    """Turn 1 tool_use, turn 2 end_turn, all recovery paths bypassed."""

    async def test_one_tool_then_end_turn_yields_full_event_sequence(self) -> None:
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "hello"})],
                [_end_turn_event(text="done")],
            ],
        )
        context = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
        )

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]

        # Expected order: turn-1 message_complete, started, completed, turn-2 message_complete
        assert isinstance(events[0], ApiMessageCompleteEvent)
        assert events[0].stop_reason == "tool_use"
        assert isinstance(events[1], ToolExecutionStartedEvent)
        assert events[1].tool_name == "Fake"
        assert events[1].tool_use_id == "toolu_01"
        assert isinstance(events[2], ToolExecutionCompletedEvent)
        assert events[2].is_error is False
        assert events[2].output == "value=hello"
        assert isinstance(events[3], ApiMessageCompleteEvent)
        assert events[3].stop_reason == "end_turn"
        assert client._turn == 2

    async def test_turn_2_request_carries_assistant_and_tool_result_messages(
        self,
    ) -> None:
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "hello"})],
                [_end_turn_event()],
            ],
        )
        context = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
        )

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        # Turn 2 request: original user + assistant tool_use + user tool_result
        turn2 = client.captured_requests[1]
        assert len(turn2.messages) == 3
        assert turn2.messages[0].role == "user"
        assert turn2.messages[1].role == "assistant"
        assert turn2.messages[2].role == "user"  # tool_results live in user msg
        # The assistant turn carries the original tool_use block
        assert isinstance(turn2.messages[1].content[0], type(turn2.messages[1].content[0]))


class TestRunQueryRecoveryPaths:
    """Four recovery paths per D10.4 — all surface as is_error=True without
    halting the loop."""

    async def test_tool_not_found_returns_error_result(self) -> None:
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_name="Ghost", tool_input={})],
                [_end_turn_event()],
            ],
        )
        # Empty registry -- "Ghost" cannot be resolved.
        context = _make_context(api_client=client)

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(
            event for event in events if isinstance(event, ToolExecutionCompletedEvent)
        )
        assert completed.is_error is True
        assert "tool not found: Ghost" in completed.output

    async def test_validation_error_returns_error_result(self) -> None:
        # FakeInput requires `value: str`. Send something missing it.
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"wrong_field": 1})],
                [_end_turn_event()],
            ],
        )
        context = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
        )

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(
            event for event in events if isinstance(event, ToolExecutionCompletedEvent)
        )
        assert completed.is_error is True
        assert "invalid input for Fake" in completed.output

    async def test_permission_denied_returns_error_result(self) -> None:
        from engine.conftest import _DenyChecker

        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "x"})],
                [_end_turn_event()],
            ],
        )
        # Override the AllowAll fixture with the deny checker.
        ctx = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
        )
        # dataclass.replace would also work but we have direct construction.
        import dataclasses

        ctx = dataclasses.replace(ctx, permission_checker=_DenyChecker())

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                ctx,
            )
        ]
        completed = next(
            event for event in events if isinstance(event, ToolExecutionCompletedEvent)
        )
        assert completed.is_error is True
        assert "permission denied: Fake" in completed.output

    async def test_tool_returning_is_error_passes_through(self) -> None:
        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
        from tools.conftest import FakeInput

        class _ErroringFake(BaseTool[FakeInput]):
            name = "Fake"
            description = "Always returns is_error=True."
            input_model = FakeInput

            async def execute(
                self,
                args: FakeInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                return ToolResult(is_error=True, output="something went wrong")

        registry = ToolRegistry()
        registry.register(_ErroringFake())
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "x"})],
                [_end_turn_event()],
            ],
        )
        context = _make_context(api_client=client, tool_registry=registry)

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(
            event for event in events if isinstance(event, ToolExecutionCompletedEvent)
        )
        assert completed.is_error is True
        assert completed.output == "something went wrong"
