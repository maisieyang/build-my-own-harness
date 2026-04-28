"""ApiMessageRequest -- request shape for the LLM Messages API."""

from __future__ import annotations

from pydantic import Field

from openharness.protocols._base import StrictModel
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.tools import ToolSpec


class ApiMessageRequest(StrictModel):
    """Request to the LLM Messages API.

    Matches the Anthropic Messages API request body 1:1. Streaming defaults to
    ``True`` because the harness always streams; ``tools`` and ``system`` are
    optional; ``max_tokens`` is enforced positive at the schema level.
    """

    model: str
    max_tokens: int = Field(gt=0)
    system: str | None = None
    messages: list[ConversationMessage]
    stream: bool = True
    tools: list[ToolSpec] | None = None
