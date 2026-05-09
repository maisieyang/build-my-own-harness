"""Shared test infrastructure for the api/ test package — P3-T1.1e.

Pre-T1.1e these test doubles + helpers + ``_FAST_POLICY`` lived in
``test_client.py``. Splitting ``TestErrorTranslation`` into its own file
(``test_translation_errors.py``, learnings/03 #6) made it obvious that the
infrastructure is shared, so it belongs here.

Exposes:

- Test doubles for the AsyncOpenAI SDK boundary (``_FakeAsyncOpenAI``,
  ``_FakeChatCompletions``, ``_FakeStream``, ``_FakeChunk``, ``_FakeChat``).
- ``_make_status_error`` / ``_make_connection_error`` — build real openai
  exception subclasses via httpx fixtures so error-translation paths run
  end-to-end.
- ``_simple_request`` — minimal ``ApiMessageRequest`` factory.
- ``_FAST_POLICY`` — retry policy with sub-millisecond delays so retry
  paths execute deterministically without sleeping.
- ``_TEXT_ONLY_CHUNKS`` — canonical happy-path chunk stream.

Mock boundary follows D3.3: we stub at the SDK's public surface
(``AsyncOpenAI``-shaped object with ``chat.completions.create()``), not at
the HTTP layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import openai

from openharness.api import RetryPolicy
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest

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
    - ``list[dict]`` -> return a stream of those chunks
    - ``Exception`` -> raise it
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
    """Fake ``AsyncOpenAI`` exposing the surface we use:
    ``client.chat.completions.create()``."""

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


# ============================================================================
# Shared test data + retry policy
# ============================================================================


# Fast retry policy for tests so we do not actually wait between attempts.
_FAST_POLICY = RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.001, jitter=0.0)


# Sample chunk sequences used in multiple tests.
_TEXT_ONLY_CHUNKS: list[dict[str, Any]] = [
    {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
    {"choices": [{"delta": {"content": " world"}, "finish_reason": None}]},
    {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    },
]
