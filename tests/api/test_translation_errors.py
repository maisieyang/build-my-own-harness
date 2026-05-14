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

    async def test_permission_denied_error_maps_to_authentication_failure(
        self,
    ) -> None:
        """``PermissionDeniedError`` (HTTP 403) translates to
        :class:`AuthenticationFailure` — semantically still an auth issue
        (key is real but lacks scope), and not retryable."""
        perm_err = _make_status_error(openai.PermissionDeniedError, 403, "Insufficient permissions")
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[perm_err]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(AuthenticationFailure) as exc_info:
            async for _ in client.stream_message(_simple_request()):
                pass

        assert exc_info.value.status_code == 403
        assert sdk.chat_completions.call_count == 1  # not retried

    async def test_unexpected_exception_falls_back_to_request_failure(
        self,
    ) -> None:
        """Unknown exception class (not a subclass of openai.* errors) gets
        wrapped conservatively as RequestFailure with ``status_code=None`` —
        surfaces the issue rather than silently swallowing it."""
        unknown = RuntimeError("totally unexpected SDK quirk")
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(responses=[unknown]),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(RequestFailure) as exc_info:
            async for _ in client.stream_message(_simple_request()):
                pass

        assert "Unexpected error" in str(exc_info.value)
        assert exc_info.value.status_code is None

    async def test_rate_limit_with_malformed_retry_after_header_falls_back(
        self,
    ) -> None:
        """``_parse_retry_after`` swallows AttributeError / ValueError so a
        malformed ``retry-after`` doesn't crash translation. After
        exhausting retries (3 attempts at _FAST_POLICY) the final
        RateLimitFailure carries ``retry_after=None`` — proving the parse
        error was absorbed, not propagated."""
        import httpx as _httpx

        def _malformed_rate_err() -> openai.RateLimitError:
            req = _httpx.Request("POST", "https://api.test.dashscope/v1/chat/completions")
            resp = _httpx.Response(429, headers={"retry-after": "tomorrow"}, request=req)
            return openai.RateLimitError(message="rate limit", response=resp, body=None)

        # Configure 3 responses — all 3 attempts fail with the same error.
        sdk = _FakeAsyncOpenAI(
            chat_completions=_FakeChatCompletions(
                responses=[_malformed_rate_err(), _malformed_rate_err(), _malformed_rate_err()]
            ),
        )
        client = OpenAICompatibleApiClient(sdk=sdk, retry_policy=_FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(RateLimitFailure) as exc_info:
            async for _ in client.stream_message(_simple_request()):
                pass

        # retry_after is None because "tomorrow" can't be parsed as float;
        # _parse_retry_after caught the ValueError and returned None.
        assert exc_info.value.retry_after is None
        assert exc_info.value.status_code == 429
        assert sdk.chat_completions.call_count == 3  # all retries consumed
