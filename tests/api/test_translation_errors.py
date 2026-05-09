"""Tests for ``_translate_openai_error`` end-to-end via the streaming API
client — P3-T1.1e (extracted from ``test_client.py`` per learnings/03 #6).

The error-translation path is logically separate from happy-path / tool-use
streaming: it answers "given an SDK exception, do we raise the right
:class:`OpenHarnessApiError` subclass with the right metadata, and does
retry policy honour the retry-vs-no-retry classification?"

Helpers + test doubles + ``_FAST_POLICY`` live in ``conftest.py`` (shared
with ``test_client.py``).
"""

from __future__ import annotations

import openai
import pytest

from api.conftest import (
    _FAST_POLICY,
    _TEXT_ONLY_CHUNKS,
    _FakeAsyncOpenAI,
    _FakeChatCompletions,
    _make_connection_error,
    _make_status_error,
    _simple_request,
)
from openharness.api import (
    AuthenticationFailure,
    OpenAICompatibleApiClient,
    RateLimitFailure,
    RequestFailure,
)
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
)


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
