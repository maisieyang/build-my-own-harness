"""Integration test for ``create_default_tool_registry`` — P2-T3 sub-unit 3f.

Verifies the factory produces a registry that:

1. Contains all five base tools under their PascalCase names (D6.4).
2. Projects to ``list[ToolSpec]`` of length five with all fields populated.
3. Round-trips through real execute calls for two representative tools
   (Read against a fixture file, Bash against ``echo``) — proves
   registration -> retrieval -> execution chain works end to end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openharness.execution import (
    BoundaryVerification,
    CommandOperation,
    EnforcedBoundary,
    ExecutionEffect,
    FileEditOperation,
    FileReadOperation,
    FileSearchOperation,
    FileWriteOperation,
    OperationCompleted,
    ProcessCompleted,
)
from openharness.mcp import McpToolAdapter
from openharness.permissions import workspace_runtime_profile
from openharness.protocols import ToolSpec
from openharness.tools import (
    Bash,
    Edit,
    Grep,
    Read,
    SpawnAgent,
    Write,
    create_default_tool_registry,
)
from openharness.tools.base import (
    ExecutionDomain,
    ExternalEffectKind,
    ExternalEffectSurface,
    ToolExecutionContext,
)
from openharness.tools.bash import BashInput
from openharness.tools.edit import EditInput
from openharness.tools.grep import GrepInput
from openharness.tools.load_skill import LoadSkillTool
from openharness.tools.read import ReadInput
from openharness.tools.web_fetch import WebFetch
from openharness.tools.web_search import WebSearch
from openharness.tools.write import WriteInput

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.execution import DataPlaneOperation, ExecutionResult


_G0_DEFAULT_TOOL_COVERAGE = {
    "Read": (ExecutionDomain.LOCAL_DATA, True, ExecutionEffect.FILE_READ),
    "Write": (ExecutionDomain.LOCAL_DATA, False, ExecutionEffect.FILE_WRITE),
    "Edit": (ExecutionDomain.LOCAL_DATA, False, ExecutionEffect.FILE_WRITE),
    "Bash": (ExecutionDomain.LOCAL_DATA, False, ExecutionEffect.COMMAND),
    "Grep": (ExecutionDomain.LOCAL_DATA, True, ExecutionEffect.FILE_SEARCH),
    # Agent reaches the same effects through a child QueryContext rather than
    # emitting a DataPlaneOperation itself. Its inheritance baseline is pinned
    # in tests/tools/test_spawn_agent.py.
    "Agent": (ExecutionDomain.DELEGATED_RUNTIME, False, None),
}


class _RecordingSandboxSession:
    """Verified-session spy for the G0 tool-to-operation coverage baseline."""

    def __init__(self) -> None:
        profile = workspace_runtime_profile()
        self._boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="g0-recording",
            backend_version="1",
            covered_effects=(
                ExecutionEffect.COMMAND,
                ExecutionEffect.FILE_READ,
                ExecutionEffect.FILE_WRITE,
                ExecutionEffect.FILE_SEARCH,
            ),
            verification=BoundaryVerification.VERIFIED,
        )
        self.operations: list[DataPlaneOperation] = []

    @property
    def boundary(self) -> EnforcedBoundary:
        return self._boundary

    async def execute(self, operation: DataPlaneOperation) -> ExecutionResult:
        self.operations.append(operation)
        if isinstance(operation, CommandOperation):
            return ProcessCompleted(output="ok", exit_code=0)
        return OperationCompleted(output="ok", metadata={})

    async def close(self) -> None:
        return None


def test_factory_registers_default_tools() -> None:
    registry = create_default_tool_registry()
    listed = registry.list_tools()
    # P6-T5: ``Agent`` (SpawnAgent) joins the default lineup.
    assert [t.name for t in listed] == [
        "Read",
        "Write",
        "Edit",
        "Bash",
        "Grep",
        "Agent",
    ]
    # Spot-check that each is the expected concrete class.
    assert isinstance(registry.get("Read"), Read)
    assert isinstance(registry.get("Write"), Write)
    assert isinstance(registry.get("Edit"), Edit)
    assert isinstance(registry.get("Bash"), Bash)
    assert isinstance(registry.get("Grep"), Grep)
    assert isinstance(registry.get("Agent"), SpawnAgent)


def test_g0_default_registry_domain_and_effect_coverage_matrix() -> None:
    """Pin every built-in model-callable tool before permission cutover.

    The effect value is the verified SandboxSession effect for direct local
    tools. ``None`` means delegated runtime: Agent must inherit the parent's
    covered runtime instead of manufacturing a local operation of its own.
    """
    registry = create_default_tool_registry()

    actual = {
        tool.name: (
            tool.execution_domain,
            tool.is_read_only,
            _G0_DEFAULT_TOOL_COVERAGE[tool.name][2],
        )
        for tool in registry.list_tools()
    }

    assert actual == _G0_DEFAULT_TOOL_COVERAGE


def test_g0_conditionally_registered_tool_surface_matrix() -> None:
    """Pin the non-default production surfaces CLI may add to the registry."""
    assert LoadSkillTool.execution_domain is ExecutionDomain.TRUSTED_CONTROL
    assert LoadSkillTool.is_read_only is True

    for web_tool in (WebSearch, WebFetch):
        assert web_tool.execution_domain is ExecutionDomain.EXTERNAL_EFFECT
        assert web_tool.external_effect_surface is ExternalEffectSurface.WEB
        assert web_tool.external_effect_kind is ExternalEffectKind.NETWORK_READ
        assert web_tool.external_effect_trusted is True

    assert McpToolAdapter.execution_domain is ExecutionDomain.EXTERNAL_EFFECT
    assert McpToolAdapter.external_effect_surface is ExternalEffectSurface.MCP
    # MCP effect kind and trust are resolved per instance from server trust
    # and annotations; tests/mcp_pkg/test_adapter.py pins the complete matrix.


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "operation_type", "required_effect"),
    [
        ("Read", ReadInput(path="input.txt"), FileReadOperation, ExecutionEffect.FILE_READ),
        (
            "Write",
            WriteInput(path="output.txt", content="x"),
            FileWriteOperation,
            ExecutionEffect.FILE_WRITE,
        ),
        (
            "Edit",
            EditInput(path="edit.txt", old_str="a", new_str="b"),
            FileEditOperation,
            ExecutionEffect.FILE_WRITE,
        ),
        (
            "Bash",
            BashInput(command="printf ok"),
            CommandOperation,
            ExecutionEffect.COMMAND,
        ),
        (
            "Grep",
            GrepInput(pattern="x", path="."),
            FileSearchOperation,
            ExecutionEffect.FILE_SEARCH,
        ),
    ],
)
async def test_g0_direct_local_tools_route_through_verified_operation_path(
    tmp_path: Path,
    tool_name: str,
    tool_input: object,
    operation_type: type[object],
    required_effect: ExecutionEffect,
) -> None:
    """Prove coverage from production tool execute() to SandboxSession."""
    registry = create_default_tool_registry()
    session = _RecordingSandboxSession()
    tool = registry.get(tool_name)

    result = await tool.execute(  # type: ignore[arg-type]
        tool_input,
        ToolExecutionContext(cwd=tmp_path, sandbox_session=session),
    )

    assert result.is_error is False
    assert len(session.operations) == 1
    operation = session.operations[0]
    assert isinstance(operation, operation_type)
    assert operation.required_effect is required_effect


def test_factory_schema_projects_to_all_specs() -> None:
    registry = create_default_tool_registry()
    schemas = registry.to_api_schema()
    # 5 base tools + 1 sub-agent.
    assert len(schemas) == 6
    for spec in schemas:
        assert isinstance(spec, ToolSpec)
        assert spec.name
        assert spec.description
        # input_schema is a JSON Schema dict; the only invariant we rely on
        # universally is "object" with a properties map.
        assert "properties" in spec.input_schema


class TestEndToEndExecution:
    async def test_read_via_registry_executes_against_real_file(self, tmp_path: Path) -> None:
        target = tmp_path / "fixture.txt"
        target.write_text("registry round-trip\n")
        registry = create_default_tool_registry()
        read = registry.get("Read")
        result = await read.execute(
            ReadInput(path=str(target)),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.is_error is False
        assert result.output == "registry round-trip\n"

    async def test_bash_via_registry_executes_real_subprocess(self, tmp_path: Path) -> None:
        registry = create_default_tool_registry()
        bash = registry.get("Bash")
        result = await bash.execute(
            BashInput(command="echo registry-end-to-end"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.is_error is False
        assert "registry-end-to-end" in result.output


@pytest.mark.parametrize(
    "tool_name",
    ["Read", "Write", "Edit", "Bash", "Grep", "Agent"],
)
def test_each_tool_has_pascal_case_name(tool_name: str) -> None:
    # D6.4: tool names are PascalCase; the registry stores them under the
    # exact wire-format string the LLM will see.
    registry = create_default_tool_registry()
    tool = registry.get(tool_name)
    assert tool.name == tool_name
    assert tool_name[0].isupper()


@pytest.mark.parametrize(
    ("tool_name", "expected_is_read_only"),
    [
        ("Read", True),
        ("Grep", True),
        ("Write", False),
        ("Edit", False),
        # Bash 默认保守 False —— `cat foo` 是只读但 `rm foo` 也是 Bash,
        # 静态判不出 read vs write,默认非只读交 AuthZ Tier 3 走 strict path
        ("Bash", False),
        # Agent (SpawnAgent) — sub-agent may dispatch mutating tools, so
        # AuthZ Tier 3 strict path applies. P6-T3 / D16.1.
        ("Agent", False),
    ],
)
def test_is_read_only_classification(
    tool_name: str,
    expected_is_read_only: bool,
) -> None:
    # P3-T1.1a / D13.3: AuthZ Tier 3 (P3-T3) reads this attribute to decide
    # lax vs strict permission path. Read / Grep opt-in to read-only; the
    # others inherit BaseTool's safe default of False.
    registry = create_default_tool_registry()
    tool = registry.get(tool_name)
    assert tool.is_read_only is expected_is_read_only
