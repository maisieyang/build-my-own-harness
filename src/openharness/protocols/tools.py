"""ToolSpec -- schema describing a tool to the LLM.

Matches the shape of Anthropic Messages API ``tools`` entries 1:1, so a tool
description constructed here can be sent verbatim. ``input_schema`` is a JSON
Schema dict, kept untyped because JSON Schema is structurally open-ended.
"""

from __future__ import annotations

from typing import Any

from openharness.protocols._base import StrictModel


class ToolSpec(StrictModel):
    """Schema describing a tool the LLM can choose to invoke."""

    name: str
    description: str
    input_schema: dict[str, Any]
