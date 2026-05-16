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

from openharness.protocols import ToolSpec
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


class TestIsReadOnlyDefault:
    """``is_read_only`` (P3-T1.1a / D13.3) defaults to False on BaseTool.

    Subclasses opt INTO read-only by setting ``is_read_only = True`` (Read /
    Grep). Tools that mutate state inherit the safe default — the AuthZ Tier 3
    (P3-T3) gives the strict-mode treatment to anything that could write.
    """

    def test_basetool_default_is_false(self) -> None:
        # The default lives on BaseTool itself; _FakeTool doesn't override it.
        assert _FakeTool.is_read_only is False

    def test_subclass_can_override_to_true(self) -> None:
        class _ReadOnlyFake(BaseTool[FakeInput]):
            name = "ReadOnlyFake"
            description = "Subclass that opts into read-only."
            input_model = FakeInput
            is_read_only = True

            async def execute(
                self,
                args: FakeInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                return ToolResult(output="ok")

        assert _ReadOnlyFake.is_read_only is True
        assert _ReadOnlyFake().is_read_only is True


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


class TestToolRegistryToApiSchema:
    def test_empty_registry_returns_empty_list(self) -> None:
        assert ToolRegistry().to_api_schema() == []

    def test_single_tool_schema_carries_name_and_description(self) -> None:
        registry = ToolRegistry()
        registry.register(_FakeTool())
        schemas = registry.to_api_schema()
        assert len(schemas) == 1
        assert isinstance(schemas[0], ToolSpec)
        assert schemas[0].name == "Fake"
        assert schemas[0].description == "Fake tool used in tests."

    def test_input_schema_reflects_pydantic_model(self) -> None:
        # Verifies the D8.2 claim: model_json_schema() output goes through.
        registry = ToolRegistry()
        registry.register(_FakeTool())
        schema = registry.to_api_schema()[0].input_schema
        # FakeInput has a single ``value: str`` field; assert the schema
        # carries it without binding to JSON-schema-spec implementation
        # details (e.g., property type name format).
        assert "properties" in schema
        assert "value" in schema["properties"]

    def test_multi_tool_order_matches_registration(self) -> None:
        registry = ToolRegistry()
        registry.register(_FakeTool())
        registry.register(_AltFakeTool())
        names = [spec.name for spec in registry.to_api_schema()]
        assert names == ["Fake", "Alt"]


# --------------------------------------------------------------------------- #
# P6-T2: ToolExecutionContext.parent_query field                              #
# --------------------------------------------------------------------------- #


class TestParentQueryField:
    """P6-T2 (D16.8) — additive optional field on ToolExecutionContext.

    The field is the ONLY structural dispatch-machinery change Phase 6
    introduces. Tests verify:

    1. Default ``None`` — backward compat for every existing tool that
       doesn't care about sub-agents.
    2. Explicit ``QueryContext`` passed — what the engine does for the
       SpawnAgent path.
    3. Backward-compat positional construction ``ToolExecutionContext(cwd=p)``
       still works.
    """

    def test_default_parent_query_is_none(self, tmp_path: Path) -> None:
        ctx = ToolExecutionContext(cwd=tmp_path)
        assert ctx.parent_query is None

    def test_keyword_construction_with_cwd_only(self, tmp_path: Path) -> None:
        # Existing tools / tests / fixtures construct this way — must
        # remain valid.
        ctx = ToolExecutionContext(cwd=tmp_path)
        assert ctx.cwd == tmp_path
        assert ctx.parent_query is None

    def test_explicit_parent_query_attached(self, tmp_path: Path) -> None:
        # ``parent_query`` is just an opaque object at this layer (no
        # isinstance check). Pass a sentinel object to verify the field
        # round-trips. SpawnAgent's actual sub-agent construction is
        # tested separately in P6-T3.
        sentinel = object()
        ctx = ToolExecutionContext(
            cwd=tmp_path,
            parent_query=sentinel,  # type: ignore[arg-type]
        )
        assert ctx.parent_query is sentinel

    def test_is_frozen(self, tmp_path: Path) -> None:
        # Defense in depth: ToolExecutionContext must remain immutable so
        # tools can't leak side-channel state back to engine.
        import dataclasses as _dc

        ctx = ToolExecutionContext(cwd=tmp_path)
        with pytest.raises(_dc.FrozenInstanceError):
            ctx.parent_query = object()  # type: ignore[misc,assignment]


# --------------------------------------------------------------------------- #
# P7-T2: ToolExecutionContext.execution_env field                             #
# --------------------------------------------------------------------------- #


class TestExecutionEnvOnExecCtx:
    """P7-T2 (D17.3) — additive optional ``execution_env`` field.

    Mirrors the ``parent_query`` test pattern: default ``None``,
    explicit injection round-trips, frozen prevents reassignment.
    The engine populates this from ``QueryContext.execution_env``;
    tools that don't consume it (Read / Write / Edit / Grep / MCP /
    LoadSkill / SpawnAgent) ignore the field. Only BashTool reads it.
    """

    def test_default_execution_env_is_none(self, tmp_path: Path) -> None:
        ctx = ToolExecutionContext(cwd=tmp_path)
        assert ctx.execution_env is None

    def test_explicit_execution_env_attached(self, tmp_path: Path) -> None:
        sentinel = object()
        ctx = ToolExecutionContext(
            cwd=tmp_path,
            execution_env=sentinel,  # type: ignore[arg-type]
        )
        assert ctx.execution_env is sentinel

    def test_can_set_both_parent_query_and_execution_env(self, tmp_path: Path) -> None:
        # Engine populates both at dispatch time (P6-T2 + P7-T2).
        parent_sentinel = object()
        env_sentinel = object()
        ctx = ToolExecutionContext(
            cwd=tmp_path,
            parent_query=parent_sentinel,  # type: ignore[arg-type]
            execution_env=env_sentinel,  # type: ignore[arg-type]
        )
        assert ctx.parent_query is parent_sentinel
        assert ctx.execution_env is env_sentinel
