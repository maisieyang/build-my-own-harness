"""Smoke test for ``run_query`` ↔ ``auto_compact_if_needed`` wiring — P11-T3.3f.

Verifies the engine actually calls compact before each LLM request when
``QueryContext.compact_enabled=True``, and that ``compact_enabled=False``
preserves pre-Phase-11 behavior (no compact in the loop).

We use a stub API client + minimal tool
registry so the loop runs end-to-end without touching real providers
or the filesystem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.hooks import HookRegistry
from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


class _RecordingStubClient:
    """Records the message list of every request the engine sends."""

    def __init__(self) -> None:
        self.requests: list[ApiMessageRequest] = []
        # Configured to PTL on the FIRST request when self.ptl_on_first
        # is True (used by the L4 fallback test).
        self.ptl_on_first = False
        self._call_index = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)
        # Always emit a complete event so the engine exits cleanly
        yield ApiTextDeltaEvent(text="ok")
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text="ok")],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )
        self._call_index += 1


class _SummarizingStubClient:
    """Used for L4 path: returns a <summary> response on every call.

    The engine's L4 will call summarize() which uses this same client,
    so we always return a structured-summary response (sufficient for
    both the summarize call AND the subsequent main-LLM call after
    compact).
    """

    def __init__(self) -> None:
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)
        response = "<summary>Compact summary content</summary>"
        yield ApiTextDeltaEvent(text=response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text=response)],
            ),
            usage=UsageSnapshot(input_tokens=10, output_tokens=2),
            stop_reason="end_turn",
        )


def _build_context(
    client: object,
    *,
    compact_enabled: bool = True,
    threshold_ratio: float = 0.83,
) -> QueryContext:
    return QueryContext(
        api_client=client,  # type: ignore[arg-type]
        tool_registry=create_default_tool_registry(),
        hook_registry=HookRegistry(),
        system_prompt="test prompt",
        cwd=__import__("pathlib").Path("/tmp"),
        model="qwen-plus",
        max_tokens=512,
        max_turns=3,
        compact_enabled=compact_enabled,
        compact_threshold_ratio=threshold_ratio,
    )


def _user_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


class TestCompactEngineIntegration:
    @pytest.mark.asyncio
    async def test_no_compact_when_disabled(self) -> None:
        # compact_enabled=False → engine never calls auto_compact;
        # message array passed to LLM matches what we provided.
        client = _RecordingStubClient()
        context = _build_context(client, compact_enabled=False)
        messages = [_user_text("x" * 1_000) for _ in range(200)]  # over threshold

        events = []
        async for event in run_query(messages, context):
            events.append(event)

        # LLM saw the FULL message list (no compaction)
        assert len(client.requests) == 1
        # Original 200 messages preserved
        assert len(client.requests[0].messages) == 200

    @pytest.mark.asyncio
    async def test_no_compact_when_below_threshold(self) -> None:
        # compact_enabled=True but small input → L0 says no-op
        client = _RecordingStubClient()
        context = _build_context(client, compact_enabled=True)
        messages = [_user_text("hi")]

        async for _event in run_query(messages, context):
            pass

        assert len(client.requests) == 1
        assert len(client.requests[0].messages) == 1

    @pytest.mark.asyncio
    async def test_l4_compact_fires_on_large_input(self) -> None:
        # Large input → L0 over threshold → L2 doesn't help (each
        # message short) → L4 LLM call.
        # Use _SummarizingStubClient so the L4 summarize() succeeds
        # AND the subsequent main LLM call also gets a response.
        client = _SummarizingStubClient()
        context = _build_context(client, compact_enabled=True)
        # 200 messages * ~1k chars each → ~67k tokens after padding,
        # well over the 26.5k threshold. Each message under 2400 char
        # threshold so L2 won't help.
        messages = [_user_text("x" * 1_000) for _ in range(200)]

        async for _event in run_query(messages, context):
            pass

        # Two requests issued: one summarize call (L4) + one main LLM
        # call with compacted messages
        assert len(client.requests) == 2
        # Main request (last) has FEWER messages than original — L4
        # spliced. Original 200 → boundary + summary + 12 preserved = 14
        main_request = client.requests[-1]
        assert len(main_request.messages) < 200
        assert len(main_request.messages) == 14

    @pytest.mark.asyncio
    async def test_engine_default_compact_enabled_matches_dataclass_default(self) -> None:
        # QueryContext built without compact_enabled kwarg defaults to True.
        # Verifies the default flows through to engine behavior.
        client = _RecordingStubClient()
        context = QueryContext(
            api_client=client,  # type: ignore[arg-type]
            tool_registry=create_default_tool_registry(),
            hook_registry=HookRegistry(),
            system_prompt="x",
            cwd=__import__("pathlib").Path("/tmp"),
            model="qwen-plus",
            max_tokens=128,
            max_turns=1,
        )
        # Default compact_enabled=True
        assert context.compact_enabled is True
        async for _event in run_query([_user_text("hi")], context):
            pass
        # Small input → no compaction fires; 1 request sent
        assert len(client.requests) == 1
