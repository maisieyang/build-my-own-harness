"""End-to-end Phase 11 integration tests (P11-T7-7a, 7b).

Pin the full Phase 11 loops without the engine wrapper in the way:

1. **Extract → store → relevance → use_count** (T7-7a). Stub LLM
   responds to ``EXTRACTION_SYSTEM_PROMPT`` with canned JSON.
   First call writes a memory. Second call's ``select_relevant_memories``
   picks it up and ``mark_memory_used`` bumps ``use_count`` to 1.
2. **Compact L4 fires above threshold** (T7-7b). A large messages
   array triggers ``auto_compact_if_needed`` → L4 → stub
   summarize returns a ``<summary>``-tagged blob → message count
   drops, ``CompactResult.compact_kind == "full"``.

These tests deliberately use the *real* ``FilesystemMemoryStore``,
``select_relevant_memories``, ``mark_memory_used``,
``extract_memories_from_turn``, ``auto_compact_if_needed`` — only
the LLM client is stubbed. If they pass, the Phase 11 pipeline is
wired end-to-end.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openharness.memory.relevance import select_relevant_memories
from openharness.memory.store import FilesystemMemoryStore
from openharness.memory.usage import mark_memory_used
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.protocols.stream_events import ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.services.compact import auto_compact_if_needed
from openharness.services.extract import extract_memories_from_turn

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.protocols import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# --------------------------------------------------------------------------- #
# Stub client                                                                 #
# --------------------------------------------------------------------------- #


class _CannedResponseStub:
    """Returns a single canned text response on each ``stream_message``.

    Tests provide either ``response`` (single static reply for every call)
    or ``responses`` (list — call N returns responses[N]).
    """

    def __init__(
        self,
        *,
        response: str | None = None,
        responses: list[str] | None = None,
    ) -> None:
        if response is None and responses is None:
            raise ValueError("provide either response or responses")
        self._single = response
        self._sequence = responses
        self.calls: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.calls.append(request)
        if self._sequence is not None:
            text = self._sequence[len(self.calls) - 1]
        else:
            assert self._single is not None
            text = self._single
        yield ApiTextDeltaEvent(text=text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


# --------------------------------------------------------------------------- #
# 7a — Extract → store → relevance → use_count                                #
# --------------------------------------------------------------------------- #


class TestExtractToInjectionLoop:
    """Proves the Phase 11 closed loop works on a real on-disk store.

    Round 1: stub LLM returns extraction JSON → memory written.
    Round 2: query mentions the same topic → memory selected →
    ``mark_memory_used`` bumps ``use_count``.
    """

    @pytest.mark.asyncio
    async def test_round_trip_extract_then_relevance_then_use_count(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)

        # Round 1: extract writes a stripe-sdk memory.
        extraction_json = json.dumps(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "stripe-sdk",
                        "description": "Stripe SDK 8.x quirks",
                        "body": "Stripe SDK v8 changed the webhook signature header name to Stripe-Signature.",
                    }
                ]
            }
        )
        client = _CannedResponseStub(response=extraction_json)
        round1_messages = [
            ConversationMessage(role="user", content=[TextBlock(text="hi")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="hi back")]),
        ]
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,  # type: ignore[arg-type]
            model="qwen-plus",
            messages=round1_messages,
            memory_store=store,
            enabled=True,
        )
        assert result.error is None, f"extract failed: {result.error}"
        assert len(result.written) == 1
        assert result.written[0].name == "stripe-sdk"
        # Memory file should exist on disk
        memory_files = list(tmp_path.glob("*.md"))
        assert len(memory_files) == 1

        # Round 2: select_relevant_memories against a query that meta-
        # hits the stored memory's description.
        # We re-discover from disk to ensure persistence (not just
        # in-memory state from round 1).
        store2 = FilesystemMemoryStore(project_dir=tmp_path)
        all_memories = list(store2.discover().values())
        assert len(all_memories) == 1
        relevant = select_relevant_memories(
            "stripe webhook signature header",
            all_memories,
        )
        assert len(relevant) == 1
        assert relevant[0].name == "stripe-sdk"
        # initial use_count is 0
        assert relevant[0].use_count == 0

        # Mark used (simulates CLI's per-turn injection step).
        mark_memory_used(relevant[0])

        # Re-read from disk — use_count should have bumped to 1.
        store3 = FilesystemMemoryStore(project_dir=tmp_path)
        all_memories_after = list(store3.discover().values())
        assert len(all_memories_after) == 1
        assert all_memories_after[0].use_count == 1
        assert all_memories_after[0].last_used_at is not None


# --------------------------------------------------------------------------- #
# 7b — auto_compact_if_needed fires L4 above threshold                        #
# --------------------------------------------------------------------------- #


class TestCompactL4FiresAboveThreshold:
    """Proves L4 LLM compaction fires when L0 estimates above threshold.

    For qwen-plus: window=32k, threshold_ratio=0.83 → threshold≈26560.
    Build messages totaling ~30k estimated tokens, stub the summarize
    LLM with a 9-slot-shaped response, assert ``compact_kind == "full"``
    and message count drops from many → boundary + summary + 12 preserved.
    """

    @pytest.mark.asyncio
    async def test_l4_fires_and_compresses(self) -> None:
        # To force L4 we have to bypass L2 (context-collapse) first:
        # L2 fires when ANY single text block is > 2400 chars
        # (``_L2_LONG_TEXT_THRESHOLD``). Use many short-ish messages
        # so L2 has nothing to collapse, but the aggregate token
        # count still exceeds threshold (~26560 for qwen-plus).
        #
        # ~2000 chars per message x 50 messages = 100k chars total
        # → roughly 33k tokens via the 4/3-padding estimator, comfortably
        # above the 26560 threshold but with each individual block
        # under L2's cutoff.
        small_chunk = "x " * 1000  # 2000 chars per message, < L2 threshold
        many_msgs: list[ConversationMessage] = []
        for i in range(50):
            role: str = "user" if i % 2 == 0 else "assistant"
            many_msgs.append(
                ConversationMessage(
                    role=role,  # type: ignore[arg-type]
                    content=[TextBlock(text=f"m{i}: {small_chunk}")],
                )
            )

        # Stub returns a 9-slot summary inside <summary>...</summary>
        summary_blob = (
            "<analysis>truncating for brevity</analysis>\n"
            "<summary>\n"
            "1. **Primary Request**: stub\n"
            "2. **Key Technical Concepts**: stub\n"
            "3. **Files and Code Sections**: stub\n"
            "4. **Errors and fixes**: stub\n"
            "5. **Problem Solving**: stub\n"
            "6. **All user messages**: stub\n"
            "7. **Pending Tasks**: stub\n"
            "8. **Current Work**: stub\n"
            "9. **Optional Next Step**: stub\n"
            "</summary>"
        )
        client = _CannedResponseStub(response=summary_blob)

        new_messages, compact_result = await auto_compact_if_needed(
            many_msgs,
            model="qwen-plus",
            api_client=client,  # type: ignore[arg-type]
            session_memory_path=None,
            enabled=True,
            threshold_ratio=0.83,
        )

        # L4 should have fired
        assert compact_result.compact_kind == "full"
        assert 4 in compact_result.applied_levels
        # Summarize LLM was called exactly once
        assert len(client.calls) == 1
        # The summarize call ran with tools_disabled=True → tools=[]
        assert client.calls[0].tools == []
        # New message count: boundary (1) + summary (1) + preserved (12) = 14
        # ``_PRESERVE_RECENT`` defaults to 12 in compact.py
        assert len(new_messages) == 14
        assert len(new_messages) < len(many_msgs)
        assert compact_result.final_tokens < compact_result.original_tokens

    @pytest.mark.asyncio
    async def test_under_threshold_no_op(self) -> None:
        # Small messages → L0 says no-op, no LLM call.
        small_msgs = [
            ConversationMessage(role="user", content=[TextBlock(text="hi")]),
        ]
        client = _CannedResponseStub(response="should never be called")
        new_messages, compact_result = await auto_compact_if_needed(
            small_msgs,
            model="qwen-plus",
            api_client=client,  # type: ignore[arg-type]
            session_memory_path=None,
            enabled=True,
        )
        assert compact_result.compact_kind == "none"
        assert new_messages == small_msgs
        assert client.calls == []  # no LLM call

    @pytest.mark.asyncio
    async def test_disabled_short_circuits(self) -> None:
        client = _CannedResponseStub(response="<summary>x</summary>")
        big_chunk = "x " * 4000
        many = [
            ConversationMessage(
                role="user",
                content=[TextBlock(text=f"{i}:{big_chunk}")],
            )
            for i in range(20)
        ]
        _new_messages, compact_result = await auto_compact_if_needed(
            many,
            model="qwen-plus",
            api_client=client,  # type: ignore[arg-type]
            session_memory_path=None,
            enabled=False,  # disabled
        )
        assert compact_result.compact_kind == "none"
        assert client.calls == []
