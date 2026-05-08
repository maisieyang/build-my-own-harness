"""Tests for :class:`ToolResult`, :class:`ToolExecutionContext`, and
:class:`BaseTool` — P2-T2 sub-units 2a + 2b.

For ToolResult / ToolExecutionContext (2a):

1. Both are frozen — the loop trusts results and contexts are not mutated
   after construction.
2. ``ToolResult.metadata`` defaults to a *fresh* dict per instance — the
   classic ``= {}`` mutable-default trap would silently couple instances.

For BaseTool (2b):

1. ABC enforcement: subclass missing ``execute`` cannot be instantiated.
2. ``_FakeTool`` (in conftest.py) round-trips through name / description /
   input_model / execute and produces a ToolResult.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openharness.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)
from tools.conftest import FakeInput, _FakeTool


class TestToolResult:
    def test_required_field_only(self) -> None:
        result = ToolResult(output="hello")
        assert result.output == "hello"
        assert result.is_error is False
        assert result.metadata == {}

    def test_error_result_construction(self) -> None:
        result = ToolResult(output="file not found", is_error=True)
        assert result.is_error is True

    def test_metadata_round_trip(self) -> None:
        result = ToolResult(output="ok", metadata={"bytes_written": 42})
        assert result.metadata == {"bytes_written": 42}

    def test_metadata_default_is_per_instance(self) -> None:
        # The mutable-default trap: if metadata used `= {}` the two instances
        # below would share the same dict; mutating r1.metadata would surface
        # in r2.metadata. ``field(default_factory=dict)`` prevents this.
        r1 = ToolResult(output="a")
        r2 = ToolResult(output="b")
        assert r1.metadata is not r2.metadata

    def test_frozen_field_assignment_raises(self) -> None:
        result = ToolResult(output="ok")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.is_error = True  # type: ignore[misc]


class TestToolExecutionContext:
    def test_cwd_round_trip(self) -> None:
        ctx = ToolExecutionContext(cwd=Path("/tmp/x"))
        assert ctx.cwd == Path("/tmp/x")

    def test_frozen_field_assignment_raises(self) -> None:
        ctx = ToolExecutionContext(cwd=Path("/tmp"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.cwd = Path("/elsewhere")  # type: ignore[misc]


class TestBaseToolAbstractEnforcement:
    def test_subclass_without_execute_cannot_instantiate(self) -> None:
        # ABC's @abstractmethod guard: any subclass that omits ``execute``
        # raises TypeError on instantiation, naming the missing method.
        class _Incomplete(BaseTool[FakeInput]):
            name = "Incomplete"
            description = "missing execute"
            input_model = FakeInput

        with pytest.raises(TypeError, match="execute"):
            _Incomplete()  # type: ignore[abstract]


class TestFakeToolRoundTrip:
    def test_class_attributes_set_correctly(self) -> None:
        tool = _FakeTool()
        assert tool.name == "Fake"
        assert tool.description == "Fake tool used in tests."
        assert tool.input_model is FakeInput

    async def test_execute_returns_tool_result(self) -> None:
        tool = _FakeTool()
        args = FakeInput(value="hello")
        result = await tool.execute(args, ToolExecutionContext(cwd=Path("/tmp")))
        assert isinstance(result, ToolResult)
        assert result.output == "value=hello"
        assert result.is_error is False


class _AltFakeTool(BaseTool[FakeInput]):
    """Second fake with a different name — used to verify multi-registration."""

    name = "Alt"
    description = "Second fake."
    input_model = FakeInput

    async def execute(
        self,
        args: FakeInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        return ToolResult(output=f"alt:{args.value}")


class TestToolRegistry:
    def test_register_and_get_round_trip(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool()
        registry.register(tool)
        assert registry.get("Fake") is tool

    def test_register_duplicate_name_raises(self) -> None:
        registry = ToolRegistry()
        registry.register(_FakeTool())
        with pytest.raises(ValueError, match="'Fake' already registered"):
            registry.register(_FakeTool())

    def test_get_missing_raises_key_error(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(KeyError, match="DoesNotExist"):
            registry.get("DoesNotExist")

    def test_list_tools_empty_initially(self) -> None:
        assert ToolRegistry().list_tools() == []

    def test_list_tools_preserves_registration_order(self) -> None:
        registry = ToolRegistry()
        first = _FakeTool()
        second = _AltFakeTool()
        registry.register(first)
        registry.register(second)
        listed = registry.list_tools()
        assert [t.name for t in listed] == ["Fake", "Alt"]

    def test_list_tools_returns_caller_owned_copy(self) -> None:
        # Mutating the returned list must not affect the registry.
        registry = ToolRegistry()
        registry.register(_FakeTool())
        listed = registry.list_tools()
        listed.clear()
        assert len(registry.list_tools()) == 1
