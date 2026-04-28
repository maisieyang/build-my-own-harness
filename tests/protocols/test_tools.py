"""Tests for ToolSpec — the schema describing a tool to the LLM."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.protocols.tools import ToolSpec


class TestToolSpec:
    def test_construct(self) -> None:
        spec = ToolSpec(
            name="get_weather",
            description="Get current weather for a location",
            input_schema={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        )
        assert spec.name == "get_weather"
        assert spec.description == "Get current weather for a location"
        assert spec.input_schema["properties"]["location"]["type"] == "string"

    def test_input_schema_accepts_nested_dict(self) -> None:
        spec = ToolSpec(
            name="search",
            description="Search the web",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "filters": {
                        "type": "object",
                        "properties": {
                            "lang": {"type": "string"},
                            "k": {"type": "integer", "minimum": 1, "maximum": 50},
                        },
                    },
                },
                "required": ["query"],
            },
        )
        assert spec.input_schema["properties"]["filters"]["properties"]["k"]["maximum"] == 50

    def test_roundtrip(self) -> None:
        original = ToolSpec(
            name="bash",
            description="Run a shell command",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        )
        loaded = ToolSpec.model_validate_json(original.model_dump_json())
        assert loaded == original

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ToolSpec.model_validate(
                {
                    "name": "x",
                    "description": "y",
                    "input_schema": {"type": "object"},
                    "unexpected": "no",
                }
            )

    def test_missing_required_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolSpec.model_validate({"name": "x", "description": "y"})

    def test_real_anthropic_tool_shape(self) -> None:
        # Shape copied from Anthropic Messages API "tool use" docs.
        wire = {
            "name": "get_weather",
            "description": "Get the current weather in a given location",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "The unit of temperature",
                    },
                },
                "required": ["location"],
            },
        }
        spec = ToolSpec.model_validate(wire)
        assert spec.name == "get_weather"
        assert spec.input_schema["properties"]["unit"]["enum"] == ["celsius", "fahrenheit"]
        assert spec.input_schema["required"] == ["location"]
