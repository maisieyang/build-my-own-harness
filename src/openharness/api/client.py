"""OpenAI-compatible API client.

The orchestrator that combines:

- :func:`to_openai_request` (3c.1) for request translation
- :class:`_StreamAssembler` (3c.1) for response stream consumption
- :mod:`openharness.api.retry` (3b) for bounded retry decisions
- :func:`_translate_openai_error` (this file) for SDK-error → our-error mapping

Targets Qwen via DashScope by default. Works with any OpenAI-compatible
endpoint -- OpenAI cloud, DeepSeek, Moonshot, SiliconFlow, etc. -- by
configuring the ``AsyncOpenAI`` instance with a different ``base_url``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

import openai

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    PromptTooLongFailure,
    QuotaExceededFailure,
    RateLimitFailure,
    RequestFailure,
)
from openharness.api.retry import (
    DEFAULT_POLICY,
    RetryPolicy,
    compute_retry_delay,
    is_retryable,
)
from openharness.api.translation import _StreamAssembler, to_openai_request
from openharness.observability import get_logger
from openharness.protocols.stream_events import ApiRetryEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openai import AsyncOpenAI

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


logger = get_logger("api")


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


def _is_insufficient_quota(exc: openai.RateLimitError) -> bool:
    """Recognize non-transient quota exhaustion in common provider bodies."""
    body = exc.body
    if not isinstance(body, dict):
        return False
    nested = body.get("error")
    candidates = [body]
    if isinstance(nested, dict):
        candidates.append(nested)
    return any(
        value == "insufficient_quota"
        for candidate in candidates
        for key in ("type", "code")
        if isinstance((value := candidate.get(key)), str)
    )


# P4-T3.3a: provider-specific phrasings of "your input is too long".
# Pattern match against the *lowercased* error message;extensible —
# add new patterns as new providers / model families surface them.
# Ordered most-common-first. Empirically observed phrasings:
#   - OpenAI:    "context_length_exceeded"
#   - Anthropic: "prompt is too long"
#   - Qwen:      "Range of input length" (DashScope)
#   - Generic:   "maximum context length"
_PROMPT_TOO_LONG_PATTERNS: tuple[str, ...] = (
    "context_length_exceeded",
    "context length exceeded",
    "maximum context length",
    "prompt is too long",
    "range of input length",
    "input is too long",
    "input length",
)


def _is_prompt_too_long(message: str) -> bool:
    """True iff the message matches any known prompt-length-exceeded phrasing."""
    lowered = message.lower()
    return any(p in lowered for p in _PROMPT_TOO_LONG_PATTERNS)


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
        if _is_insufficient_quota(exc):
            return QuotaExceededFailure(
                str(exc),
                status_code=429,
                retry_after=_parse_retry_after(exc),
            )
        return RateLimitFailure(
            str(exc),
            status_code=429,
            retry_after=_parse_retry_after(exc),
        )
    if isinstance(exc, openai.APIStatusError):
        # P4-T3.3a: route prompt-too-long to its dedicated subclass so the
        # engine's reactive truncation layer can catch it without
        # parsing the message itself.
        message = str(exc)
        if _is_prompt_too_long(message):
            return PromptTooLongFailure(message, status_code=exc.status_code)
        # Other 4xx / 5xx -- BadRequestError / InternalServerError / etc.
        return RequestFailure(message, status_code=exc.status_code)
    if isinstance(exc, openai.APIConnectionError):
        # Network-level failure: no HTTP response, no status code.
        return RequestFailure(f"Connection error: {exc}", status_code=None)
    # Unknown -- wrap conservatively. Caller's ``from`` chains the original.
    return RequestFailure(f"Unexpected error: {exc}", status_code=None)


class SupportsStreamingMessages(Protocol):
    """Streaming-messages contract that engine / CLI consume.

    P3-T1.1d makes this Protocol explicit so:

    1. ``QueryContext.api_client`` types against a *contract* not a concrete
       class — Phase 5 Anthropic-native client / future Provider-specific
       clients all satisfy the same shape without inheritance.
    2. Tests that need to stub the API can implement the Protocol with a
       minimal class instead of touching :class:`OpenAICompatibleApiClient`
       internals.

    Structural typing: any object exposing a single async method
    ``stream_message(request) -> AsyncIterator[ApiStreamEvent]`` satisfies
    this Protocol — no inheritance required (mirrors the
    :class:`PermissionChecker` Protocol pattern in ``permissions/checker.py``).
    """

    def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        """Stream a single LLM response. See :meth:`OpenAICompatibleApiClient.stream_message`
        for the canonical event-order contract."""
        ...  # pragma: no cover - Protocol method body


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
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._sdk = sdk
        self._retry_policy = retry_policy
        # Provider-specific request-body fields (Settings.extra_body),
        # merged into every create() call via the SDK's extra_body kwarg.
        self._extra_body = extra_body

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
        if self._extra_body is not None:
            openai_kwargs["extra_body"] = dict(self._extra_body)

        async def _establish_stream() -> Any:
            try:
                return await self._sdk.chat.completions.create(**openai_kwargs)
            except OpenHarnessApiError:
                raise
            except Exception as exc:
                # Translate SDK exception so retry logic can decide retryability
                # via isinstance(error, RateLimitFailure / RequestFailure / ...)
                raise _translate_openai_error(exc) from exc

        stream: Any | None = None
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                stream = await _establish_stream()
                break
            except Exception as error:
                if not is_retryable(error) or attempt >= self._retry_policy.max_attempts:
                    raise
                delay = compute_retry_delay(error, attempt, self._retry_policy)
                logger.info(
                    "retry",
                    attempt=attempt,
                    delay_seconds=delay,
                    error=type(error).__name__,
                )
                # Surface the retry before sleeping so interactive callers
                # never appear frozen during provider-requested backoff.
                yield ApiRetryEvent(
                    attempt=attempt,
                    delay_seconds=delay,
                    error=str(error),
                )
                await asyncio.sleep(delay)

        if stream is None:  # pragma: no cover - the loop either assigns or raises
            raise RuntimeError("retry loop ended without a stream")

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
