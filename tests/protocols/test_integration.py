"""Integration tests across protocol modules.

Verifies that the protocol layer composes end-to-end:
1. The public API is reachable via top-level ``from openharness.protocols import ...``
2. A complete ``ApiMessageRequest`` containing every ContentBlock variant +
   tools survives a full JSON roundtrip with type identity preserved.
"""

from __future__ import annotations


def test_public_api_re_exports_from_package_root() -> None:
    """Every protocol type the rest of the codebase needs is importable from
    ``openharness.protocols`` directly — no need to know about submodules.
    """
    from openharness.protocols import (
        ApiMessageCompleteEvent,
        ApiMessageRequest,
        ApiRetryEvent,
        ApiStreamEvent,
        ApiTextDeltaEvent,
        ContentBlock,
        ConversationMessage,
        ImageBlock,
        ImageSource,
        TextBlock,
        ToolResultBlock,
        ToolSpec,
        ToolUseBlock,
        UsageSnapshot,
    )

    # Sanity: each name resolved to something (no AttributeError in import above).
    public_names = [
        ApiMessageCompleteEvent,
        ApiMessageRequest,
        ApiRetryEvent,
        ApiStreamEvent,
        ApiTextDeltaEvent,
        ContentBlock,
        ConversationMessage,
        ImageBlock,
        ImageSource,
        TextBlock,
        ToolResultBlock,
        ToolSpec,
        ToolUseBlock,
        UsageSnapshot,
    ]
    assert all(name is not None for name in public_names)


def test_full_request_roundtrip_with_all_block_types_and_tools() -> None:
    """End-to-end wire-format integrity: a request that exercises every major
    protocol type must roundtrip through JSON with all type tags preserved.

    Exercises:
    - TextBlock + ToolUseBlock + ToolResultBlock + ImageBlock + ImageSource
    - ConversationMessage with mixed content
    - ToolSpec with realistic JSON Schema
    - ApiMessageRequest with stream=True, tools, system, max_tokens
    """
    from openharness.protocols import (
        ApiMessageRequest,
        ContentBlock,  # noqa: F401  (proves the alias is exposed)
        ConversationMessage,
        ImageBlock,
        ImageSource,
        TextBlock,
        ToolResultBlock,
        ToolSpec,
        ToolUseBlock,
    )

    weather_tool = ToolSpec(
        name="get_weather",
        description="Get current weather",
        input_schema={
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    )

    user_with_image = ConversationMessage(
        role="user",
        content=[
            TextBlock(text="What's in this image and the weather where it was taken?"),
            ImageBlock(
                source=ImageSource(media_type="image/png", data="base64-encoded-data"),
            ),
        ],
    )

    assistant_with_tool_use = ConversationMessage(
        role="assistant",
        content=[
            TextBlock(text="Let me look up the weather."),
            ToolUseBlock(
                id="toolu_01ABC",
                name="get_weather",
                input={"location": "San Francisco, CA"},
            ),
        ],
    )

    user_with_tool_result = ConversationMessage(
        role="user",
        content=[
            ToolResultBlock(
                tool_use_id="toolu_01ABC",
                content="Sunny, 72F",
                is_error=False,
            ),
        ],
    )

    original = ApiMessageRequest(
        model="claude-3-5-sonnet-latest",
        max_tokens=4096,
        system="You are a helpful assistant.",
        messages=[user_with_image, assistant_with_tool_use, user_with_tool_result],
        stream=True,
        tools=[weather_tool],
    )

    # Wire-format roundtrip
    json_str = original.model_dump_json()
    loaded = ApiMessageRequest.model_validate_json(json_str)

    # Equality preserved end-to-end
    assert loaded == original

    # Type tags survived discriminated-union dispatch
    assert isinstance(loaded.messages[0].content[0], TextBlock)
    assert isinstance(loaded.messages[0].content[1], ImageBlock)
    assert isinstance(loaded.messages[1].content[1], ToolUseBlock)
    assert isinstance(loaded.messages[2].content[0], ToolResultBlock)

    # Cross-block reference (tool_use_id ↔ tool_use.id) intact
    tool_use = loaded.messages[1].content[1]
    tool_result = loaded.messages[2].content[0]
    assert isinstance(tool_use, ToolUseBlock)
    assert isinstance(tool_result, ToolResultBlock)
    assert tool_result.tool_use_id == tool_use.id

    # Tools survived
    assert loaded.tools is not None
    assert loaded.tools[0].name == "get_weather"
