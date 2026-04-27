"""UsageSnapshot — token counts from a single LLM API call."""

from __future__ import annotations

from pydantic import Field

from openharness.protocols._base import StrictModel


class UsageSnapshot(StrictModel):
    """Token counts for one LLM API call.

    Anthropic's response also includes ``cache_creation_input_tokens`` and
    ``cache_read_input_tokens``. We add those when prompt caching lands (later phase).

    ``total_tokens`` is exposed as a plain ``@property`` (not ``@computed_field``) so it
    is *not* included in serialization — Anthropic wire format does not have this field.
    """

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
