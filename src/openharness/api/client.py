"""OpenAI-compatible API client.

The orchestrator that combines:

- :func:`to_openai_request` (3c.1) for request translation
- :class:`_StreamAssembler` (3c.1) for response stream consumption
- :func:`with_retry` (3b) for retry-on-failure
- :func:`_translate_openai_error` (this file) for SDK-error → our-error mapping

Targets Qwen via DashScope by default. Works with any OpenAI-compatible
endpoint -- OpenAI cloud, DeepSeek, Moonshot, SiliconFlow, etc. -- by
configuring the ``AsyncOpenAI`` instance with a different ``base_url``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import openai

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)
from openharness.api.retry import DEFAULT_POLICY, RetryPolicy, with_retry
from openharness.api.translation import _StreamAssembler, to_openai_request
from openharness.protocols.stream_events import ApiRetryEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openai import AsyncOpenAI

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


def _parse_retry_after(exc: openai.RateLimitError) -> float | None:
    """Extract ``Retry-After`` header from a rate-limit response, if present.

    Returns ``None`` if the header is missing or malformed -- caller falls
    back to computed exponential backoff.
    """
    try:
        retry_after = exc.response.headers.get("retry-after")
        if retry_after is None:
            return None
        return float(retry_after)
    except (AttributeError, ValueError):
        return None


def _translate_openai_error(exc: Exception) -> OpenHarnessApiError:
    """Map an openai SDK exception to our error hierarchy.

    Caller is expected to ``raise X from original_exc`` so the ``__cause__``
    chain is preserved for debug visibility.
    """
    if isinstance(exc, openai.AuthenticationError):
        return AuthenticationFailure(str(exc), status_code=401)
    if isinstance(exc, openai.PermissionDeniedError):
        return AuthenticationFailure(str(exc), status_code=403)
    if isinstance(exc, openai.RateLimitError):
        return RateLimitFailure(
            str(exc),
            status_code=429,
            retry_after=_parse_retry_after(exc),
        )
    if isinstance(exc, openai.APIStatusError):
        # Other 4xx / 5xx -- BadRequestError / InternalServerError / etc.
        return RequestFailure(str(exc), status_code=exc.status_code)
    if isinstance(exc, openai.APIConnectionError):
        # Network-level failure: no HTTP response, no status code.
        return RequestFailure(f"Connection error: {exc}", status_code=None)
    # Unknown -- wrap conservatively. Caller's ``from`` chains the original.
    return RequestFailure(f"Unexpected error: {exc}", status_code=None)


class OpenAICompatibleApiClient:
    """API client implementing the streaming-messages contract against any
    OpenAI-compatible endpoint.

    Construct with a pre-configured :class:`AsyncOpenAI` instance so the
    caller (CLI, tests) controls auth / base_url / timeouts / etc. The client
    itself only handles the request → translate → call → translate → stream
    pipeline.
    """

    def __init__(
        self,
        *,
        sdk: AsyncOpenAI,
        retry_policy: RetryPolicy = DEFAULT_POLICY,
    ) -> None:
        self._sdk = sdk
        self._retry_policy = retry_policy

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        """Stream a single LLM response.

        Event order:

        1. Zero or more :class:`ApiRetryEvent`s (only if connection
           establishment had to retry)
        2. Zero or more :class:`ApiTextDeltaEvent`s (text generated
           incrementally)
        3. Exactly one :class:`ApiMessageCompleteEvent` (terminal)

        Raises :class:`OpenHarnessApiError` (subclass) if the request
        ultimately fails after retries.
        """
        openai_kwargs = to_openai_request(request)

        retry_events: list[ApiRetryEvent] = []

        async def on_retry(attempt: int, delay: float, error: Exception) -> None:
            retry_events.append(
                ApiRetryEvent(
                    attempt=attempt,
                    delay_seconds=delay,
                    error=str(error),
                ),
            )

        async def _establish_stream() -> Any:
            try:
                return await self._sdk.chat.completions.create(**openai_kwargs)
            except OpenHarnessApiError:
                raise
            except Exception as exc:
                # Translate SDK exception so retry logic can decide retryability
                # via isinstance(error, RateLimitFailure / RequestFailure / ...)
                raise _translate_openai_error(exc) from exc

        stream = await with_retry(
            _establish_stream,
            policy=self._retry_policy,
            on_retry=on_retry,
        )

        # Retries (if any) happened before stream establishment -- yield those
        # events first so consumers see them before the actual response.
        for retry_event in retry_events:
            yield retry_event

        assembler = _StreamAssembler()
        try:
            async for chunk in stream:
                chunk_dict = chunk.model_dump()
                for event in assembler.consume(chunk_dict):
                    yield event
        except OpenHarnessApiError:
            raise
        except Exception as exc:
            raise _translate_openai_error(exc) from exc

        yield assembler.finalize()


__all__ = [
    "OpenAICompatibleApiClient",
]
