"""ContentBlock types — text, image, tool_use, tool_result.

Modeled to match the Anthropic Messages API wire format so a JSON response can be
``TypeAdapter(ContentBlock).validate_python(...)``-ed directly. See
``decisions/02-protocols.md``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field

from openharness.protocols._base import StrictModel


class TextBlock(StrictModel):
    """Plain text from user or assistant."""

    type: Literal["text"] = "text"
    text: str


class ImageSource(StrictModel):
    """Base64-encoded image source. URL sources are not yet supported."""

    type: Literal["base64"] = "base64"
    media_type: str
    data: str


class ImageBlock(StrictModel):
    """Image content (currently base64 sources only)."""

    type: Literal["image"] = "image"
    source: ImageSource


class ToolUseBlock(StrictModel):
    """Assistant invoking a tool. ``id`` correlates with the matching ``ToolResultBlock``."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlock(StrictModel):
    """Result of a tool invocation, returned to the model on the next turn.

    ``tool_use_id`` must match a ``ToolUseBlock.id`` from the previous assistant message.
    Anthropic also allows ``content`` to be a list of nested blocks; we support string-only
    for now and extend if/when actually needed.
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


# Discriminated union — Pydantic dispatches by the ``type`` field at parse time.
# TS-equivalent: ``type ContentBlock = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock``
ContentBlock: TypeAlias = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]
