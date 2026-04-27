"""Tests for ConversationMessage."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage


class TestConversationMessage:
    def test_user_text_message(self) -> None:
        msg = ConversationMessage(role="user", content=[TextBlock(text="hi")])
        assert msg.role == "user"
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextBlock)

    def test_assistant_with_tool_use(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="I'll check that."),
                ToolUseBlock(id="toolu_1", name="bash", input={"cmd": "ls"}),
            ],
        )
        assert isinstance(msg.content[1], ToolUseBlock)

    def test_user_with_tool_result(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="toolu_1", content="output")],
        )
        assert isinstance(msg.content[0], ToolResultBlock)

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConversationMessage.model_validate(
                {"role": "system", "content": [{"type": "text", "text": "hi"}]}
            )

    def test_empty_content_allowed(self) -> None:
        # Allowed for now; API client (Module 3) will reject before sending if needed.
        msg = ConversationMessage(role="user", content=[])
        assert msg.content == []

    def test_roundtrip_with_mixed_content(self) -> None:
        original = ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="checking..."),
                ToolUseBlock(id="t1", name="bash", input={"c": "ls"}),
            ],
        )
        loaded = ConversationMessage.model_validate_json(original.model_dump_json())
        assert loaded == original
        assert isinstance(loaded.content[1], ToolUseBlock)

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ConversationMessage.model_validate({"role": "user", "content": [], "extra_meta": "x"})

    def test_wire_format_user_with_tool_result(self) -> None:
        # Real Anthropic shape: user reply containing a tool_result block.
        wire = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
                    "content": "Sunny, 72F",
                    "is_error": False,
                }
            ],
        }
        msg = ConversationMessage.model_validate(wire)
        assert msg.role == "user"
        assert isinstance(msg.content[0], ToolResultBlock)
