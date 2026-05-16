"""Tool system -- :class:`BaseTool` ABC, :class:`ToolRegistry`, the runtime
types tools consume, and the five base tools (P2-T3).

Public API:

    from openharness.tools import (
        BaseTool,
        ToolExecutionContext,
        ToolRegistry,
        ToolResult,
        create_default_tool_registry,
    )

The five base tools (``Read`` / ``Write`` / ``Edit`` / ``Bash`` / ``Grep``)
live in their own submodules; callers normally use them via the registry
returned by :func:`create_default_tool_registry`.
"""

from __future__ import annotations

from openharness.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)
from openharness.tools.bash import Bash
from openharness.tools.edit import Edit
from openharness.tools.grep import Grep
from openharness.tools.load_skill import LoadSkillInput, LoadSkillTool
from openharness.tools.read import Read
from openharness.tools.spawn_agent import SpawnAgent, SpawnAgentInput
from openharness.tools.write import Write


def create_default_tool_registry() -> ToolRegistry:
    """Construct a :class:`ToolRegistry` with the five base tools registered.

    Registration order matches ``tasks/phase-2-plan.md``:
    ``Read`` -> ``Write`` -> ``Edit`` -> ``Bash`` -> ``Grep``. The order is
    visible to the LLM via ``to_api_schema()`` and to UIs via
    ``list_tools()``.
    """
    registry = ToolRegistry()
    registry.register(Read())
    registry.register(Write())
    registry.register(Edit())
    registry.register(Bash())
    registry.register(Grep())
    return registry


__all__ = [
    "BaseTool",
    "Bash",
    "Edit",
    "Grep",
    "LoadSkillInput",
    "LoadSkillTool",
    "Read",
    "SpawnAgent",
    "SpawnAgentInput",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "Write",
    "create_default_tool_registry",
]
