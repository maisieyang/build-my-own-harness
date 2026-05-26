"""Tests for :func:`summarize` primitive — P11-T1.

Four layers of behavior to pin:

1. **Happy path** — single LLM call streams text deltas; primitive
   concatenates and returns.
2. **Streaming retry** — transient :class:`OpenHarnessApiError`
   retries up to 2 times; auth failures never retry; 3rd consecutive
   failure propagates.
3. **PTL retry** — :class:`PromptTooLongFailure` drops oldest 1/5
   of messages and retries; up to 3 retries; empty-messages stops
   retry chain.
4. **Outer concerns** — :func:`asyncio.wait_for` timeout enforced;
   ``tools_disabled=True`` sends ``tools=[]``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from openharness.api.errors import (
    AuthenticationFailure,
    PromptTooLongFailure,
    RequestFailure,
)
from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.services.summarize import summarize

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# ---------------------------------------------------------------------------
# Stub clients — different shapes for different test surfaces
# ---------------------------------------------------------------------------


class _CompletionStubClient:
    """Yields a single text delta + complete event. Records last request."""

    def __init__(self, text: str = "summarized output") -> None:
        self._text = text
        self.last_request: ApiMessageRequest | None = None
        self.call_count = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        self.call_count += 1
        yield ApiTextDeltaEvent(text=self._text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text=self._text)],
            ),
            usage=UsageSnapshot(input_tokens=10, output_tokens=2),
            stop_reason="end_turn",
        )


class _ChunkedStubClient:
    """Yields multiple text deltas to verify concatenation."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        for chunk in self._chunks:
            yield ApiTextDeltaEvent(text=chunk)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text="".join(self._chunks))],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class _SequencedStubClient:
    """Sequentially raises / succeeds per call. Index advances each call."""

    def __init__(self, outcomes: list[Exception | str]) -> None:
        """``outcomes[i]`` is either an Exception to raise on call i, or
        a str to yield as a single text delta then complete."""
        self._outcomes = outcomes
        self.call_count = 0
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)
        outcome = self._outcomes[self.call_count]
        self.call_count += 1
        if isinstance(outcome, Exception):
            # Async generator needs a yield somewhere to be valid;
            # raise before the first yield so caller never gets one.
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]
            raise outcome
        yield ApiTextDeltaEvent(text=outcome)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text=outcome)],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class _SlowStubClient:
    """Stalls beyond the timeout to exercise asyncio.wait_for."""

    def __init__(self, sleep_seconds: float = 5.0) -> None:
        self._sleep = sleep_seconds

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        # Sleep before yielding ANY event — wait_for fires.
        await asyncio.sleep(self._sleep)
        # Defensive — never reached in the timeout test
        yield ApiMessageCompleteEvent(  # pragma: no cover
            message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _build_user_message(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_single_text_delta_returned(self) -> None:
        client = _CompletionStubClient(text="hello world")
        result = await summarize(
            messages=[_build_user_message("input")],
            system_prompt="summarize this",
            model="qwen-plus",
            api_client=client,
        )
        assert result == "hello world"
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_chunks_concatenated(self) -> None:
        client = _ChunkedStubClient(["hello ", "world", "!"])
        result = await summarize(
            messages=[_build_user_message("input")],
            system_prompt="prompt",
            model="qwen-plus",
            api_client=client,
        )
        assert result == "hello world!"

    @pytest.mark.asyncio
    async def test_request_carries_system_prompt(self) -> None:
        client = _CompletionStubClient()
        await summarize(
            messages=[_build_user_message("u")],
            system_prompt="this is the system prompt",
            model="qwen-plus",
            api_client=client,
        )
        assert client.last_request is not None
        assert client.last_request.system == "this is the system prompt"

    @pytest.mark.asyncio
    async def test_request_carries_model(self) -> None:
        client = _CompletionStubClient()
        await summarize(
            messages=[_build_user_message("u")],
            system_prompt="p",
            model="qwen-max",
            api_client=client,
        )
        assert client.last_request is not None
        assert client.last_request.model == "qwen-max"


# ---------------------------------------------------------------------------
# 2. Streaming retry
# ---------------------------------------------------------------------------


class TestStreamingRetry:
    @pytest.mark.asyncio
    async def test_one_transient_then_succeeds(self) -> None:
        client = _SequencedStubClient(outcomes=[RequestFailure("503 transient"), "recovered"])
        result = await summarize(
            messages=[_build_user_message("u")],
            system_prompt="p",
            model="qwen-plus",
            api_client=client,
        )
        assert result == "recovered"
        assert client.call_count == 2

    @pytest.mark.asyncio
    async def test_three_transients_propagates(self) -> None:
        # 1 initial + 2 retries = 3 attempts. 3rd failure propagates.
        client = _SequencedStubClient(
            outcomes=[
                RequestFailure("503 a"),
                RequestFailure("503 b"),
                RequestFailure("503 c"),
            ]
        )
        with pytest.raises(RequestFailure, match="503 c"):
            await summarize(
                messages=[_build_user_message("u")],
                system_prompt="p",
                model="qwen-plus",
                api_client=client,
            )
        assert client.call_count == 3

    @pytest.mark.asyncio
    async def test_auth_failure_never_retries(self) -> None:
        # AuthenticationFailure is not transient — propagate immediately
        # without consuming the streaming retry budget.
        client = _SequencedStubClient(outcomes=[AuthenticationFailure("401 invalid key")])
        with pytest.raises(AuthenticationFailure):
            await summarize(
                messages=[_build_user_message("u")],
                system_prompt="p",
                model="qwen-plus",
                api_client=client,
            )
        assert client.call_count == 1


# ---------------------------------------------------------------------------
# 3. PTL retry — bubbles past streaming retry to the outer layer
# ---------------------------------------------------------------------------


class TestPtlRetry:
    @pytest.mark.asyncio
    async def test_ptl_drops_oldest_fifth_then_succeeds(self) -> None:
        client = _SequencedStubClient(outcomes=[PromptTooLongFailure("too long"), "shrunk and OK"])
        # 10 messages → 1/5 = 2 dropped → 8 remaining on retry
        messages = [_build_user_message(f"msg-{i}") for i in range(10)]
        result = await summarize(
            messages=messages,
            system_prompt="p",
            model="qwen-plus",
            api_client=client,
        )
        assert result == "shrunk and OK"
        # First attempt had 10 messages; second had 8 (oldest 2 dropped)
        assert len(client.requests[0].messages) == 10
        assert len(client.requests[1].messages) == 8
        # Oldest dropped → second request starts with msg-2
        assert client.requests[1].messages[0].content[0].text == "msg-2"  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_four_ptls_propagates(self) -> None:
        # 1 initial + 3 retries = 4 attempts; 4th PTL propagates.
        client = _SequencedStubClient(
            outcomes=[
                PromptTooLongFailure("a"),
                PromptTooLongFailure("b"),
                PromptTooLongFailure("c"),
                PromptTooLongFailure("d"),
            ]
        )
        messages = [_build_user_message(f"msg-{i}") for i in range(20)]
        with pytest.raises(PromptTooLongFailure, match="d"):
            await summarize(
                messages=messages,
                system_prompt="p",
                model="qwen-plus",
                api_client=client,
            )
        assert client.call_count == 4

    @pytest.mark.asyncio
    async def test_ptl_with_single_message_propagates_after_drop(self) -> None:
        # 1 message → drop 1 → empty → propagate (not stuck retrying
        # against empty messages forever).
        client = _SequencedStubClient(outcomes=[PromptTooLongFailure("single too big")])
        with pytest.raises(PromptTooLongFailure, match="single too big"):
            await summarize(
                messages=[_build_user_message("the only one")],
                system_prompt="p",
                model="qwen-plus",
                api_client=client,
            )
        # Only attempted once — empty input is unrecoverable.
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_caller_messages_not_mutated(self) -> None:
        # PTL retry takes a copy; caller's list is unchanged after.
        client = _SequencedStubClient(outcomes=[PromptTooLongFailure("once"), "ok"])
        messages = [_build_user_message(f"msg-{i}") for i in range(10)]
        original_length = len(messages)
        await summarize(
            messages=messages,
            system_prompt="p",
            model="qwen-plus",
            api_client=client,
        )
        assert len(messages) == original_length


# ---------------------------------------------------------------------------
# 4. Outer concerns — timeout + tools_disabled
# ---------------------------------------------------------------------------


class TestOuterConcerns:
    @pytest.mark.asyncio
    async def test_timeout_raises_on_slow_client(self) -> None:
        # _SlowStubClient sleeps 5s; timeout 0.1s → wait_for fires.
        client = _SlowStubClient(sleep_seconds=5.0)
        with pytest.raises(asyncio.TimeoutError):
            await summarize(
                messages=[_build_user_message("u")],
                system_prompt="p",
                model="qwen-plus",
                api_client=client,
                timeout_seconds=0.1,
            )

    @pytest.mark.asyncio
    async def test_tools_disabled_sends_empty_list(self) -> None:
        # Default tools_disabled=True → request.tools should be []
        # so the provider unambiguously sees "tools forbidden".
        client = _CompletionStubClient()
        await summarize(
            messages=[_build_user_message("u")],
            system_prompt="p",
            model="qwen-plus",
            api_client=client,
        )
        assert client.last_request is not None
        assert client.last_request.tools == []

    @pytest.mark.asyncio
    async def test_tools_disabled_false_sends_none(self) -> None:
        # Opt-out for the rare future caller that wants tools enabled.
        # request.tools=None means field omitted from the wire request.
        client = _CompletionStubClient()
        await summarize(
            messages=[_build_user_message("u")],
            system_prompt="p",
            model="qwen-plus",
            api_client=client,
            tools_disabled=False,
        )
        assert client.last_request is not None
        assert client.last_request.tools is None

    @pytest.mark.asyncio
    async def test_max_tokens_passed_through(self) -> None:
        client = _CompletionStubClient()
        await summarize(
            messages=[_build_user_message("u")],
            system_prompt="p",
            model="qwen-plus",
            api_client=client,
            max_tokens=5000,
        )
        assert client.last_request is not None
        assert client.last_request.max_tokens == 5000
