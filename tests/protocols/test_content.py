"""Tests for ContentBlock types and the discriminated union dispatch."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from openharness.protocols.content import (
    ContentBlock,
    ImageBlock,
    ImageSource,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

# TypeAdapter lets us validate against a non-BaseModel type (an Annotated alias here).
# mypy strict requires the generic parameter explicitly — TypeAdapter is Generic[T].
_BLOCK_ADAPTER: TypeAdapter[ContentBlock] = TypeAdapter(ContentBlock)


class TestTextBlock:
    def test_construct(self) -> None:
        block = TextBlock(text="hello")
        assert block.type == "text"
        assert block.text == "hello"

    def test_roundtrip_json(self) -> None:
        original = TextBlock(text="round trip")
        loaded = TextBlock.model_validate_json(original.model_dump_json())
        assert loaded == original

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TextBlock.model_validate({"type": "text", "text": "hi", "unexpected": "x"})

    def test_assignment_revalidation(self) -> None:
        block = TextBlock(text="hi")
        with pytest.raises(ValidationError):
            block.text = 42  # type: ignore[assignment]


class TestToolUseBlock:
    def test_construct(self) -> None:
        block = ToolUseBlock(id="toolu_123", name="bash", input={"command": "ls"})
        assert block.type == "tool_use"
        assert block.input == {"command": "ls"}

    def test_input_accepts_nested_dict(self) -> None:
        block = ToolUseBlock(
            id="toolu_1",
            name="search",
            input={"query": "hi", "filters": {"lang": "en", "k": 5}},
        )
        assert block.input["filters"]["k"] == 5


class TestToolResultBlock:
    def test_default_no_error(self) -> None:
        block = ToolResultBlock(tool_use_id="toolu_123", content="output")
        assert block.is_error is False

    def test_with_error(self) -> None:
        block = ToolResultBlock(tool_use_id="toolu_1", content="failed", is_error=True)
        assert block.is_error is True


class TestImageBlock:
    def test_construct(self) -> None:
        source = ImageSource(media_type="image/png", data="base64data")
        block = ImageBlock(source=source)
        assert block.type == "image"
        assert block.source.media_type == "image/png"


class TestContentBlockDiscriminator:
    """``Annotated[Union[...], Field(discriminator="type")]`` dispatch."""

    def test_dispatch_text(self) -> None:
        block = _BLOCK_ADAPTER.validate_python({"type": "text", "text": "hello"})
        assert isinstance(block, TextBlock)
        assert block.text == "hello"

    def test_dispatch_tool_use(self) -> None:
        block = _BLOCK_ADAPTER.validate_python(
            {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"cmd": "ls"}}
        )
        assert isinstance(block, ToolUseBlock)
        assert block.name == "bash"

    def test_dispatch_tool_result(self) -> None:
        block = _BLOCK_ADAPTER.validate_python(
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}
        )
        assert isinstance(block, ToolResultBlock)
        assert block.is_error is False

    def test_dispatch_image(self) -> None:
        block = _BLOCK_ADAPTER.validate_python(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "b64"},
            }
        )
        assert isinstance(block, ImageBlock)
        assert block.source.data == "b64"

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _BLOCK_ADAPTER.validate_python({"type": "made_up_block"})

    def test_missing_discriminator_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _BLOCK_ADAPTER.validate_python({"text": "missing type field"})


class TestRealAnthropicWireFormat:
    """Shapes copied from Anthropic Messages API docs."""

    def test_assistant_response_with_tool_use(self) -> None:
        # Mimics response.content from a tool-using assistant turn.
        wire = [
            {"type": "text", "text": "I'll check the weather."},
            {
                "type": "tool_use",
                "id": "toolu_01A09q90qw90lq917835lq9",
                "name": "get_weather",
                "input": {"location": "San Francisco, CA"},
            },
        ]
        blocks = [_BLOCK_ADAPTER.validate_python(item) for item in wire]
        assert isinstance(blocks[0], TextBlock)
        assert isinstance(blocks[1], ToolUseBlock)
        assert blocks[1].input == {"location": "San Francisco, CA"}

    def test_user_followup_with_tool_result(self) -> None:
        wire = [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
                "content": "Sunny, 72F",
                "is_error": False,
            },
        ]
        blocks = [_BLOCK_ADAPTER.validate_python(item) for item in wire]
        assert isinstance(blocks[0], ToolResultBlock)
        assert blocks[0].tool_use_id.startswith("toolu_")
