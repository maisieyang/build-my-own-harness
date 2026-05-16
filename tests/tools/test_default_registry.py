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
from openharness.tools.base import ToolExecutionContext
from openharness.tools.bash import BashInput
from openharness.tools.read import ReadInput

if TYPE_CHECKING:
    from pathlib import Path


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
