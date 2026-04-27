"""ApiMessageRequest -- request shape for the LLM Messages API."""

from __future__ import annotations

from openharness.protocols._base import StrictModel
from openharness.protocols.messages import ConversationMessage


class ApiMessageRequest(StrictModel):
    """Request to the LLM Messages API.

    Minimal shape for now: model selection, output token cap, and the conversation
    history. ``system`` / ``tools`` / ``temperature`` / ``stream`` arrive in subsequent
    micro-cycles.
    """

    model: str
    max_tokens: int
    system: str | None = None
    messages: list[ConversationMessage]
