"""Tests for ApiMessageRequest."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.tools import ToolSpec


def _user_msg(text: str = "hi") -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


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

    def test_system_defaults_to_none(self) -> None:
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        assert req.system is None

    def test_with_system_prompt(self) -> None:
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            system="You are a helpful assistant.",
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        assert req.system == "You are a helpful assistant."

    def test_stream_defaults_to_true(self) -> None:
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            messages=[_user_msg()],
        )
        assert req.stream is True

    def test_stream_can_be_disabled(self) -> None:
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            stream=False,
            messages=[_user_msg()],
        )
        assert req.stream is False

    def test_max_tokens_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApiMessageRequest(
                model="claude-3-5-sonnet-latest",
                max_tokens=0,
                messages=[_user_msg()],
            )

    def test_max_tokens_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApiMessageRequest(
                model="claude-3-5-sonnet-latest",
                max_tokens=-100,
                messages=[_user_msg()],
            )


class TestApiMessageRequestWithTools:
    def test_tools_defaults_to_none(self) -> None:
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            messages=[_user_msg()],
        )
        assert req.tools is None

    def test_with_single_tool(self) -> None:
        weather = ToolSpec(
            name="get_weather",
            description="Get current weather",
            input_schema={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        )
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            tools=[weather],
            messages=[_user_msg()],
        )
        assert req.tools is not None
        assert len(req.tools) == 1
        assert req.tools[0].name == "get_weather"

    def test_with_multiple_tools(self) -> None:
        bash = ToolSpec(
            name="bash",
            description="Run shell command",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        )
        read = ToolSpec(
            name="read",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        req = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            tools=[bash, read],
            messages=[_user_msg()],
        )
        assert req.tools is not None
        assert {t.name for t in req.tools} == {"bash", "read"}

    def test_roundtrip_with_tools(self) -> None:
        original = ApiMessageRequest(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            tools=[
                ToolSpec(
                    name="search",
                    description="Search docs",
                    input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
                )
            ],
            messages=[_user_msg()],
        )
        loaded = ApiMessageRequest.model_validate_json(original.model_dump_json())
        assert loaded == original
        assert loaded.tools is not None
        assert loaded.tools[0].name == "search"
