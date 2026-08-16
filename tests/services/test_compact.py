"""Tests for the compact escalation pipeline.

4 surfaces:

1. token estimation + threshold computation
2. summary preparation clears old tool results with independent recency boundaries
3. full_compact (summarize call + 9-slot prompt + parse)
4. ``auto_compact_if_needed`` orchestration
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING

import pytest

from openharness.api.errors import RequestFailure
from openharness.protocols import ApiMessageRequest, ConversationMessage, TextBlock, ToolSpec
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
    compact_for_request_budget,
    estimate_message_tokens,
    estimate_request_input_tokens,
    full_compact,
    get_context_window,
    request_input_token_budget,
    threshold_tokens,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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

    def test_request_estimate_includes_system_and_tool_catalog(self) -> None:
        messages = [_user_text("hello")]
        conversation_only = estimate_message_tokens(messages, model="qwen-plus")
        request = ApiMessageRequest(
            model="qwen-plus",
            max_tokens=2_000,
            system="PROJECT_INSTRUCTION=" + "s" * 2_000,
            messages=messages,
            tools=[
                ToolSpec(
                    name="WarehouseQuery",
                    description="TOOL_DESCRIPTION=" + "d" * 2_000,
                    input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
                )
            ],
        )

        assert estimate_request_input_tokens(request) > conversation_only

    def test_request_budget_reserves_output_and_safety_margin(self) -> None:
        # qwen-plus has a 32k window. Reserve 2k output, then use 80% of
        # the remaining input capacity: (32_000 - 2_000) * .8 = 24_000.
        assert (
            request_input_token_budget("qwen-plus", max_output_tokens=2_000, threshold_ratio=0.8)
            == 24_000
        )

    @pytest.mark.asyncio
    async def test_forced_recompile_chooses_largest_protocol_valid_tail_that_fits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        messages = [
            _user_text("old request"),
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="Read", input={"path": "a.py"})],
            ),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="t1", content="old result")],
            ),
            _user_text("follow-up"),
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="t2", name="Grep", input={"pattern": "x"})],
            ),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="t2", content="recent result")],
            ),
            _user_text("final request"),
        ]
        seen: dict[str, int] = {}

        async def _capture_full(
            current: list[ConversationMessage], **kwargs: object
        ) -> tuple[list[ConversationMessage], bool]:
            preserve = int(kwargs["preserve_recent"])
            seen["preserve_recent"] = preserve
            return [_user_text("boundary"), _user_text("summary"), *current[-preserve:]], True

        monkeypatch.setattr("openharness.services.compact.full_compact", _capture_full)
        # The fake estimator makes 4 messages fit but 5 exceed the request
        # budget. A 2-message tool pair is protocol-valid; a suffix beginning
        # with its ToolResult is not.
        monkeypatch.setattr(
            "openharness.services.compact.estimate_message_tokens",
            lambda current, **_kwargs: len(current) * 10,
        )

        compacted, applied = await compact_for_request_budget(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            input_token_budget=55,
            request_overhead_tokens=10,
            preserve_recent_messages=12,
            full_compact_max_tokens=4,
        )

        assert applied is True
        assert seen["preserve_recent"] == 4
        assert compacted[-4:] == messages[-4:]


# ---------------------------------------------------------------------------
# 2. Summary preparation
# ---------------------------------------------------------------------------


class TestSummaryPreparation:
    @pytest.mark.asyncio
    async def test_tool_cleanup_is_generic_and_keeps_three_latest_interactions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        async def _capture(**kwargs: object) -> str:
            seen["messages"] = kwargs["messages"]
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _capture)
        older: list[ConversationMessage] = []
        cases = [
            ("read", "Read", False, "READ_BODY"),
            ("bash", "Bash", False, "BASH_BODY"),
            ("mcp", "mcp__warehouse__query", True, "MCP_ERROR_BODY"),
            ("agent", "Agent", False, "AGENT_BODY"),
            ("skill", "LoadSkill", False, "SKILL_BODY"),
            ("web", "WebFetch", False, "WEB_BODY"),
        ]
        for tool_use_id, name, is_error, body in cases:
            older.extend(
                [
                    ConversationMessage(
                        role="assistant",
                        content=[ToolUseBlock(id=tool_use_id, name=name, input={"value": name})],
                    ),
                    ConversationMessage(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id=tool_use_id,
                                content=body,
                                is_error=is_error,
                            )
                        ],
                    ),
                ]
            )
        recent = [_user_text(f"recent-{i}") for i in range(12)]

        new_messages, changed = await full_compact(
            [*older, *recent],
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=12,
        )

        assert changed is True
        summary_messages = seen["messages"]
        assert isinstance(summary_messages, list)
        summary_input = repr(summary_messages[:-1])
        assert "READ_BODY" not in summary_input
        assert "BASH_BODY" not in summary_input
        assert "MCP_ERROR_BODY" not in summary_input
        assert "input={'value': 'Read'}" in summary_input
        assert "input={'value': 'Bash'}" in summary_input
        assert "input={'value': 'mcp__warehouse__query'}" in summary_input
        assert summary_input.count("[cleared]") == 3
        # The latest three completed interactions are protected independently
        # of the 12-message Summary tail.
        assert "AGENT_BODY" in summary_input
        assert "SKILL_BODY" in summary_input
        assert "WEB_BODY" in summary_input
        assert new_messages[-12:] == recent

    @pytest.mark.asyncio
    async def test_recent_message_tail_and_recent_tools_are_parallel_protections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        async def _capture(**kwargs: object) -> str:
            seen["messages"] = kwargs["messages"]
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _capture)
        interactions: list[ConversationMessage] = []
        for index in range(5):
            tool_use_id = f"tool-{index}"
            interactions.extend(
                [
                    ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id=tool_use_id,
                                name=f"Tool{index}",
                                input={"index": index},
                            )
                        ],
                    ),
                    ConversationMessage(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id=tool_use_id,
                                content=f"RESULT_{index}",
                            )
                        ],
                    ),
                ]
            )

        # The last four interactions occupy the eight-message recent tail.
        # Tool1 is not in the latest-three tool set, but must remain intact
        # because message recency is an independent protection.
        new_messages, changed = await full_compact(
            interactions,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=8,
        )

        assert changed is True
        assert new_messages[-8:] == interactions[-8:]
        summary_input = repr(seen["messages"])
        assert "RESULT_0" not in summary_input
        assert "[cleared]" in summary_input

    @pytest.mark.asyncio
    async def test_parallel_tool_batch_is_cleaned_per_tool_use_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        async def _capture(**kwargs: object) -> str:
            seen["messages"] = kwargs["messages"]
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _capture)
        batched_tools = ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(id=f"batch-{index}", name=f"Tool{index}", input={"i": index})
                for index in range(4)
            ],
        )
        batched_results = ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id=f"batch-{index}", content=f"BATCH_RESULT_{index}")
                for index in range(4)
            ],
        )
        recent = [_user_text(f"recent-{index}") for index in range(12)]

        await full_compact(
            [batched_tools, batched_results, *recent],
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=12,
        )

        summary_input = repr(seen["messages"])
        assert "BATCH_RESULT_0" not in summary_input
        assert "BATCH_RESULT_1" in summary_input
        assert "BATCH_RESULT_2" in summary_input
        assert "BATCH_RESULT_3" in summary_input
        assert "input={'i': 0}" in summary_input

    @pytest.mark.asyncio
    async def test_user_and_assistant_text_are_never_cleared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}
        long_user = "USER_FACT=" + "u" * 5_000
        long_assistant = "ASSISTANT_DECISION=" + "a" * 5_000

        async def _capture(**kwargs: object) -> str:
            seen["messages"] = kwargs["messages"]
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _capture)
        messages = [
            _user_text(long_user),
            ConversationMessage(role="assistant", content=[TextBlock(text=long_assistant)]),
            *[_user_text(f"recent-{i}") for i in range(2)],
        ]

        await full_compact(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=2,
        )

        assert long_user in repr(seen["messages"])
        assert long_assistant in repr(seen["messages"])

    @pytest.mark.asyncio
    async def test_recent_tool_result_is_preserved_byte_for_byte(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _summary(**_kwargs: object) -> str:
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _summary)
        recent = [
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="recent-read", name="Read", input={"path": "x"})],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="recent-read",
                        content="RECENT_READ_BODY" * 500,
                    )
                ],
            ),
        ]
        messages = [_user_text("old-a"), _user_text("old-b"), *recent]

        new_messages, changed = await full_compact(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=2,
        )

        assert changed is True
        assert new_messages[-2:] == recent

    @pytest.mark.asyncio
    async def test_orphan_tool_result_is_not_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, object] = {}

        async def _capture(**kwargs: object) -> str:
            seen["messages"] = kwargs["messages"]
            return "<summary>compact</summary>"

        monkeypatch.setattr("openharness.services.compact.summarize", _capture)
        messages = [
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="missing", content="ORPHAN_BODY")],
            ),
            _user_text("old"),
            _user_text("recent"),
        ]

        await full_compact(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=1,
        )

        assert "ORPHAN_BODY" in repr(seen["messages"])

    @pytest.mark.asyncio
    async def test_summary_failure_returns_exact_original_after_private_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fail(**_kwargs: object) -> str:
            raise RequestFailure("summary unavailable")

        monkeypatch.setattr("openharness.services.compact.summarize", _fail)
        messages = [
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="old-read", name="Read", input={"path": "x.py"})],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="old-read",
                        content="EXACT_READ_BODY" * 500,
                    )
                ],
            ),
            _user_text("recent"),
        ]

        new_messages, changed = await full_compact(
            messages,
            model="qwen-plus",
            api_client=_StubLLMClient("unused"),
            preserve_recent=1,
        )

        assert changed is False
        assert new_messages == messages


# ---------------------------------------------------------------------------
# 3. L4 full_compact
# ---------------------------------------------------------------------------


class TestL4FullCompact:
    @pytest.mark.asyncio
    async def test_prompt_defines_minimal_handoff_contract(
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
        for section in (
            "Current Objective",
            "Current State",
            "Verified Evidence",
            "Decisions and Constraints",
            "Active Artifacts",
            "Remaining Work",
        ):
            assert section in prompt
        assert "acceptance criteria" in prompt
        assert "Do not invent" in prompt
        assert "facts, user instructions, and model inferences" in prompt
        assert "provenance" in prompt
        assert "verbatim" in prompt
        assert "Later explicit evidence supersedes stale state" in prompt
        assert "filler" in prompt
        assert "All User Messages" not in prompt
        assert "Optional Next Step" not in prompt
        assert "<analysis>" not in prompt
        summary_messages = seen["messages"]
        assert isinstance(summary_messages, list)
        final_message = summary_messages[-1]
        assert isinstance(final_message, ConversationMessage)
        assert final_message.role == "user"
        final_text = final_message.content[0]
        assert isinstance(final_text, TextBlock)
        assert "create the handoff state now" in final_text.text.lower()
        assert "do not continue or imitate" in final_text.text.lower()
        assert "uppercase key=value" not in final_text.text.lower()
        assert "synthetic tool-use envelope" not in final_text.text.lower()

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
# 4. Orchestrator escalation order
# ---------------------------------------------------------------------------


class TestAutoCompactEscalation:
    def test_public_surface_has_no_session_memory_checkpoint(self) -> None:
        """Auto compact must not expose the retired L3 checkpoint input."""
        parameters = inspect.signature(auto_compact_if_needed).parameters
        assert "session_memory_path" not in parameters

    @pytest.mark.asyncio
    async def test_recent_message_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            await auto_compact_if_needed(
                [_user_text("hello")],
                model="qwen-plus",
                api_client=_StubLLMClient("unused"),
                preserve_recent_messages=0,
            )

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
    async def test_request_overhead_can_trigger_compact_when_messages_alone_fit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        messages = [_user_text(f"message-{index}") for index in range(13)]
        client = _StubLLMClient(response="<summary>x</summary>")
        monkeypatch.setattr(
            "openharness.services.compact.estimate_message_tokens",
            lambda current, **_kwargs: len(current) * 10,
        )

        _new_messages, result = await auto_compact_if_needed(
            messages,
            model="qwen-plus",
            api_client=client,
            input_token_budget=150,
            request_overhead_tokens=30,
        )

        # Conversation=130 would fit. Full Draft Request=160 does not.
        assert result.original_tokens == 160
        assert result.compact_kind == "full"
        assert client.call_count == 1

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
    async def test_tool_cleanup_below_threshold_skips_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _StubLLMClient(response="<summary>must not run</summary>")
        messages: list[ConversationMessage] = []
        for index in range(4):
            tool_use_id = f"tool-{index}"
            messages.extend(
                [
                    ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id=tool_use_id,
                                name=f"mcp__server__tool_{index}",
                                input={"index": index},
                            )
                        ],
                    ),
                    ConversationMessage(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id=tool_use_id,
                                content=f"RAW_RESULT_{index}",
                            )
                        ],
                    ),
                ]
            )
        messages.append(_user_text("recent"))

        def _estimate(current: list[ConversationMessage], **_kwargs: object) -> int:
            return 100 if "RAW_RESULT_0" in repr(current) else 40

        monkeypatch.setattr("openharness.services.compact.estimate_message_tokens", _estimate)
        monkeypatch.setattr(
            "openharness.services.compact.threshold_tokens",
            lambda *_args, **_kwargs: 50,
        )

        new_messages, result = await auto_compact_if_needed(
            messages,
            model="qwen-plus",
            api_client=client,
            preserve_recent_messages=1,
        )

        assert client.call_count == 0
        assert result.compact_kind == "tool_results"
        assert result.applied_levels == (0, 2)
        assert result.original_tokens == 100
        assert result.final_tokens == 40
        assert "RAW_RESULT_0" not in repr(new_messages)
        assert repr(new_messages).count("[cleared]") == 1

    @pytest.mark.asyncio
    async def test_above_threshold_always_runs_full_compact(self) -> None:
        # Long user messages must not be globally folded as a cheap substitute
        # for semantic compaction. Crossing the threshold always attempts L4.
        client = _StubLLMClient(response="<summary>compacted summary content</summary>")
        messages = [_user_text("USER_CONSTRAINT=" + "x" * 6_000) for _ in range(20)]
        new_messages, result = await auto_compact_if_needed(
            messages,
            model="qwen-plus",
            api_client=client,
        )
        assert result.compact_kind == "full"
        assert result.applied_levels == (0, 4)
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
