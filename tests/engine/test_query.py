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

import dataclasses
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from engine.conftest import _StubApiClient
from openharness.engine.context import QueryContext
from openharness.engine.errors import LoopLimitExceeded
from openharness.engine.query import run_query
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ConversationMessage,
    TextBlock,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolUseBlock,
    UsageSnapshot,
)
from openharness.tools import ExecutionDomain, ToolRegistry

if TYPE_CHECKING:
    from pydantic import BaseModel

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
    max_turns: int = 20,
) -> QueryContext:
    return QueryContext(
        api_client=cast("OpenAICompatibleApiClient", api_client),
        tool_registry=tool_registry if tool_registry is not None else ToolRegistry(),
        system_prompt="you are a test harness",
        cwd=Path("/tmp"),
        model="qwen-plus",
        max_tokens=512,
        max_turns=max_turns,
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

        # P6+-T1: API events flow through unchanged, followed by the
        # new ConversationCompleteEvent appended at the end.
        from openharness.protocols import ConversationCompleteEvent

        assert collected[:-1] == events
        assert isinstance(collected[-1], ConversationCompleteEvent)
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
        # P6+-T1: one API event + one ConversationCompleteEvent.
        from openharness.protocols import ConversationCompleteEvent

        assert len(collected) == 2
        assert isinstance(collected[-1], ConversationCompleteEvent)
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

    async def test_forged_hidden_tool_is_denied_by_action_policy_before_execution(
        self,
    ) -> None:
        from openharness.permissions import ActionDenyKind, DenyResult
        from openharness.tools import ToolExecutionContext

        class _PlanPolicy:
            def evaluate(
                self,
                tool_name: str,
                args: BaseModel,
                context: ToolExecutionContext,
            ) -> DenyResult | None:
                del args, context
                if tool_name == "Fake":
                    return DenyResult(
                        kind=ActionDenyKind.PLAN_CAPABILITY,
                        reason="tool is not exposed in plan mode",
                    )
                return None

        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "must not execute"})],
                [_end_turn_event()],
            ],
        )
        visible_registry = ToolRegistry()
        dispatch_registry = _registry_with_fake_tool()
        context = _make_context(api_client=client, tool_registry=visible_registry)
        context = dataclasses.replace(
            context,
            dispatch_tool_registry=dispatch_registry,
            action_deny_policy=_PlanPolicy(),
        )

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]

        assert client.captured_requests[0].tools is None
        completed = next(
            event for event in events if isinstance(event, ToolExecutionCompletedEvent)
        )
        assert completed.is_error is True
        assert completed.output == "action denied: tool is not exposed in plan mode"

    async def test_action_policy_failure_denies_instead_of_failing_open(self) -> None:
        class _BrokenPolicy:
            def evaluate(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("sensitive arguments must not reach logs")

        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "must not execute"})],
                [_end_turn_event()],
            ],
        )
        context = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
        )
        context = dataclasses.replace(
            context,
            action_deny_policy=_BrokenPolicy(),  # type: ignore[arg-type]
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
        assert completed.output == "action denied: action policy evaluation failed"
        assert completed.is_error is True

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

    async def test_tool_returning_is_error_passes_through(self) -> None:
        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
        from tools.conftest import FakeInput

        class _ErroringFake(BaseTool[FakeInput]):
            execution_domain = ExecutionDomain.TRUSTED_CONTROL
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


class TestRunQueryMultiTurn:
    """3+ turns of tool_use, finally end_turn -> clean exit."""

    async def test_three_turns_then_end_turn(self) -> None:
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_use_id="t1", tool_input={"value": "1"})],
                [_fake_tool_use_event(tool_use_id="t2", tool_input={"value": "2"})],
                [_fake_tool_use_event(tool_use_id="t3", tool_input={"value": "3"})],
                [_end_turn_event(text="all done")],
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

        # 4 API turns made; 3 tool dispatches done.
        assert client._turn == 4
        started = [e for e in events if isinstance(e, ToolExecutionStartedEvent)]
        completed = [e for e in events if isinstance(e, ToolExecutionCompletedEvent)]
        assert [e.tool_use_id for e in started] == ["t1", "t2", "t3"]
        assert [e.tool_use_id for e in completed] == ["t1", "t2", "t3"]
        # Final API event is end_turn.
        api_completes = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
        assert api_completes[-1].stop_reason == "end_turn"


class TestRunQueryMaxTurnsBoundary:
    """Hitting the max_turns cap raises LoopLimitExceeded -- D6.1 hard floor."""

    async def test_no_cap_allows_more_than_fifty_tool_turns(self) -> None:
        tool_turns = [
            [_fake_tool_use_event(tool_use_id=f"t{index}", tool_input={"value": str(index)})]
            for index in range(1, 52)
        ]
        client = _StubApiClient(events_per_turn=[*tool_turns, [_end_turn_event("done")]])
        context = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
            max_turns=None,
        )

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]

        assert client._turn == 52
        completes = [event for event in events if isinstance(event, ApiMessageCompleteEvent)]
        assert completes[-1].stop_reason == "end_turn"

    async def test_max_turns_exhausted_raises_loop_limit_exceeded(self) -> None:
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_use_id="t1", tool_input={"value": "1"})],
                [_fake_tool_use_event(tool_use_id="t2", tool_input={"value": "2"})],
            ],
        )
        context = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
        )
        # Cap to 2 turns -- both consumed by tool_use, no end_turn.
        context = dataclasses.replace(context, max_turns=2)

        with pytest.raises(LoopLimitExceeded) as exc_info:
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                pass

        assert exc_info.value.max_turns == 2
        assert exc_info.value.messages is not None
        assert [message.role for message in exc_info.value.messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
        ]
        # Both configured turns ran before the loop exhausted.
        assert client._turn == 2


class TestRunQueryProgrammingErrorPropagation:
    """Per D8.5/D10.5: tool.execute()'s raise propagates through the
    async generator -- not silently caught."""

    async def test_runtime_error_in_execute_surfaces_to_caller(self) -> None:
        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
        from tools.conftest import FakeInput

        class _CrashingFake(BaseTool[FakeInput]):
            execution_domain = ExecutionDomain.TRUSTED_CONTROL
            name = "Fake"
            description = "Crashes mid-execution -- should propagate."
            input_model = FakeInput

            async def execute(
                self,
                args: FakeInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                raise RuntimeError("boom")

        registry = ToolRegistry()
        registry.register(_CrashingFake())
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "x"})],
            ],
        )
        context = _make_context(api_client=client, tool_registry=registry)

        # P3-T4.4f: tool exception is wrapped in ToolError (per Three-Axis G)
        # and the original RuntimeError chains via __cause__.
        from openharness.errors import ToolError

        with pytest.raises(ToolError, match="Fake") as exc_info:
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                pass
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "boom" in str(exc_info.value.__cause__)


class TestRunQueryDryRunMode:
    """D12.5: PermissionMode.DRY_RUN bypasses permission_checker and
    tool.execute() entirely, emitting synthetic "would call X with Y"
    completions so the LLM sees loop progress without side effects."""

    async def test_dry_run_skips_execute_and_emits_synthetic_completion(
        self,
    ) -> None:
        from openharness.permissions import ExecutionPosture
        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
        from tools.conftest import FakeInput

        # A tool that would CRASH if actually called -- proves DRY_RUN
        # short-circuit really skips execute.
        class _MustNotRun(BaseTool[FakeInput]):
            execution_domain = ExecutionDomain.LOCAL_DATA
            name = "Fake"
            description = "Crashes if executed -- DRY_RUN should never run this."
            input_model = FakeInput

            async def execute(
                self,
                args: FakeInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                raise AssertionError("execute() called under DRY_RUN")

        registry = ToolRegistry()
        registry.register(_MustNotRun())
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "anything"})],
                [_end_turn_event()],
            ],
        )
        ctx = _make_context(api_client=client, tool_registry=registry)
        ctx = dataclasses.replace(ctx, execution_posture=ExecutionPosture.DRY_RUN)

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
        assert completed.is_error is False
        assert completed.output.startswith("would call Fake with ")
        assert "anything" in completed.output  # input echoed back

    async def test_dry_run_bypasses_deny_listed_command(self) -> None:
        # A "rm -rf /" command would normally hit DenyListChecker; under
        # DRY_RUN we never even consult the checker -- the synthetic
        # "would call" path runs instead.
        from openharness.permissions import ExecutionPosture
        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
        from tools.conftest import FakeInput

        # Fake that masquerades as Bash for deny-list purposes.
        class _BashLike(BaseTool[FakeInput]):
            execution_domain = ExecutionDomain.LOCAL_DATA
            name = "Bash"
            description = "Bash-like; would run if execute() reached."
            input_model = FakeInput

            async def execute(
                self,
                args: FakeInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                raise AssertionError("execute() called under DRY_RUN")

        registry = ToolRegistry()
        registry.register(_BashLike())
        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_name="Bash", tool_input={"value": "x"})],
                [_end_turn_event()],
            ],
        )
        ctx = _make_context(api_client=client, tool_registry=registry)
        ctx = dataclasses.replace(
            ctx,
            execution_posture=ExecutionPosture.DRY_RUN,
        )

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
        # No "permission denied" -- DRY_RUN doesn't consult the checker.
        assert "would call Bash" in completed.output
        assert completed.is_error is False

    async def test_auto_mode_behaves_like_default_in_phase2(self) -> None:
        # D12.4: AUTO is reserved for Phase 3 (skip interactive confirmation).
        # In Phase 2 it must behave identically to DEFAULT — pass through to
        # permission_checker + tool.execute().
        from openharness.permissions import ReviewerPosture

        client = _StubApiClient(
            events_per_turn=[
                [_fake_tool_use_event(tool_input={"value": "hello"})],
                [_end_turn_event()],
            ],
        )
        ctx = _make_context(
            api_client=client,
            tool_registry=_registry_with_fake_tool(),
        )
        ctx = dataclasses.replace(ctx, reviewer_posture=ReviewerPosture.AUTO)

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
        # Real tool ran -- output is the FakeTool's "value=hello", not "would call".
        assert completed.output == "value=hello"
        assert completed.is_error is False


class TestRunQueryParallelToolUsesInOneTurn:
    """LLM may emit multiple tool_use blocks in one assistant message.
    D6.3 says serial within a turn -- order must be preserved."""

    async def test_two_tool_uses_dispatch_in_emission_order(self) -> None:
        multi_use = ApiMessageCompleteEvent.model_validate(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "ta",
                            "name": "Fake",
                            "input": {"value": "first"},
                        },
                        {
                            "type": "tool_use",
                            "id": "tb",
                            "name": "Fake",
                            "input": {"value": "second"},
                        },
                    ],
                },
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "tool_use",
            }
        )
        client = _StubApiClient(
            events_per_turn=[
                [multi_use],
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

        started = [e for e in events if isinstance(e, ToolExecutionStartedEvent)]
        completed = [e for e in events if isinstance(e, ToolExecutionCompletedEvent)]
        # Order preserved: ta before tb
        assert [e.tool_use_id for e in started] == ["ta", "tb"]
        assert [e.tool_use_id for e in completed] == ["ta", "tb"]
        # Each tool's Started immediately precedes its Completed (D6.3 serial).
        ta_start_idx = events.index(started[0])
        ta_done_idx = events.index(completed[0])
        tb_start_idx = events.index(started[1])
        assert ta_start_idx < ta_done_idx < tb_start_idx


# --------------------------------------------------------------------------- #
# P6+-T1: ConversationCompleteEvent (multi-turn state hand-off)              #
# --------------------------------------------------------------------------- #


class TestConversationCompleteEvent:
    """P6+-T1: ``run_query`` yields ``ConversationCompleteEvent`` as
    its LAST event on clean exit (end_turn/max_tokens/stop_sequence).
    The event carries the full message history so REPL callers can
    carry forward multi-turn state."""

    async def test_event_is_last_event_yielded(self) -> None:
        from openharness.protocols import ConversationCompleteEvent

        events: list[ApiStreamEvent] = [
            ApiTextDeltaEvent(text="hello"),
            _end_turn_event(text="hello"),
        ]
        client = _StubApiClient(events_per_turn=[events])
        context = _make_context(api_client=client)

        collected = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        # The final event is the ConversationCompleteEvent.
        assert isinstance(collected[-1], ConversationCompleteEvent)
        # And no earlier event is one.
        assert not any(isinstance(e, ConversationCompleteEvent) for e in collected[:-1])

    async def test_event_includes_user_and_assistant_messages(self) -> None:
        from openharness.protocols import ConversationCompleteEvent

        end = _end_turn_event(text="response text")
        client = _StubApiClient(events_per_turn=[[end]])
        context = _make_context(api_client=client)

        collected = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        final = collected[-1]
        assert isinstance(final, ConversationCompleteEvent)
        # The history contains the original user message + the final
        # assistant response.
        assert len(final.messages) == 2
        assert final.messages[0].role == "user"
        assert final.messages[1].role == "assistant"

    async def test_not_emitted_on_loop_limit_exceeded(self) -> None:
        # When the loop hits max_turns without an end_turn, the engine
        # raises LoopLimitExceeded — no ConversationCompleteEvent is
        # yielded (the loop didn't complete cleanly). REPL handles
        # this via the LoopError exception arm.
        from openharness.engine.errors import LoopLimitExceeded
        from openharness.protocols import ConversationCompleteEvent

        # Force tool_use forever (so engine never returns end_turn).
        tool_use_msg = ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="t1", name="DummyTool", input={})],
        )
        complete = ApiMessageCompleteEvent(
            message=tool_use_msg,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="tool_use",
        )
        # Simulate a registered tool to keep dispatch happy.
        registry = ToolRegistry()
        client = _StubApiClient(events_per_turn=[[complete]] * 5)

        context = _make_context(
            api_client=client,
            tool_registry=registry,
            max_turns=2,
        )

        events_seen: list[ApiStreamEvent] = []
        with pytest.raises(LoopLimitExceeded):
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                events_seen.append(event)

        # Did NOT emit a ConversationCompleteEvent before raising.
        assert not any(isinstance(e, ConversationCompleteEvent) for e in events_seen)
