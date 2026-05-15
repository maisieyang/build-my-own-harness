"""Tests for ``McpToolAdapter`` + ``_synth_input_model`` — P5-T3.

Three responsibility surfaces:

1. **Schema synthesis** (`_synth_input_model`):JSON Schema subset →
   Pydantic model with right types + required/optional behavior.
2. **Trust gating** (`McpToolAdapter._resolve_read_only`):trusted server
   honors `readOnlyHint`;untrusted forces `is_read_only=False`.
3. **Round-trip via real subprocess** (smoke):construct adapter from
   real `tools/list` result + invoke `execute` end-to-end.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from openharness.mcp import McpClient, McpServerConfig, McpToolAdapter
from openharness.mcp.adapter import _synth_input_model
from openharness.tools.base import ToolExecutionContext

_TEST_SERVER = Path(__file__).parent / "_test_server.py"


def _python_server_cfg(name: str = "Echo") -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command=(sys.executable, str(_TEST_SERVER)),
    )


# --------------------------------------------------------------------------- #
# _synth_input_model — JSON Schema → Pydantic                                 #
# --------------------------------------------------------------------------- #


class TestSynthInputModel:
    def test_empty_schema_yields_empty_model(self) -> None:
        model = _synth_input_model(None)
        instance = model()  # no fields
        assert isinstance(instance, BaseModel)
        assert instance.model_dump() == {}

    def test_object_with_no_properties_yields_empty(self) -> None:
        model = _synth_input_model({"type": "object", "properties": {}})
        instance = model()
        assert instance.model_dump() == {}

    @pytest.mark.parametrize(
        ("json_type", "py_value", "rejected"),
        [
            ("string", "hello", 123),
            ("integer", 42, "not-an-int"),
            ("number", 3.14, "nan-text"),
            ("boolean", True, "true-as-string"),
        ],
    )
    def test_primitive_types_map_correctly(
        self, json_type: str, py_value: Any, rejected: Any
    ) -> None:
        model = _synth_input_model(
            {
                "type": "object",
                "properties": {"x": {"type": json_type}},
                "required": ["x"],
            }
        )
        # Valid value parses.
        assert model(x=py_value).model_dump() == {"x": py_value}
        # Wrong type rejected by Pydantic.
        with pytest.raises(ValidationError):
            model(x=rejected)

    def test_required_vs_optional(self) -> None:
        model = _synth_input_model(
            {
                "type": "object",
                "properties": {
                    "needed": {"type": "string"},
                    "extra": {"type": "string"},
                },
                "required": ["needed"],
            }
        )
        # Required missing → error.
        with pytest.raises(ValidationError):
            model()
        # Optional missing → defaults to None.
        assert model(needed="hi").model_dump() == {"needed": "hi", "extra": None}

    def test_enum_maps_to_literal(self) -> None:
        model = _synth_input_model(
            {
                "type": "object",
                "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
                "required": ["color"],
            }
        )
        assert model(color="red").model_dump() == {"color": "red"}
        with pytest.raises(ValidationError):
            model(color="yellow")

    def test_array_and_object_map_to_list_and_dict(self) -> None:
        model = _synth_input_model(
            {
                "type": "object",
                "properties": {
                    "tags": {"type": "array"},
                    "meta": {"type": "object"},
                },
                "required": ["tags", "meta"],
            }
        )
        assert model(tags=["a", "b"], meta={"k": "v"}).model_dump() == {
            "tags": ["a", "b"],
            "meta": {"k": "v"},
        }

    def test_unsupported_type_falls_back_to_any(self) -> None:
        # $ref / oneOf / anyOf / null / etc. → Any (per D15.8)
        model = _synth_input_model(
            {
                "type": "object",
                "properties": {"weird": {"$ref": "#/definitions/Foo"}},
                "required": ["weird"],
            }
        )
        # Anything goes for Any fields.
        assert model(weird="string").model_dump() == {"weird": "string"}
        assert model(weird=42).model_dump() == {"weird": 42}
        assert model(weird=[1, 2]).model_dump() == {"weird": [1, 2]}

    def test_description_passed_to_field(self) -> None:
        model = _synth_input_model(
            {
                "type": "object",
                "properties": {"x": {"type": "string", "description": "the x value"}},
                "required": ["x"],
            }
        )
        # Pydantic exposes description via model_json_schema.
        schema = model.model_json_schema()
        assert schema["properties"]["x"]["description"] == "the x value"


# --------------------------------------------------------------------------- #
# Trust gating                                                                #
# --------------------------------------------------------------------------- #


def _fake_tool_def(
    name: str = "x",
    description: str = "",
    schema: dict[str, Any] | None = None,
    read_only_hint: bool | None = None,
) -> SimpleNamespace:
    """Build a SDK-Tool-shaped duck-typed object — same fields we access:
    name, description, inputSchema, annotations.readOnlyHint."""
    annotations = (
        SimpleNamespace(readOnlyHint=read_only_hint) if read_only_hint is not None else None
    )
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
        annotations=annotations,
    )


class TestTrustGating:
    def test_trusted_with_read_only_true(self) -> None:
        tool = _fake_tool_def(read_only_hint=True)
        adapter = McpToolAdapter("Svr", tool, client=None, trust=True)  # type: ignore[arg-type]
        assert adapter.is_read_only is True
        assert adapter.trust_source == "trusted-server"

    def test_trusted_with_read_only_false(self) -> None:
        tool = _fake_tool_def(read_only_hint=False)
        adapter = McpToolAdapter("Svr", tool, client=None, trust=True)  # type: ignore[arg-type]
        assert adapter.is_read_only is False

    def test_trusted_with_no_annotation_defaults_false(self) -> None:
        tool = _fake_tool_def(read_only_hint=None)  # no annotations
        adapter = McpToolAdapter("Svr", tool, client=None, trust=True)  # type: ignore[arg-type]
        assert adapter.is_read_only is False

    def test_untrusted_forces_false_even_if_server_claims_true(self) -> None:
        # The trust whitelist is the truth source — server's self-report
        # is ignored when not trusted (D15.6).
        tool = _fake_tool_def(read_only_hint=True)
        adapter = McpToolAdapter("Svr", tool, client=None, trust=False)  # type: ignore[arg-type]
        assert adapter.is_read_only is False
        assert adapter.trust_source == "strict-default"


# --------------------------------------------------------------------------- #
# Adapter name + description                                                  #
# --------------------------------------------------------------------------- #


class TestAdapterMetadata:
    def test_name_includes_server_namespace(self) -> None:
        tool = _fake_tool_def(name="read_file")
        adapter = McpToolAdapter("Filesystem", tool, client=None, trust=False)  # type: ignore[arg-type]
        assert adapter.name == "Filesystem.read_file"

    def test_description_falls_back_when_empty(self) -> None:
        tool = _fake_tool_def(name="x", description="")
        adapter = McpToolAdapter("Svr", tool, client=None, trust=False)  # type: ignore[arg-type]
        assert adapter.description == "MCP tool x"

    def test_description_passes_through_when_set(self) -> None:
        tool = _fake_tool_def(name="x", description="the answer")
        adapter = McpToolAdapter("Svr", tool, client=None, trust=False)  # type: ignore[arg-type]
        assert adapter.description == "the answer"

    def test_input_model_is_synthesized_pydantic_class(self) -> None:
        tool = _fake_tool_def(schema={"type": "object", "properties": {"text": {"type": "string"}}})
        adapter = McpToolAdapter("S", tool, client=None, trust=False)  # type: ignore[arg-type]
        # Should be a Pydantic class with field 'text'.
        instance = adapter.input_model(text="hi")
        assert instance.model_dump() == {"text": "hi"}


# --------------------------------------------------------------------------- #
# End-to-end execute via real subprocess                                      #
# --------------------------------------------------------------------------- #


class TestExecuteRoundTrip:
    """Real subprocess (via _test_server.py) so we verify the
    adapter.execute → client.call_tool → server roundtrip end-to-end."""

    async def test_echo_tool_returns_tool_result(self) -> None:
        async with McpClient(_python_server_cfg()) as client:
            raw_tools = await client.list_tools()
            echo_def = next(t for t in raw_tools if t.name == "echo")
            adapter = McpToolAdapter("Test", echo_def, client=client, trust=True)

            args = adapter.input_model(text="hello")
            result = await adapter.execute(args, ToolExecutionContext(cwd=Path("/tmp")))

            assert result.is_error is False
            assert "echo: hello" in result.output

    async def test_server_side_error_becomes_is_error_tool_result(self) -> None:
        """The MCP server's ``raise_error`` tool returns ``isError=True``;
        adapter must surface that as ``ToolResult(is_error=True)`` —
        errors-as-payload (D15.4 logical case)."""
        async with McpClient(_python_server_cfg()) as client:
            raw_tools = await client.list_tools()
            err_def = next(t for t in raw_tools if t.name == "raise_error")
            adapter = McpToolAdapter("Test", err_def, client=client, trust=True)

            args = adapter.input_model()
            result = await adapter.execute(args, ToolExecutionContext(cwd=Path("/tmp")))

            assert result.is_error is True
            assert "intentional test failure" in result.output.lower()
