"""Tests for ApiMessageRequest."""

from __future__ import annotations

from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest


class TestApiMessageRequest:
    def test_minimal_request(self) -> None:
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        assert req.model == "claude-3-5-sonnet-latest"
        assert req.max_tokens == 1024
        assert len(req.messages) == 1
