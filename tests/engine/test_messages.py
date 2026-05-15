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
    drop_oldest_tool_pair,
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


class TestDropOldestToolPair:
    """P4-T3.3b — drop the oldest assistant(tool_use)+user pair, keeping
    messages well-formed for the next request."""

    def _user(self, text: str) -> ConversationMessage:
        return ConversationMessage(role="user", content=[TextBlock(text=text)])

    def _assistant_tool_use(self, tool_use_id: str) -> ConversationMessage:
        return ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(id=tool_use_id, name="Read", input={"path": "/x"}),
            ],
        )

    def _tool_result(self, tool_use_id: str) -> ConversationMessage:
        return ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id=tool_use_id, content="ok")],
        )

    def test_drops_first_pair_when_multiple_exist(self) -> None:
        # 3 turns of tool use:t1, t2, t3. After drop, only t2 + t3 remain
        # (plus the initial user prompt).
        messages = [
            self._user("initial"),
            self._assistant_tool_use("t1"),
            self._tool_result("t1"),
            self._assistant_tool_use("t2"),
            self._tool_result("t2"),
            self._assistant_tool_use("t3"),
            self._tool_result("t3"),
        ]
        result = drop_oldest_tool_pair(messages)
        # Length shrinks by exactly 2 (1 pair dropped)
        assert len(result) == len(messages) - 2
        # The dropped pair is t1 — t2 is now the first tool_use
        first_assistant = next(m for m in result if m.role == "assistant")
        assert isinstance(first_assistant.content[0], ToolUseBlock)
        assert first_assistant.content[0].id == "t2"

    def test_drops_only_pair_in_2_turn_conversation(self) -> None:
        messages = [
            self._user("initial"),
            self._assistant_tool_use("t1"),
            self._tool_result("t1"),
        ]
        result = drop_oldest_tool_pair(messages)
        assert len(result) == 1
        assert result[0].role == "user"
        assert result[0].content[0].text == "initial"  # type: ignore[union-attr]

    def test_input_unchanged_when_no_tool_pair(self) -> None:
        # No assistant(tool_use) message present — caller cannot recover.
        messages = [
            self._user("initial prompt"),
        ]
        result = drop_oldest_tool_pair(messages)
        assert result == messages

    def test_input_list_not_mutated(self) -> None:
        messages = [
            self._user("initial"),
            self._assistant_tool_use("t1"),
            self._tool_result("t1"),
        ]
        snapshot = copy.deepcopy(messages)
        drop_oldest_tool_pair(messages)
        assert messages == snapshot

    def test_returns_new_list_not_same_object(self) -> None:
        messages = [self._user("hi")]
        result = drop_oldest_tool_pair(messages)
        assert result is not messages  # new list

    def test_preserves_messages_after_dropped_pair(self) -> None:
        messages = [
            self._user("initial"),
            self._assistant_tool_use("t1"),
            self._tool_result("t1"),
            self._user("follow-up"),
            self._assistant_tool_use("t2"),
            self._tool_result("t2"),
        ]
        result = drop_oldest_tool_pair(messages)
        # Followup user message + t2 pair all preserved
        assert any(
            isinstance(m.content[0], TextBlock) and m.content[0].text == "follow-up" for m in result
        )
        assert any(
            isinstance(m.content[0], ToolUseBlock) and m.content[0].id == "t2" for m in result
        )

    def test_orphan_assistant_tool_use_at_end_not_dropped(self) -> None:
        # Pathological:assistant tool_use with no following user — we
        # shouldn't drop it because there's nothing well-formed about
        # dropping the assistant alone (it'd still be the orphan in the
        # surviving messages). Caller has to handle by re-raising.
        messages = [
            self._user("initial"),
            self._assistant_tool_use("t1"),  # no companion user
        ]
        result = drop_oldest_tool_pair(messages)
        assert result == messages
