"""Tests for the wire-format translation between Anthropic-shape (our
``protocols/``) and OpenAI-shape (Qwen / OpenAI cloud / DeepSeek / etc.).

Two main concerns:

1. ``to_openai_request(req)`` -- convert ``ApiMessageRequest`` into the dict
   shape that ``openai.AsyncOpenAI().chat.completions.create()`` expects as
   keyword arguments.

2. ``_StreamAssembler`` -- consume OpenAI streaming chunks (the dict shape of
   ``chat.completion.chunk``), yield our ``ApiStreamEvent``s as text deltas
   arrive, and produce a final ``ApiMessageCompleteEvent`` on ``finalize()``.

These are pure functions / stateless logic. Tests here use plain ``dict``
fixtures rather than the real ``openai`` SDK -- the mock boundary is at the
SDK layer, not here.
"""

from __future__ import annotations

import pytest

from openharness.api.errors import MalformedToolCallFailure
from openharness.api.translation import _StreamAssembler, to_openai_request
from openharness.protocols.content import (
    ImageBlock,
    ImageSource,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.tools import ToolSpec

# ============================================================================
# to_openai_request: Anthropic-shape  →  OpenAI-shape kwargs
# ============================================================================


class TestToOpenAiRequestBasicFields:
    def test_model_max_tokens_stream_passthrough(self) -> None:
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        result = to_openai_request(req)
        assert result["model"] == "qwen-max"
        assert result["max_tokens"] == 1024
        assert result["stream"] is True

    def test_explicit_stream_false_preserved(self) -> None:
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            stream=False,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        assert to_openai_request(req)["stream"] is False


class TestToOpenAiRequestSystemPrompt:
    def test_no_system_means_no_system_message(self) -> None:
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        result = to_openai_request(req)
        assert "system" not in result  # No top-level system field
        roles = [m["role"] for m in result["messages"]]
        assert "system" not in roles

    def test_system_prepended_as_first_message(self) -> None:
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            system="You are helpful.",
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        result = to_openai_request(req)
        assert "system" not in result  # OpenAI uses role:system in messages, not top-level
        assert result["messages"][0] == {"role": "system", "content": "You are helpful."}
        assert result["messages"][1]["role"] == "user"


class TestToOpenAiRequestTextContent:
    def test_single_text_block_uses_string_form(self) -> None:
        # Single TextBlock → use plain string content for compactness.
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        result = to_openai_request(req)
        assert result["messages"][0] == {"role": "user", "content": "hi"}

    def test_multiple_text_blocks_use_list_form(self) -> None:
        # Multi-block content cannot collapse to a single string.
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[
                ConversationMessage(
                    role="user",
                    content=[TextBlock(text="part 1"), TextBlock(text="part 2")],
                ),
            ],
        )
        result = to_openai_request(req)
        assert result["messages"][0]["content"] == [
            {"type": "text", "text": "part 1"},
            {"type": "text", "text": "part 2"},
        ]


class TestToOpenAiRequestImage:
    def test_user_message_with_image_uses_data_url(self) -> None:
        req = ApiMessageRequest(
            model="qwen-vl-max",
            max_tokens=1024,
            messages=[
                ConversationMessage(
                    role="user",
                    content=[
                        TextBlock(text="What is this?"),
                        ImageBlock(source=ImageSource(media_type="image/png", data="b64data")),
                    ],
                ),
            ],
        )
        result = to_openai_request(req)
        msg = result["messages"][0]
        assert msg["content"] == [
            {"type": "text", "text": "What is this?"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,b64data"},
            },
        ]


class TestToOpenAiRequestAssistantWithToolUse:
    def test_assistant_with_only_tool_use(self) -> None:
        # Anthropic puts tool_use INSIDE assistant message content;
        # OpenAI puts tool_calls at message TOP LEVEL.
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[
                ConversationMessage(role="user", content=[TextBlock(text="weather?")]),
                ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(id="toolu_01", name="get_weather", input={"loc": "SF"}),
                    ],
                ),
            ],
        )
        result = to_openai_request(req)
        assistant = result["messages"][1]
        assert assistant["role"] == "assistant"
        # No content (or empty) when only tool_use
        assert assistant.get("content") in (None, "", [])
        assert assistant["tool_calls"] == [
            {
                "id": "toolu_01",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"loc": "SF"}'},
            },
        ]

    def test_assistant_with_text_and_tool_use(self) -> None:
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[
                ConversationMessage(role="user", content=[TextBlock(text="weather?")]),
                ConversationMessage(
                    role="assistant",
                    content=[
                        TextBlock(text="checking..."),
                        ToolUseBlock(id="toolu_01", name="get_weather", input={"loc": "SF"}),
                    ],
                ),
            ],
        )
        result = to_openai_request(req)
        assistant = result["messages"][1]
        assert assistant["content"] == "checking..."
        assert len(assistant["tool_calls"]) == 1


class TestToOpenAiRequestToolResultUserMessage:
    def test_user_with_tool_result_becomes_role_tool_message(self) -> None:
        # Anthropic puts tool_result INSIDE a user message;
        # OpenAI requires a SEPARATE message with role="tool".
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[
                ConversationMessage(role="user", content=[TextBlock(text="weather?")]),
                ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(id="toolu_01", name="get_weather", input={"loc": "SF"}),
                    ],
                ),
                ConversationMessage(
                    role="user",
                    content=[ToolResultBlock(tool_use_id="toolu_01", content="Sunny, 72F")],
                ),
            ],
        )
        result = to_openai_request(req)
        last = result["messages"][-1]
        assert last == {
            "role": "tool",
            "tool_call_id": "toolu_01",
            "content": "Sunny, 72F",
        }


class TestToOpenAiRequestTools:
    def test_tool_specs_wrapped_in_function_envelope(self) -> None:
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            tools=[
                ToolSpec(
                    name="get_weather",
                    description="Get current weather",
                    input_schema={
                        "type": "object",
                        "properties": {"loc": {"type": "string"}},
                        "required": ["loc"],
                    },
                ),
            ],
        )
        result = to_openai_request(req)
        assert result["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"loc": {"type": "string"}},
                        "required": ["loc"],
                    },
                },
            },
        ]

    def test_no_tools_field_when_request_has_none(self) -> None:
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        result = to_openai_request(req)
        assert "tools" not in result

    def test_empty_tools_list_omits_field(self) -> None:
        # F15 (B2 eval 挖出): summarize/compact/decompose 用 tools=[] 表达
        # "禁用工具", 但 DashScope 拒绝空数组 ("[] is too short - 'tools'").
        # 空列表在 OpenAI-compat wire 层等同于无 tools —— 省略字段, 别发 [].
        req = ApiMessageRequest(
            model="qwen-max",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            tools=[],
        )
        result = to_openai_request(req)
        assert "tools" not in result


# ============================================================================
# _StreamAssembler: consume OpenAI streaming chunks  →  ApiStreamEvent / final
# ============================================================================


class TestStreamAssemblerEmpty:
    def test_finish_only_chunk_yields_no_events(self) -> None:
        a = _StreamAssembler()
        events = list(
            a.consume(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 0},
                },
            ),
        )
        assert events == []


class TestStreamAssemblerTextOnly:
    def test_single_text_delta_yields_text_event(self) -> None:
        a = _StreamAssembler()
        events = list(
            a.consume(
                {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
            ),
        )
        assert len(events) == 1
        assert isinstance(events[0], ApiTextDeltaEvent)
        assert events[0].text == "hello"

    def test_multi_chunk_text_finalize_assembles_full_message(self) -> None:
        a = _StreamAssembler()
        a.consume({"choices": [{"delta": {"content": "hello "}, "finish_reason": None}]})
        a.consume({"choices": [{"delta": {"content": "world"}, "finish_reason": None}]})
        a.consume(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        complete = a.finalize()
        assert isinstance(complete, ApiMessageCompleteEvent)
        assert complete.stop_reason == "end_turn"
        assert complete.usage.input_tokens == 5
        assert complete.usage.output_tokens == 3
        assert complete.message.role == "assistant"
        assert len(complete.message.content) == 1
        text_block = complete.message.content[0]
        assert isinstance(text_block, TextBlock)
        assert text_block.text == "hello world"


class TestStreamAssemblerToolUse:
    def test_tool_use_assembled_from_argument_deltas(self) -> None:
        a = _StreamAssembler()
        # Initial delta with id + name
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": ""},
                                },
                            ],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        # Argument deltas (incremental)
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": '{"loc"'}}],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": ': "SF"}'}}],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        # Final
        a.consume(
            {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

        complete = a.finalize()
        assert complete.stop_reason == "tool_use"
        assert len(complete.message.content) == 1
        tool_use = complete.message.content[0]
        assert isinstance(tool_use, ToolUseBlock)
        assert tool_use.id == "call_1"
        assert tool_use.name == "get_weather"
        assert tool_use.input == {"loc": "SF"}  # JSON-parsed from "{\"loc\": \"SF\"}"

    def test_truncated_arguments_with_length_finish_reason_raises(self) -> None:
        """max_tokens cap truncates a Write tool call mid-content string —
        finalize() must surface MalformedToolCallFailure with a hint about
        raising --max-tokens, NOT a raw JSONDecodeError.
        """
        a = _StreamAssembler()
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_write_1",
                                    "type": "function",
                                    "function": {
                                        "name": "Write",
                                        "arguments": '{"file_path": "/tmp/a.md", "content": "long article ',
                                    },
                                },
                            ],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        a.consume({"choices": [{"delta": {}, "finish_reason": "length"}]})

        with pytest.raises(MalformedToolCallFailure) as excinfo:
            a.finalize()
        err = excinfo.value
        assert err.tool_name == "Write"
        assert err.finish_reason == "length"
        assert err.arguments_excerpt is not None
        assert err.arguments_excerpt.startswith('{"file_path"')
        msg = str(err)
        assert "truncated mid-JSON" in msg
        assert "'Write'" in msg
        assert "--max-tokens" in msg

    def test_truncated_arguments_with_provider_misreporting_finish_reason(
        self,
    ) -> None:
        """Some providers (observed: DashScope/qwen) report
        finish_reason='tool_calls' even when the cap truncated the stream
        mid-string. The heuristic on parser msg 'Unterminated string' must
        still route to the --max-tokens hint, not the model-bug hint.
        """
        a = _StreamAssembler()
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_w2",
                                    "type": "function",
                                    "function": {
                                        "name": "Write",
                                        "arguments": '{"file_path": "/tmp/a.md", "content": "...',
                                    },
                                },
                            ],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        a.consume({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})

        with pytest.raises(MalformedToolCallFailure) as excinfo:
            a.finalize()
        err = excinfo.value
        assert err.finish_reason == "tool_calls"
        msg = str(err)
        # heuristic kicks in — same --max-tokens hint as the 'length' case
        assert "truncated mid-JSON" in msg
        assert "--max-tokens" in msg
        # NOT the "transient model bug" branch
        assert "malformed syntax" not in msg

    def test_malformed_json_with_stop_finish_reason_raises(self) -> None:
        """Model emits invalid JSON despite finish_reason='stop' (rare model
        bug case) — finalize() still surfaces MalformedToolCallFailure but
        with the 'malformed syntax' framing, not the max_tokens framing.
        """
        a = _StreamAssembler()
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": "{loc: not-json}",
                                    },
                                },
                            ],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        a.consume({"choices": [{"delta": {}, "finish_reason": "stop"}]})

        with pytest.raises(MalformedToolCallFailure) as excinfo:
            a.finalize()
        err = excinfo.value
        assert err.tool_name == "get_weather"
        assert err.finish_reason == "stop"
        msg = str(err)
        assert "malformed syntax" in msg
        assert "max_tokens" not in msg  # different code path, not the cap framing


class TestStreamAssemblerMixedContent:
    def test_text_then_tool_use_both_in_final_message(self) -> None:
        a = _StreamAssembler()
        # Text first
        a.consume(
            {"choices": [{"delta": {"content": "Let me check"}, "finish_reason": None}]},
        )
        # Then tool call (full args in one chunk for simplicity)
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"loc": "SF"}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        a.consume(
            {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        complete = a.finalize()
        assert len(complete.message.content) == 2
        assert isinstance(complete.message.content[0], TextBlock)
        assert complete.message.content[0].text == "Let me check"
        assert isinstance(complete.message.content[1], ToolUseBlock)


class TestStreamAssemblerStopReasonMapping:
    def test_stop_to_end_turn(self) -> None:
        a = _StreamAssembler()
        a.consume(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0},
            },
        )
        assert a.finalize().stop_reason == "end_turn"

    def test_tool_calls_to_tool_use(self) -> None:
        a = _StreamAssembler()
        a.consume(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {"name": "noop", "arguments": "{}"},
                                },
                            ],
                        },
                        "finish_reason": None,
                    },
                ],
            },
        )
        a.consume(
            {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
        assert a.finalize().stop_reason == "tool_use"

    def test_length_to_max_tokens(self) -> None:
        a = _StreamAssembler()
        a.consume(
            {"choices": [{"delta": {"content": "trun"}, "finish_reason": None}]},
        )
        a.consume(
            {
                "choices": [{"delta": {}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
        assert a.finalize().stop_reason == "max_tokens"

    def test_content_filter_to_stop_sequence(self) -> None:
        # Anthropic doesn't have content_filter; closest analog is stop_sequence.
        a = _StreamAssembler()
        a.consume(
            {
                "choices": [{"delta": {}, "finish_reason": "content_filter"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0},
            },
        )
        assert a.finalize().stop_reason == "stop_sequence"
