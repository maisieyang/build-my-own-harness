"""Tests for ApiStreamEvent and its three event variants."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
)
from openharness.protocols.usage import UsageSnapshot

_EVENT_ADAPTER: TypeAdapter[ApiStreamEvent] = TypeAdapter(ApiStreamEvent)


class TestApiTextDeltaEvent:
    def test_construct(self) -> None:
        event = ApiTextDeltaEvent(text="hello")
        assert event.type == "text_delta"
        assert event.text == "hello"

    def test_empty_text_allowed(self) -> None:
        # SDKs occasionally emit empty deltas (whitespace, mark-end). Accept.
        event = ApiTextDeltaEvent(text="")
        assert event.text == ""

    def test_roundtrip(self) -> None:
        original = ApiTextDeltaEvent(text="streamed content")
        loaded = ApiTextDeltaEvent.model_validate_json(original.model_dump_json())
        assert loaded == original

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ApiTextDeltaEvent.model_validate({"type": "text_delta", "text": "x", "extra": "no"})


class TestApiMessageCompleteEvent:
    def test_construct_text_only(self) -> None:
        event = ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )
        assert event.type == "message_complete"
        assert event.usage.total_tokens == 15

    def test_construct_with_tool_use(self) -> None:
        event = ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[
                    TextBlock(text="I'll check"),
                    ToolUseBlock(id="toolu_1", name="bash", input={"cmd": "ls"}),
                ],
            ),
            usage=UsageSnapshot(input_tokens=100, output_tokens=20),
            stop_reason="tool_use",
        )
        assert event.stop_reason == "tool_use"
        assert isinstance(event.message.content[1], ToolUseBlock)

    def test_invalid_stop_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApiMessageCompleteEvent.model_validate(
                {
                    "type": "message_complete",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "x"}],
                    },
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "stop_reason": "not_a_real_reason",
                }
            )

    def test_roundtrip(self) -> None:
        original = ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="hi")]),
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
            stop_reason="max_tokens",
        )
        loaded = ApiMessageCompleteEvent.model_validate_json(original.model_dump_json())
        assert loaded == original


class TestApiRetryEvent:
    def test_construct(self) -> None:
        event = ApiRetryEvent(attempt=2, delay_seconds=1.5, error="rate limited")
        assert event.type == "retry"
        assert event.attempt == 2
        assert event.delay_seconds == 1.5

    def test_attempt_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ApiRetryEvent(attempt=0, delay_seconds=1.0, error="x")

    def test_delay_can_be_zero(self) -> None:
        event = ApiRetryEvent(attempt=1, delay_seconds=0.0, error="immediate retry")
        assert event.delay_seconds == 0.0

    def test_negative_delay_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApiRetryEvent(attempt=1, delay_seconds=-0.5, error="x")

    def test_roundtrip(self) -> None:
        original = ApiRetryEvent(attempt=3, delay_seconds=2.5, error="503 Service Unavailable")
        loaded = ApiRetryEvent.model_validate_json(original.model_dump_json())
        assert loaded == original


class TestApiStreamEventDiscriminator:
    """``Annotated[Union[...], Field(discriminator="type")]`` dispatch."""

    def test_dispatch_text_delta(self) -> None:
        event = _EVENT_ADAPTER.validate_python({"type": "text_delta", "text": "hello"})
        assert isinstance(event, ApiTextDeltaEvent)

    def test_dispatch_message_complete(self) -> None:
        event = _EVENT_ADAPTER.validate_python(
            {
                "type": "message_complete",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            }
        )
        assert isinstance(event, ApiMessageCompleteEvent)

    def test_dispatch_retry(self) -> None:
        event = _EVENT_ADAPTER.validate_python(
            {
                "type": "retry",
                "attempt": 1,
                "delay_seconds": 1.0,
                "error": "rate limited",
            }
        )
        assert isinstance(event, ApiRetryEvent)

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _EVENT_ADAPTER.validate_python({"type": "made_up_event"})

    def test_dispatch_tool_execution_started(self) -> None:
        event = _EVENT_ADAPTER.validate_python(
            {
                "type": "tool_execution_started",
                "tool_use_id": "toolu_01A",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            }
        )
        assert isinstance(event, ToolExecutionStartedEvent)
        assert event.tool_input == {"command": "ls"}

    def test_dispatch_tool_execution_completed(self) -> None:
        event = _EVENT_ADAPTER.validate_python(
            {
                "type": "tool_execution_completed",
                "tool_use_id": "toolu_01A",
                "tool_name": "Bash",
                "output": "file.txt\n",
                "is_error": False,
            }
        )
        assert isinstance(event, ToolExecutionCompletedEvent)
        assert event.is_error is False


class TestToolExecutionEvents:
    def test_started_event_construction(self) -> None:
        event = ToolExecutionStartedEvent(
            tool_use_id="toolu_x",
            tool_name="Read",
            tool_input={"path": "README.md"},
        )
        assert event.type == "tool_execution_started"
        assert event.tool_name == "Read"

    def test_completed_event_with_error_flag(self) -> None:
        event = ToolExecutionCompletedEvent(
            tool_use_id="toolu_y",
            tool_name="Bash",
            output="permission denied",
            is_error=True,
        )
        assert event.type == "tool_execution_completed"
        assert event.is_error is True

    def test_started_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ToolExecutionStartedEvent.model_validate(
                {
                    "type": "tool_execution_started",
                    "tool_use_id": "x",
                    "tool_name": "Bash",
                    "tool_input": {},
                    "extra": "no",
                }
            )

    def test_completed_roundtrip(self) -> None:
        original = ToolExecutionCompletedEvent(
            tool_use_id="t1",
            tool_name="Read",
            output="contents",
            is_error=False,
        )
        loaded = ToolExecutionCompletedEvent.model_validate_json(original.model_dump_json())
        assert loaded == original


class TestRealisticStreamingSequence:
    """A typical sequence of events for a tool-using turn."""

    def test_full_turn_sequence(self) -> None:
        events = [
            _EVENT_ADAPTER.validate_python({"type": "text_delta", "text": "I'll "}),
            _EVENT_ADAPTER.validate_python({"type": "text_delta", "text": "check."}),
            _EVENT_ADAPTER.validate_python(
                {
                    "type": "message_complete",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I'll check."},
                            {
                                "type": "tool_use",
                                "id": "toolu_01A",
                                "name": "bash",
                                "input": {"command": "ls"},
                            },
                        ],
                    },
                    "usage": {"input_tokens": 50, "output_tokens": 12},
                    "stop_reason": "tool_use",
                }
            ),
        ]
        text_events = [e for e in events if isinstance(e, ApiTextDeltaEvent)]
        complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
        assert len(text_events) == 2
        assert len(complete_events) == 1
        assert complete_events[0].stop_reason == "tool_use"
