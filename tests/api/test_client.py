"""Tests for ``OpenAICompatibleApiClient``.

The client is the orchestrator: it takes :class:`ApiMessageRequest`, calls
the openai SDK, translates the response stream into our
:class:`ApiStreamEvent`, and wraps the call with retry support so transient
failures surface as :class:`ApiRetryEvent` while persistent failures raise
the appropriate :class:`OpenHarnessApiError` subclass.

Mock boundary follows D3.3: we stub the SDK at its public surface
(``AsyncOpenAI``-shaped object with ``chat.completions.create()``), not at
the HTTP layer. Real openai exception classes are constructed via httpx
fixtures so the error-translation code path is exercised end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import openai
import pytest

# Imports from the package root (rather than submodules) — this also
# verifies the public API surface defined in api/__init__.py.
from openharness.api import (
    AuthenticationFailure,
    OpenAICompatibleApiClient,
    RateLimitFailure,
    RequestFailure,
    RetryPolicy,
)
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
)

# ============================================================================
# Test doubles -- fake AsyncOpenAI-shaped objects (mock at SDK boundary)
# ============================================================================


class _FakeChunk:
    """Stand-in for openai's ChatCompletionChunk; provides ``model_dump()``."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return self._data


class _FakeStream:
    """Async iterator over a list of fake chunks, mimicking the SDK's
    streaming response object."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> _FakeChunk:
        if not self._chunks:
            raise StopAsyncIteration
        return _FakeChunk(self._chunks.pop(0))


@dataclass
class _FakeChatCompletions:
    """Configurable fake of ``openai.AsyncOpenAI().chat.completions``.

    Each entry in ``responses`` is consumed by one call:
    - ``list[dict]`` → return a stream of those chunks
    - ``Exception`` → raise it
    """

    responses: list[list[dict[str, Any]] | Exception] = field(default_factory=list)
    call_count: int = 0
    last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _FakeStream:
        self.call_count += 1
        self.last_kwargs = kwargs

        if self.call_count > len(self.responses):
            raise RuntimeError(
                f"FakeAsyncOpenAI was called {self.call_count} times but only "
                f"{len(self.responses)} responses are configured.",
            )

        response = self.responses[self.call_count - 1]
        if isinstance(response, Exception):
            raise response
        return _FakeStream(response)


@dataclass
class _FakeChat:
    completions: _FakeChatCompletions


@dataclass
class _FakeAsyncOpenAI:
    """Fake ``AsyncOpenAI`` exposing the surface we use: ``client.chat.completions.create()``."""

    chat_completions: _FakeChatCompletions = field(default_factory=_FakeChatCompletions)

    @property
    def chat(self) -> _FakeChat:
        return _FakeChat(self.chat_completions)


# ============================================================================
# Helpers for constructing real openai exceptions in tests
# ============================================================================


def _make_status_error(
    error_class: type[openai.APIStatusError],
    status: int,
    message: str = "test error",
) -> openai.APIStatusError:
    """Construct a real openai status-error subclass via httpx fixtures."""
    req = httpx.Request("POST", "https://api.test.dashscope/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return error_class(message=message, response=resp, body=None)


def _make_connection_error(message: str = "Connection refused") -> openai.APIConnectionError:
    req = httpx.Request("POST", "https://api.test.dashscope/v1/chat/completions")
    return openai.APIConnectionError(message=message, request=req)


def _simple_request(prompt: str = "hi") -> ApiMessageRequest:
    return ApiMessageRequest(
        model="qwen-max",
        max_tokens=1024,
        messages=[ConversationMessage(role="user", content=[TextBlock(text=prompt)])],
    )


# Fast retry policy for tests so we do not actually wait between attempts.
_FAST_POLICY = RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.001, jitter=0.0)


# Sample chunk sequences used in multiple tests
_TEXT_ONLY_CHUNKS: list[dict[str, Any]] = [
    {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
    {"choices": [{"delta": {"content": " world"}, "finish_reason": None}]},
    {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    },
]


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
# Error translation
# ============================================================================


class TestErrorTranslation:
    async def test_authentication_error(self) -> None:
        auth_err = _make_status_error(openai.AuthenticationError, 401, "Invalid API key")
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[auth_err]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk)  # type: ignore[arg-type]

        with pytest.raises(AuthenticationFailure) as exc_info:
            async for _ in client.stream_message(_simple_request()):
                pass

        assert exc_info.value.status_code == 401
        assert isinstance(exc_info.value.__cause__, openai.AuthenticationError)

    async def test_authentication_error_not_retried(self) -> None:
        # Auth errors are non-retryable: only one SDK call should happen.
        auth_err = _make_status_error(openai.AuthenticationError, 401)
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[auth_err]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(AuthenticationFailure):
            async for _ in client.stream_message(_simple_request()):
                pass

        assert sdk.chat_completions.call_count == 1

    async def test_rate_limit_then_success_emits_retry_event(self) -> None:
        rate_err = _make_status_error(openai.RateLimitError, 429)
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(
                responses=[rate_err, _TEXT_ONLY_CHUNKS],
            ),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        events: list[ApiStreamEvent] = []
        async for event in client.stream_message(_simple_request()):
            events.append(event)

        retry_events = [e for e in events if isinstance(e, ApiRetryEvent)]
        text_events = [e for e in events if isinstance(e, ApiTextDeltaEvent)]
        complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]

        assert len(retry_events) == 1
        assert retry_events[0].attempt == 1
        # Retry events come BEFORE text deltas
        assert isinstance(events[0], ApiRetryEvent)
        assert len(text_events) == 2
        assert len(complete_events) == 1
        assert sdk.chat_completions.call_count == 2

    async def test_persistent_rate_limit_exhausts_retries(self) -> None:
        rate_err = _make_status_error(openai.RateLimitError, 429)
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(
                responses=[rate_err, rate_err, rate_err],
            ),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(RateLimitFailure):
            async for _ in client.stream_message(_simple_request()):
                pass

        assert sdk.chat_completions.call_count == 3

    async def test_5xx_error_translated_to_request_failure(self) -> None:
        server_err = _make_status_error(openai.InternalServerError, 500, "Server error")
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(
                responses=[server_err, server_err, server_err],
            ),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(RequestFailure) as exc_info:
            async for _ in client.stream_message(_simple_request()):
                pass

        assert exc_info.value.status_code == 500
        assert sdk.chat_completions.call_count == 3  # 5xx is retryable

    async def test_400_error_not_retried(self) -> None:
        bad_req = _make_status_error(openai.BadRequestError, 400, "Bad request")
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[bad_req]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(RequestFailure) as exc_info:
            async for _ in client.stream_message(_simple_request()):
                pass

        assert exc_info.value.status_code == 400
        assert sdk.chat_completions.call_count == 1  # Not retried

    async def test_connection_error(self) -> None:
        conn_err = _make_connection_error("Connection refused")
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[conn_err]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(RequestFailure) as exc_info:
            async for _ in client.stream_message(_simple_request()):
                pass

        # APIConnectionError has no status_code (network never reached server)
        assert exc_info.value.status_code is None
        # Not retryable since status_code is None — only one call
        assert sdk.chat_completions.call_count == 1
