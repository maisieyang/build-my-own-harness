"""Tests for compact L0-L4 escalation pipeline — P11-T3.

5 surfaces:

1. L0 token estimation + threshold computation
2. L2 context-collapse (deterministic, char-based)
3. L3 session_memory reuse (file read, no LLM)
4. L4 full_compact (summarize call + 9-slot prompt + parse)
5. ``auto_compact_if_needed`` orchestrator escalation order
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from openharness.api.errors import RequestFailure
from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.content import (
    ImageBlock,
    ImageSource,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.services.compact import (
    CompactResult,
    FullCompactError,
    auto_compact_if_needed,
    estimate_message_tokens,
    full_compact,
    get_context_window,
    threshold_tokens,
    try_context_collapse,
    try_session_memory_compaction,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


class _StubLLMClient:
    """LLM stub for L4 tests. Yields a single text response then completes."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_request: ApiMessageRequest | None = None
        self.call_count = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        self.call_count += 1
        yield ApiTextDeltaEvent(text=self._response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text=self._response)],
            ),
            usage=UsageSnapshot(input_tokens=10, output_tokens=2),
            stop_reason="end_turn",
        )


class _FailingLLMClient:
    """LLM stub that always raises. Used to verify L4 graceful failure."""

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]
        raise RequestFailure("always failing")


# ---------------------------------------------------------------------------
# 1. L0 token estimation + threshold
# ---------------------------------------------------------------------------


class TestL0Estimation:
    def test_known_model_returns_published_window(self) -> None:
        assert get_context_window("qwen-plus") == 32_000
        assert get_context_window("gpt-4o") == 128_000
        # Settings default model (D5.3, updated 2026-07-09) must not fall
        # back to the conservative 32k default.
        assert get_context_window("qwen3.7-max") == 262_144

    def test_unknown_model_returns_default(self) -> None:
        assert get_context_window("totally-fake-model-9000") == 32_000

    def test_prefix_match_works(self) -> None:
        # "qwen-plus-latest" → prefix-matches "qwen-plus"
        assert get_context_window("qwen-plus-latest") == 32_000

    def test_threshold_ratio_applied(self) -> None:
        # 32_000 * 0.83 = 26_560
        assert threshold_tokens("qwen-plus", threshold_ratio=0.83) == 26_560

    def test_empty_messages_zero_tokens(self) -> None:
        assert estimate_message_tokens([], model="qwen-plus") == 0

    def test_text_block_token_counted(self) -> None:
        messages = [_user_text("hello world")]
        # qwen-plus uses byte-ratio fallback (no tiktoken encoding)
        # "hello world" → 11 bytes → 11//4 = 2 tokens, *4/3 = 2.67 → 2
        result = estimate_message_tokens(messages, model="qwen-plus")
        assert result > 0

    def test_image_block_uses_image_budget(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[ImageBlock(source=ImageSource(media_type="image/png", data="b64"))],
        )
        # 3072 base *4/3 padding = 4096
        assert estimate_message_tokens([msg], model="qwen-plus") == 4_096

    def test_tool_use_and_tool_result_counted(self) -> None:
        messages = [
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="x", name="read_file", input={"path": "a.py"})],
            ),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="x", content="file contents")],
            ),
        ]
        result = estimate_message_tokens(messages, model="qwen-plus")
        assert result > 0


# ---------------------------------------------------------------------------
# 2. L2 context-collapse
# ---------------------------------------------------------------------------


class TestL2ContextCollapse:
    def test_short_text_unchanged(self) -> None:
        messages = [_user_text("short")]
        new_messages, changed = try_context_collapse(messages)
        assert changed is False
        # Text passes through unchanged
        assert new_messages[0].content[0].text == "short"  # type: ignore[union-attr]

    def test_long_text_collapsed(self) -> None:
        long = "x" * 5_000  # > 2400 threshold
        messages = [_user_text(long)]
        new_messages, changed = try_context_collapse(messages)
        assert changed is True
        new_text = new_messages[0].content[0].text  # type: ignore[union-attr]
        # Head 900 + marker + tail 500 — total ≈ 1430 chars
        assert "[collapsed" in new_text
        assert len(new_text) < 2_000

    def test_long_tool_result_collapsed(self) -> None:
        long = "y" * 5_000
        messages = [
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="x", content=long)],
            )
        ]
        new_messages, changed = try_context_collapse(messages)
        assert changed is True
        new_block = new_messages[0].content[0]
        assert isinstance(new_block, ToolResultBlock)
        assert "[collapsed" in new_block.content
        assert new_block.tool_use_id == "x"

    def test_caller_messages_not_mutated(self) -> None:
        long = "x" * 5_000
        original = [_user_text(long)]
        original_first_text = original[0].content[0].text  # type: ignore[union-attr]
        try_context_collapse(original)
        # Caller's message unchanged after the call
        assert original[0].content[0].text == original_first_text  # type: ignore[union-attr]

    def test_mixed_short_and_long(self) -> None:
        long = "x" * 5_000
        messages = [_user_text("short one"), _user_text(long), _user_text("also short")]
        new_messages, changed = try_context_collapse(messages)
        assert changed is True
        assert new_messages[0].content[0].text == "short one"  # type: ignore[union-attr]
        assert "[collapsed" in new_messages[1].content[0].text  # type: ignore[union-attr]
        assert new_messages[2].content[0].text == "also short"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 3. L3 session_memory reuse
# ---------------------------------------------------------------------------


class TestL3SessionMemoryReuse:
    def test_stat_failure_returns_unchanged(self) -> None:
        class _BrokenStatPath:
            def exists(self) -> bool:
                return True

            def stat(self) -> object:
                raise OSError("stat denied")

        messages = [_user_text(f"msg-{i}") for i in range(20)]
        path = _BrokenStatPath()

        new_messages, changed = try_session_memory_compaction(messages, path)  # type: ignore[arg-type]

        assert changed is False
        assert new_messages == messages

    def test_read_failure_returns_unchanged(self) -> None:
        class _FreshStat:
            st_mtime = time.time()

        class _BrokenReadPath:
            def exists(self) -> bool:
                return True

            def stat(self) -> _FreshStat:
                return _FreshStat()

            def read_text(self, *, encoding: str) -> str:
                assert encoding == "utf-8"
                raise UnicodeDecodeError("utf-8", b"x", 0, 1, "invalid")

        messages = [_user_text(f"msg-{i}") for i in range(20)]
        path = _BrokenReadPath()

        new_messages, changed = try_session_memory_compaction(messages, path)  # type: ignore[arg-type]

        assert changed is False
        assert new_messages == messages

    def test_no_checkpoint_path_returns_unchanged(self) -> None:
        messages = [_user_text(f"msg-{i}") for i in range(20)]
        new_messages, changed = try_session_memory_compaction(messages, None)
        assert changed is False
        assert new_messages == messages

    def test_nonexistent_checkpoint_returns_unchanged(self, tmp_path: Path) -> None:
        messages = [_user_text(f"msg-{i}") for i in range(20)]
        fake_path = tmp_path / "nonexistent.md"
        new_messages, changed = try_session_memory_compaction(messages, fake_path)
        assert changed is False
        assert new_messages == messages

    def test_stale_checkpoint_returns_unchanged(self, tmp_path: Path) -> None:
        # Write a checkpoint then backdate its mtime to >1h ago
        checkpoint = tmp_path / "checkpoint.md"
        checkpoint.write_text("# Session Memory\nstale\n")
        old_time = time.time() - 7_200  # 2 hours ago
        import os

        os.utime(checkpoint, (old_time, old_time))

        messages = [_user_text(f"msg-{i}") for i in range(20)]
        new_messages, changed = try_session_memory_compaction(messages, checkpoint)
        assert changed is False
        assert new_messages == messages

    def test_fresh_checkpoint_splices_older_messages(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "checkpoint.md"
        checkpoint.write_text("# Session Memory\n## Current State\nfresh state\n")
        messages = [_user_text(f"msg-{i}") for i in range(20)]
        new_messages, changed = try_session_memory_compaction(messages, checkpoint)
        assert changed is True
        # Synthetic checkpoint message + last 12 preserved = 13 total
        assert len(new_messages) == 13
        # First message is the synthetic checkpoint
        synthetic_text = new_messages[0].content[0].text  # type: ignore[union-attr]
        assert "Session memory checkpoint" in synthetic_text
        assert "fresh state" in synthetic_text
        # Last 12 messages preserved verbatim
        assert new_messages[-1].content[0].text == "msg-19"  # type: ignore[union-attr]
        assert new_messages[1].content[0].text == "msg-8"  # type: ignore[union-attr]

    def test_too_few_messages_returns_unchanged(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "checkpoint.md"
        checkpoint.write_text("fresh content\n")
        # 5 messages < preserve_recent=12 → can't splice
        messages = [_user_text(f"msg-{i}") for i in range(5)]
        new_messages, changed = try_session_memory_compaction(messages, checkpoint)
        assert changed is False
        assert new_messages == messages


# ---------------------------------------------------------------------------
# 4. L4 full_compact
# ---------------------------------------------------------------------------


class TestL4FullCompact:
    @pytest.mark.asyncio
    async def test_prompt_preserves_structured_context_evidence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        async def _capture_prompt(**kwargs: object) -> str:
            seen["system_prompt"] = str(kwargs["system_prompt"])
            seen["messages"] = kwargs["messages"]
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _capture_prompt)
        messages = [_user_text(f"msg-{i}") for i in range(5)]

        _new_messages, changed = await full_compact(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=2,
        )

        assert changed is True
        prompt = str(seen["system_prompt"])
        assert "Tool/Skill provenance" in prompt
        assert "opaque identifiers" in prompt
        assert "verbatim" in prompt
        assert "chronological order" in prompt
        assert "latest error" in prompt
        assert "most recent evidence" in prompt
        assert "filler" in prompt
        summary_messages = seen["messages"]
        assert isinstance(summary_messages, list)
        final_message = summary_messages[-1]
        assert isinstance(final_message, ConversationMessage)
        assert final_message.role == "user"
        final_text = final_message.content[0]
        assert isinstance(final_text, TextBlock)
        assert "summarize the preceding conversation now" in final_text.text.lower()
        assert "every uppercase key=value marker line" in final_text.text.lower()
        assert "synthetic tool-use envelope" in final_text.text.lower()

    @pytest.mark.asyncio
    async def test_default_timeout_allows_long_context_summarization(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, float] = {}

        async def _capture_timeout(**kwargs: object) -> str:
            seen["timeout_seconds"] = float(kwargs["timeout_seconds"])
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _capture_timeout)
        messages = [_user_text(f"msg-{i}") for i in range(5)]

        _new_messages, changed = await full_compact(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=2,
        )

        assert changed is True
        assert seen["timeout_seconds"] == 120.0

    @pytest.mark.asyncio
    async def test_too_few_messages_skips(self) -> None:
        # < preserve_recent=12, no work to do
        client = _StubLLMClient(response="<summary>x</summary>")
        messages = [_user_text(f"msg-{i}") for i in range(5)]
        new_messages, changed = await full_compact(messages, model="qwen-plus", api_client=client)
        assert changed is False
        assert new_messages == messages
        assert client.call_count == 0  # LLM never called

    @pytest.mark.asyncio
    async def test_summary_extracted_and_spliced(self) -> None:
        response = (
            "<analysis>thinking about what to summarize</analysis>"
            "<summary>1. Primary Request: Test compact\n2. Key Concepts: pytest</summary>"
        )
        client = _StubLLMClient(response=response)
        messages = [_user_text(f"msg-{i}") for i in range(20)]
        new_messages, changed = await full_compact(messages, model="qwen-plus", api_client=client)
        assert changed is True
        # boundary marker + summary + 12 recent = 14
        assert len(new_messages) == 14
        # Boundary marker first
        assert "summarized below" in new_messages[0].content[0].text  # type: ignore[union-attr]
        # Summary contents present, analysis discarded
        summary_msg = new_messages[1].content[0].text  # type: ignore[union-attr]
        assert "Primary Request" in summary_msg
        assert "thinking about" not in summary_msg

    @pytest.mark.asyncio
    async def test_no_summary_tags_uses_raw_response(self) -> None:
        # LLM ignored schema — salvage with stripped raw
        client = _StubLLMClient(response="just plain text no tags")
        messages = [_user_text(f"msg-{i}") for i in range(20)]
        new_messages, changed = await full_compact(messages, model="qwen-plus", api_client=client)
        assert changed is True
        summary_msg = new_messages[1].content[0].text  # type: ignore[union-attr]
        assert "just plain text" in summary_msg

    @pytest.mark.asyncio
    async def test_llm_failure_returns_unchanged(self) -> None:
        # summarize() exhausts retries → full_compact returns False
        client = _FailingLLMClient()
        messages = [_user_text(f"msg-{i}") for i in range(20)]
        new_messages, changed = await full_compact(messages, model="qwen-plus", api_client=client)
        assert changed is False
        assert new_messages == messages

    @pytest.mark.asyncio
    async def test_explicit_unexpected_failure_reports_type_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _unexpected(**_kwargs: object) -> str:
            raise ValueError("sensitive implementation detail")

        monkeypatch.setattr("openharness.services.compact.summarize", _unexpected)
        messages = [_user_text(f"msg-{i}") for i in range(5)]

        with pytest.raises(FullCompactError, match=r"^summarization failed: ValueError$"):
            await full_compact(
                messages,
                model="qwen-plus",
                api_client=_StubLLMClient("unused"),
                preserve_recent=2,
                raise_on_failure=True,
            )

    @pytest.mark.asyncio
    async def test_empty_summary_fail_open_returns_unchanged(self) -> None:
        messages = [_user_text(f"msg-{i}") for i in range(5)]

        new_messages, changed = await full_compact(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("   "),
            preserve_recent=2,
        )

        assert changed is False
        assert new_messages == messages

    @pytest.mark.asyncio
    async def test_explicit_timeout_reports_timeout_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _timeout(**_kwargs: object) -> str:
            raise asyncio.TimeoutError

        monkeypatch.setattr("openharness.services.compact.summarize", _timeout)
        messages = [_user_text(f"msg-{i}") for i in range(5)]

        with pytest.raises(
            FullCompactError,
            match=r"summarization timed out after 25s",
        ):
            await full_compact(
                messages,
                model="qwen-plus",
                api_client=_StubLLMClient("unused"),
                timeout_seconds=25,
                preserve_recent=2,
                raise_on_failure=True,
            )

    @pytest.mark.asyncio
    async def test_explicit_provider_failure_reports_safe_typed_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _provider_failure(**_kwargs: object) -> str:
            raise RequestFailure("bad request for sk-secret-value", status_code=400)

        monkeypatch.setattr("openharness.services.compact.summarize", _provider_failure)
        messages = [_user_text(f"msg-{i}") for i in range(5)]

        with pytest.raises(FullCompactError) as caught:
            await full_compact(
                messages,
                model="qwen-plus",
                api_client=_StubLLMClient("unused"),
                preserve_recent=2,
                raise_on_failure=True,
            )

        message = str(caught.value)
        assert (
            message == "summarization failed: RequestFailure (HTTP 400): bad request for [redacted]"
        )
        assert "sk-secret-value" not in message

    @pytest.mark.asyncio
    async def test_explicit_empty_summary_reports_no_usable_summary(self) -> None:
        messages = [_user_text(f"msg-{i}") for i in range(5)]

        with pytest.raises(
            FullCompactError,
            match="summarizer returned no usable summary",
        ):
            await full_compact(
                messages,
                model="qwen-plus",
                api_client=_StubLLMClient("   "),
                preserve_recent=2,
                raise_on_failure=True,
            )


# ---------------------------------------------------------------------------
# 5. Orchestrator escalation order
# ---------------------------------------------------------------------------


class TestAutoCompactEscalation:
    @pytest.mark.asyncio
    async def test_default_l4_timeout_is_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, float] = {}

        async def _capture_timeout(
            messages: list[ConversationMessage], **kwargs: object
        ) -> tuple[list[ConversationMessage], bool]:
            seen["timeout_seconds"] = float(kwargs["timeout_seconds"])
            return messages, False

        monkeypatch.setattr("openharness.services.compact.full_compact", _capture_timeout)
        messages = [_user_text("x" * 1_000) for _ in range(200)]

        await auto_compact_if_needed(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
        )

        assert seen["timeout_seconds"] == 120.0

    @pytest.mark.asyncio
    async def test_below_threshold_no_op(self) -> None:
        # 2 short messages → way under any threshold
        client = _StubLLMClient(response="<summary>x</summary>")
        messages = [_user_text("hi"), _user_text("hello")]
        new_messages, result = await auto_compact_if_needed(
            messages, model="qwen-plus", api_client=client
        )
        assert result.compact_kind == "none"
        assert result.applied_levels == (0,)
        assert client.call_count == 0
        assert new_messages == messages

    @pytest.mark.asyncio
    async def test_disabled_skips_everything(self) -> None:
        # Even with lots of tokens, disabled=False short-circuits
        long = "x" * 100_000
        client = _StubLLMClient(response="<summary>x</summary>")
        messages = [_user_text(long) for _ in range(20)]
        _new_messages, result = await auto_compact_if_needed(
            messages, model="qwen-plus", api_client=client, enabled=False
        )
        assert result.compact_kind == "none"
        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_l2_alone_sufficient(self) -> None:
        # Messages with long bodies that L2 can collapse — total
        # falls under threshold after collapse, L3/L4 don't run.
        # ``qwen-plus`` threshold @ 0.83 = 26_560. We need a value
        # where original is > threshold but l2-collapsed is < threshold.
        client = _StubLLMClient(response="<summary>x</summary>")
        # 20 messages each with ~3000 chars → ~60k chars → ~15k tokens
        # *4/3 = ~20k. Under threshold. Hmm.
        # Let me make them bigger: each 6000 chars → 120k chars → 30k+ tokens
        long = "x" * 6_000
        messages = [_user_text(long) for _ in range(20)]
        _new_messages, result = await auto_compact_if_needed(
            messages, model="qwen-plus", api_client=client
        )
        # L2 collapses each long message (head 900 + tail 500 + marker)
        # ≈ 1400 chars per message *20 = 28k chars → ~7k tokens
        # 7k *4/3 ≈ 9k tokens → under 26k threshold
        assert result.compact_kind == "context_collapse"
        assert 2 in result.applied_levels
        assert client.call_count == 0  # LLM never called

    @pytest.mark.asyncio
    async def test_l3_kicks_in_when_l2_insufficient(self, tmp_path: Path) -> None:
        # L2 won't help — short messages, but lots of them
        # L3 has a fresh checkpoint to splice with
        checkpoint = tmp_path / "checkpoint.md"
        checkpoint.write_text("# Session Memory\nfresh state\n")
        client = _StubLLMClient(response="<summary>x</summary>")
        # 100 small messages *~300 chars = 30k chars → 7k tokens *4/3 = 10k
        # That's under threshold actually. Let me increase scale.
        long_enough = "x" * 1_000  # under L2 threshold
        # 200 messages *1000 chars = 200k chars → 50k tokens *4/3 = 67k tokens
        # Above 26k threshold → triggers compact.
        # L2 won't collapse (each message is under 2400 chars).
        # L3 should splice into checkpoint.
        messages = [_user_text(long_enough) for _ in range(200)]
        new_messages, result = await auto_compact_if_needed(
            messages,
            model="qwen-plus",
            api_client=client,
            session_memory_path=checkpoint,
        )
        assert result.compact_kind == "session_memory"
        assert 3 in result.applied_levels
        assert client.call_count == 0  # L3 doesn't call LLM
        # Synthetic checkpoint message + 12 preserved = 13 messages
        assert len(new_messages) == 13

    @pytest.mark.asyncio
    async def test_l4_when_l2_l3_insufficient(self) -> None:
        # L2 won't collapse (short messages); no checkpoint → L3 skips;
        # L4 LLM call.
        client = _StubLLMClient(response="<summary>compacted summary content</summary>")
        long_enough = "x" * 1_000
        messages = [_user_text(long_enough) for _ in range(200)]
        new_messages, result = await auto_compact_if_needed(
            messages,
            model="qwen-plus",
            api_client=client,
            session_memory_path=None,  # no checkpoint → skip L3
        )
        assert result.compact_kind == "full"
        assert 4 in result.applied_levels
        assert client.call_count == 1  # L4 called LLM once
        # Boundary marker + summary + 12 preserved = 14
        assert len(new_messages) == 14

    @pytest.mark.asyncio
    async def test_l4_failure_returns_none_kind(self) -> None:
        # L4 LLM exhausts retries → falls back to original messages
        client = _FailingLLMClient()
        long_enough = "x" * 1_000
        messages = [_user_text(long_enough) for _ in range(200)]
        _new_messages, result = await auto_compact_if_needed(
            messages,
            model="qwen-plus",
            api_client=client,
            session_memory_path=None,
        )
        assert result.compact_kind == "none"
        # Final tokens didn't change since nothing actually freed bytes
        assert result.final_tokens == result.original_tokens


# ---------------------------------------------------------------------------
# 6. CompactResult dataclass
# ---------------------------------------------------------------------------


class TestCompactResult:
    def test_no_op_factory(self) -> None:
        result = CompactResult.no_op(tokens=500)
        assert result.compact_kind == "none"
        assert result.applied_levels == (0,)
        assert result.original_tokens == 500
        assert result.final_tokens == 500

    def test_frozen(self) -> None:
        import dataclasses

        result = CompactResult.no_op(tokens=100)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.compact_kind = "full"  # type: ignore[misc]
