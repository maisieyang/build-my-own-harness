"""Tests for ``OpenAICompatibleApiClient`` — happy path / tool-use streaming /
``SupportsStreamingMessages`` Protocol satisfaction.

Error-translation tests live in ``test_translation_errors.py`` (split P3-T1.1e
per learnings/03 #6). Test doubles + helpers + ``_FAST_POLICY`` live in
``conftest.py`` (shared with ``test_translation_errors.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest

# Imports from the package root (rather than submodules) — this also
# verifies the public API surface defined in api/__init__.py.
from api.conftest import (
    _TEXT_ONLY_CHUNKS,
    _FakeAsyncOpenAI,
    _FakeChatCompletions,
    _simple_request,
)
from openharness.api import (
    OpenAICompatibleApiClient,
    SupportsStreamingMessages,
)
from openharness.protocols.content import ToolUseBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
)

# ============================================================================
# Happy path
# ============================================================================


class TestStreamMessageHappyPath:
    async def test_text_only_streaming(self) -> None:
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[_TEXT_ONLY_CHUNKS]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk)  # type: ignore[arg-type]

        events: list[ApiStreamEvent] = []
        async for event in client.stream_message(_simple_request()):
            events.append(event)

        text_events = [e for e in events if isinstance(e, ApiTextDeltaEvent)]
        complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
        retry_events = [e for e in events if isinstance(e, ApiRetryEvent)]

        assert [e.text for e in text_events] == ["hello", " world"]
        assert len(complete_events) == 1
        assert complete_events[0].usage.input_tokens == 5
        assert complete_events[0].usage.output_tokens == 3
        assert complete_events[0].stop_reason == "end_turn"
        assert retry_events == []  # No retries on happy path

    async def test_sdk_called_with_translated_kwargs(self) -> None:
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[_TEXT_ONLY_CHUNKS]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk)  # type: ignore[arg-type]

        async for _ in client.stream_message(_simple_request("hello")):
            pass

        kwargs = sdk.chat_completions.last_kwargs
        assert kwargs is not None
        assert kwargs["model"] == "qwen-max"
        assert kwargs["max_tokens"] == 1024
        assert kwargs["stream"] is True
        assert kwargs["messages"] == [{"role": "user", "content": "hello"}]


# ============================================================================
# Tool-use streaming
# ============================================================================


class TestToolUseStreaming:
    async def test_text_then_tool_use_assembled(self) -> None:
        chunks: list[dict[str, Any]] = [
            {"choices": [{"delta": {"content": "Let me check"}, "finish_reason": None}]},
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
            {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ]
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[chunks]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk)  # type: ignore[arg-type]

        events: list[ApiStreamEvent] = []
        async for event in client.stream_message(_simple_request()):
            events.append(event)

        complete = next(e for e in events if isinstance(e, ApiMessageCompleteEvent))
        assert complete.stop_reason == "tool_use"
        assert len(complete.message.content) == 2
        tool_use = complete.message.content[1]
        assert isinstance(tool_use, ToolUseBlock)
        assert tool_use.name == "get_weather"
        assert tool_use.input == {"loc": "SF"}


# ============================================================================
# SupportsStreamingMessages Protocol — P3-T1.1d
# ============================================================================


class TestSupportsStreamingMessagesProtocol:
    """The Protocol that ``QueryContext.api_client`` types against.

    Structural typing: any object whose ``stream_message`` matches the
    signature satisfies the Protocol — no inheritance required. These tests
    are mypy-driven (the act of typing the LHS as ``SupportsStreamingMessages``
    while assigning a concrete object is itself the structural-typing check;
    if mypy strict is happy, the contract holds).
    """

    def test_oaic_client_satisfies_protocol(self) -> None:
        # Phase 2's OpenAICompatibleApiClient predates the Protocol declaration
        # (P3-T1.1d) but satisfies it by structural typing. The act of typing
        # the LHS as the Protocol while assigning the concrete class is itself
        # the structural-typing assertion; if mypy strict is happy, contract holds.
        from unittest.mock import Mock

        from openai import AsyncOpenAI

        client: SupportsStreamingMessages = OpenAICompatibleApiClient(
            sdk=Mock(spec=AsyncOpenAI),
        )
        assert callable(client.stream_message)

    def test_minimal_implementation_satisfies_protocol(self) -> None:
        # A class with ONLY ``stream_message`` (no inheritance, no other
        # methods) satisfies the Protocol — that's the value of structural
        # typing for stub clients in tests.
        class _MinimalClient:
            def stream_message(
                self,
                request: ApiMessageRequest,
            ) -> AsyncIterator[ApiStreamEvent]:
                async def _gen() -> AsyncIterator[ApiStreamEvent]:
                    return
                    yield  # pragma: no cover  # makes this an async generator

                del request
                return _gen()

        client: SupportsStreamingMessages = _MinimalClient()
        assert callable(client.stream_message)


# ============================================================================
# extra_body passthrough (RUNLOG 节点 8: DashScope thinking-mode default flip)
# ============================================================================


class TestExtraBodyPassthrough:
    """Provider-specific request-body fields (e.g. DashScope
    ``enable_thinking``) ride through a generic ``extra_body`` — no
    provider-specific branches in the harness."""

    async def test_extra_body_forwarded_to_sdk(self) -> None:
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[_TEXT_ONLY_CHUNKS]),
        )
        client = OpenAICompatibleApiClient(
            sdk=sdk,  # type: ignore[arg-type]
            extra_body={"enable_thinking": False},
        )

        async for _ in client.stream_message(_simple_request()):
            pass

        kwargs = sdk.chat_completions.last_kwargs
        assert kwargs is not None
        assert kwargs["extra_body"] == {"enable_thinking": False}

    async def test_no_extra_body_key_when_unset(self) -> None:
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[_TEXT_ONLY_CHUNKS]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk)  # type: ignore[arg-type]

        async for _ in client.stream_message(_simple_request()):
            pass

        kwargs = sdk.chat_completions.last_kwargs
        assert kwargs is not None
        assert "extra_body" not in kwargs
