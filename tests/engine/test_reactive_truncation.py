"""Tests for P4-T3.3c+3d — reactive prompt-too-long truncation in run_query.

Three contract surfaces:

1. **Recovery succeeds when room can be made**:provider raises
   PromptTooLongFailure once, engine drops oldest pair, retries, succeeds
   on the retry. The retry log event ``reactive_truncate`` fires once.
2. **Bounded retries**:after ``_REACTIVE_TRUNCATE_MAX`` consecutive
   failures, the engine re-raises the underlying error so the CLI's
   named except branch can surface it.
3. **No-pair-to-drop short-circuits**:if ``messages`` has no
   tool_use/tool_result pair (e.g., first-turn prompt blew context),
   the engine re-raises immediately without retrying.
"""

from __future__ import annotations

import dataclasses
import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from openharness.api.errors import PromptTooLongFailure
from openharness.engine.context import QueryContext
from openharness.engine.query import _REACTIVE_TRUNCATE_MAX, run_query
from openharness.observability import configure_logging
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.tools import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from openharness.api import SupportsStreamingMessages
    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _configure(stream: io.StringIO) -> None:
    configure_logging(level="DEBUG", format="json", stream=stream)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class _PtlThenEndTurnStub:
    """Stub client that raises PromptTooLongFailure on the first ``ptl_count``
    calls,then yields an end_turn on the next call.

    Captures every ``messages`` length seen so tests can assert that the
    engine truncated between attempts.
    """

    def __init__(self, ptl_count: int) -> None:
        self._ptl_remaining = ptl_count
        self.captured_message_counts: list[int] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.captured_message_counts.append(len(request.messages))
        if self._ptl_remaining > 0:
            self._ptl_remaining -= 1
            # ``yield`` keeps the body an async generator;raise before
            # any event so the engine catches it on attempt establishment.
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]
            raise PromptTooLongFailure("context_length_exceeded for stub", status_code=400)
        yield ApiMessageCompleteEvent.model_validate(
            {
                "message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "end_turn",
            }
        )


def _build_messages_with_tool_pairs(n_pairs: int) -> list[ConversationMessage]:
    """Build a messages list with n_pairs of assistant(tool_use)+user(tool_result)
    after an initial user prompt."""
    from openharness.protocols import ToolResultBlock, ToolUseBlock

    msgs: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="initial")]),
    ]
    for i in range(n_pairs):
        tid = f"t{i + 1}"
        msgs.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=tid, name="Read", input={"path": f"/x{i}"})],
            )
        )
        msgs.append(
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id=tid, content="ok")],
            )
        )
    return msgs


def _make_context(client: object) -> QueryContext:
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", client),
        tool_registry=ToolRegistry(),
        system_prompt="t",
        cwd=Path("/tmp"),
        model="qwen-plus",
        max_tokens=64,
    )


class TestRecoverySucceeds:
    async def test_one_ptl_then_end_turn_succeeds_after_truncate(
        self, log_stream: io.StringIO
    ) -> None:
        _configure(log_stream)
        stub = _PtlThenEndTurnStub(ptl_count=1)
        ctx = _make_context(stub)
        # 3 prior tool pairs available to drop.
        initial = _build_messages_with_tool_pairs(3)

        # Should complete without raising — exit cleanly on end_turn.
        events = [e async for e in run_query(initial, ctx)]

        # 2 calls captured: first with full messages (7), second with 5
        # (one pair dropped).
        assert len(stub.captured_message_counts) == 2
        assert stub.captured_message_counts[0] == 7
        assert stub.captured_message_counts[1] == 5

        # Exactly one ``reactive_truncate`` log event.
        truncs = [e for e in _lines(log_stream) if e["event"] == "reactive_truncate"]
        assert len(truncs) == 1
        assert truncs[0]["level"] == "warning"
        assert truncs[0]["attempt"] == 1
        assert truncs[0]["dropped_count"] == 2  # 1 pair = 2 messages dropped

        # The successful end_turn event came through.
        completes = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
        assert len(completes) == 1
        assert completes[0].stop_reason == "end_turn"


class TestBoundedRetries:
    async def test_exhausts_after_max_retries_then_reraises(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        # Configure stub to ALWAYS raise — engine should retry up to
        # _REACTIVE_TRUNCATE_MAX times then surface.
        stub = _PtlThenEndTurnStub(ptl_count=_REACTIVE_TRUNCATE_MAX + 5)
        ctx = _make_context(stub)
        # Enough pairs to drop on each attempt.
        initial = _build_messages_with_tool_pairs(_REACTIVE_TRUNCATE_MAX + 2)

        with pytest.raises(PromptTooLongFailure):
            async for _ in run_query(initial, ctx):
                pass

        # _REACTIVE_TRUNCATE_MAX retries → stream_message called
        # 1 + _REACTIVE_TRUNCATE_MAX times (initial + retries).
        assert len(stub.captured_message_counts) == _REACTIVE_TRUNCATE_MAX + 1
        # Each subsequent attempt has 2 fewer messages.
        diffs = [
            stub.captured_message_counts[i] - stub.captured_message_counts[i + 1]
            for i in range(len(stub.captured_message_counts) - 1)
        ]
        assert all(d == 2 for d in diffs)

        # _REACTIVE_TRUNCATE_MAX log events.
        truncs = [e for e in _lines(log_stream) if e["event"] == "reactive_truncate"]
        assert len(truncs) == _REACTIVE_TRUNCATE_MAX


class TestNoPairToDrop:
    async def test_first_turn_with_no_pair_immediately_raises(
        self, log_stream: io.StringIO
    ) -> None:
        """If the very first turn's prompt blows context (only user
        message, no tool pair to drop), engine cannot recover. Re-raise
        immediately — no retries, no log."""
        _configure(log_stream)
        stub = _PtlThenEndTurnStub(ptl_count=1)
        ctx = _make_context(stub)
        # Only the initial user prompt — no tool pair.
        initial = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]

        with pytest.raises(PromptTooLongFailure):
            async for _ in run_query(initial, ctx):
                pass

        # Exactly one stream_message call before raising.
        assert len(stub.captured_message_counts) == 1
        # No reactive_truncate log.
        truncs = [e for e in _lines(log_stream) if e["event"] == "reactive_truncate"]
        assert truncs == []


class TestNonPromptTooLongPassthrough:
    async def test_authentication_failure_not_caught_by_reactive_layer(
        self, log_stream: io.StringIO
    ) -> None:
        """A different error class (not PromptTooLongFailure) must NOT
        trigger reactive truncation — the original error propagates."""
        _configure(log_stream)
        from openharness.api.errors import AuthenticationFailure

        class _AuthFailStub:
            async def stream_message(
                self,
                request: ApiMessageRequest,
            ) -> AsyncIterator[ApiStreamEvent]:
                del request
                if False:  # pragma: no cover
                    yield  # type: ignore[unreachable]
                raise AuthenticationFailure("invalid key", status_code=401)

        ctx = _make_context(_AuthFailStub())
        initial = _build_messages_with_tool_pairs(3)

        with pytest.raises(AuthenticationFailure):
            async for _ in run_query(initial, ctx):
                pass

        # No reactive_truncate log — auth fails are not in scope.
        truncs = [e for e in _lines(log_stream) if e["event"] == "reactive_truncate"]
        assert truncs == []


class TestEventOrder:
    async def test_yielded_events_match_final_successful_call(
        self, log_stream: io.StringIO
    ) -> None:
        """Events from intermediate failed stream attempts must NOT leak
        to the iterator;the caller only sees events from the final
        (successful) attempt."""
        _configure(log_stream)

        from openharness.protocols import ApiTextDeltaEvent

        class _PtlOnceThenTextStream:
            def __init__(self) -> None:
                self._called = 0

            async def stream_message(
                self,
                request: ApiMessageRequest,
            ) -> AsyncIterator[ApiStreamEvent]:
                self._called += 1
                if self._called == 1:
                    if False:  # pragma: no cover
                        yield  # type: ignore[unreachable]
                    raise PromptTooLongFailure("too long", status_code=400)
                yield ApiTextDeltaEvent(text="hello")
                yield ApiMessageCompleteEvent.model_validate(
                    {
                        "message": {"role": "assistant", "content": []},
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "stop_reason": "end_turn",
                    }
                )

        stub = _PtlOnceThenTextStream()
        ctx = _make_context(stub)
        initial = _build_messages_with_tool_pairs(2)

        events = [e async for e in run_query(initial, ctx)]
        # Only the successful attempt's events come through:1 text delta + 1 message_complete.
        text_events = [e for e in events if isinstance(e, ApiTextDeltaEvent)]
        complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
        assert len(text_events) == 1
        assert len(complete_events) == 1


class TestTurnIdScope:
    async def test_truncate_keeps_same_turn_id(self, log_stream: io.StringIO) -> None:
        """A reactive retry happens within one turn — turn_id reported by
        the log must match the active turn at attempt time (not the next)."""
        _configure(log_stream)
        # Force one truncate retry on turn 1, then end_turn.
        stub = _PtlThenEndTurnStub(ptl_count=1)
        ctx = _make_context(stub)
        initial = _build_messages_with_tool_pairs(2)

        async for _ in run_query(initial, ctx):
            pass

        trunc = next(e for e in _lines(log_stream) if e["event"] == "reactive_truncate")
        assert trunc["turn"] == 1
        # turn_id from contextvar should also be 1 (set by bind_turn).
        assert trunc.get("turn_id") == 1


class TestMaxTurnsBoundary:
    async def test_reactive_truncate_does_not_consume_turn_budget(self) -> None:
        """A reactive retry within one turn must NOT increment the loop's
        turn counter — otherwise prompt-too-long would burn through
        ``max_turns`` rapidly."""
        # 2 PTL retries on turn 1 then end_turn → loop completes in 1 turn.
        stub = _PtlThenEndTurnStub(ptl_count=2)
        ctx = _make_context(stub)
        ctx = dataclasses.replace(ctx, max_turns=1)  # only 1 turn allowed
        initial = _build_messages_with_tool_pairs(3)

        # Must NOT raise LoopLimitExceeded — the 2 retries don't count.
        events = [e async for e in run_query(initial, ctx)]
        completes = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
        assert len(completes) == 1
        assert completes[0].stop_reason == "end_turn"
