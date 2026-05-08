"""Tests for messages.py helpers — P2-T1 sub-unit 1b.

Two contract properties hold for every helper that takes a ``messages`` list:

1. The returned list is correct (right shape, right tail).
2. The input list is NOT mutated — verified by deep-copy comparison.
"""

from __future__ import annotations

import copy

from openharness.engine.messages import (
    append_assistant_message,
    append_tool_results,
    append_user_text,
    extract_tool_uses,
)
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class TestAppendUserText:
    def test_appends_user_text_message(self) -> None:
        result = append_user_text([], "hello")
        assert len(result) == 1
        assert result[0].role == "user"
        assert isinstance(result[0].content[0], TextBlock)
        assert result[0].content[0].text == "hello"

    def test_preserves_existing_messages_in_order(self) -> None:
        prior = ConversationMessage(role="assistant", content=[TextBlock(text="prior")])
        result = append_user_text([prior], "follow-up")
        assert len(result) == 2
        assert result[0] == prior
        assert isinstance(result[1].content[0], TextBlock)
        assert result[1].content[0].text == "follow-up"

    def test_does_not_mutate_input(self) -> None:
        messages = [
            ConversationMessage(role="user", content=[TextBlock(text="prior")]),
        ]
        snapshot = copy.deepcopy(messages)
        append_user_text(messages, "new")
        assert messages == snapshot


class TestAppendAssistantMessage:
    def test_appends_assistant_with_text_content(self) -> None:
        block = TextBlock(text="answer")
        result = append_assistant_message([], [block])
        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].content == [block]

    def test_appends_assistant_with_tool_use_content(self) -> None:
        # Mirrors the post-stream case: assistant emits a tool_use block.
        block = ToolUseBlock(id="t1", name="Bash", input={"cmd": "ls"})
        result = append_assistant_message([], [block])
        assert isinstance(result[0].content[0], ToolUseBlock)

    def test_does_not_mutate_input(self) -> None:
        messages = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
        snapshot = copy.deepcopy(messages)
        append_assistant_message(messages, [TextBlock(text="hi back")])
        assert messages == snapshot


class TestAppendToolResults:
    def test_bundles_multiple_results_into_one_user_message(self) -> None:
        r1 = ToolResultBlock(tool_use_id="t1", content="ok")
        r2 = ToolResultBlock(tool_use_id="t2", content="also ok")
        result = append_tool_results([], [r1, r2])
        # Anthropic requires all tool_results for a turn in ONE following user message.
        assert len(result) == 1
        assert result[0].role == "user"
        assert len(result[0].content) == 2
        assert result[0].content[0] == r1
        assert result[0].content[1] == r2

    def test_preserves_input_order(self) -> None:
        ids = [f"id_{i}" for i in range(3)]
        results = [ToolResultBlock(tool_use_id=i, content="ok") for i in ids]
        bundled = append_tool_results([], results)
        assert [
            block.tool_use_id  # type: ignore[union-attr]
            for block in bundled[0].content
        ] == ids

    def test_does_not_mutate_input(self) -> None:
        messages = [
            ConversationMessage(role="assistant", content=[TextBlock(text="hi")]),
        ]
        snapshot = copy.deepcopy(messages)
        append_tool_results(messages, [ToolResultBlock(tool_use_id="x", content="ok")])
        assert messages == snapshot


class TestExtractToolUses:
    def test_returns_only_tool_use_blocks_in_order(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="thinking..."),
                ToolUseBlock(id="t1", name="Read", input={"path": "x"}),
                TextBlock(text="more"),
                ToolUseBlock(id="t2", name="Bash", input={"cmd": "ls"}),
            ],
        )
        uses = extract_tool_uses(msg)
        assert [u.id for u in uses] == ["t1", "t2"]

    def test_empty_when_no_tool_uses(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content=[TextBlock(text="just text")],
        )
        assert extract_tool_uses(msg) == []
