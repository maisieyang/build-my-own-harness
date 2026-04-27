"""ConversationMessage — a single turn in the user/assistant exchange."""

from __future__ import annotations

from typing import Literal

from openharness.protocols._base import StrictModel
from openharness.protocols.content import ContentBlock


class ConversationMessage(StrictModel):
    """A single message in the conversation history.

    The Anthropic API accepts ``content`` as either a plain string or a list of blocks; we
    always store ``list[ContentBlock]`` internally. String → list normalization is the API
    client layer's job (Module 3).
    """

    role: Literal["user", "assistant"]
    content: list[ContentBlock]
