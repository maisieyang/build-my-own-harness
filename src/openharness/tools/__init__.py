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
    ExecutionDomain,
    ExternalEffectKind,
    ExternalEffectSurface,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    TrustedControlSurface,
)
from openharness.tools.bash import Bash
from openharness.tools.edit import Edit
from openharness.tools.grep import Grep
from openharness.tools.load_skill import LoadSkillInput, LoadSkillTool
from openharness.tools.memory import (
    MemoryDeleteInput,
    MemoryDeleteTool,
    MemoryListInput,
    MemoryListTool,
    MemoryShowInput,
    MemoryShowTool,
    MemoryUpsertInput,
    MemoryUpsertTool,
    register_memory_tools,
)
from openharness.tools.read import Read
from openharness.tools.spawn_agent import SpawnAgent, SpawnAgentInput
from openharness.tools.write import Write


def create_default_tool_registry() -> ToolRegistry:
    """Construct a :class:`ToolRegistry` with the default built-in tools.

    Registration order:
    ``Read`` -> ``Write`` -> ``Edit`` -> ``Bash`` -> ``Grep`` -> ``Agent``.
    The order is visible to the LLM via ``to_api_schema()`` and to UIs via
    ``list_tools()``.

    P6-T5: ``Agent`` (SpawnAgent default instance) joins the lineup. It's
    the recursive tool dispatch primitive (Phase 6) — sub-agents inherit
    this same registry, so a sub-agent at depth 1 also sees ``Agent`` and
    can spawn its own children until ``Settings.max_agent_depth`` is
    reached.
    """
    registry = ToolRegistry()
    registry.register(Read())
    registry.register(Write())
    registry.register(Edit())
    registry.register(Bash())
    registry.register(Grep())
    registry.register(SpawnAgent())
    return registry


__all__ = [
    "BaseTool",
    "Bash",
    "Edit",
    "ExecutionDomain",
    "ExternalEffectKind",
    "ExternalEffectSurface",
    "Grep",
    "LoadSkillInput",
    "LoadSkillTool",
    "MemoryDeleteInput",
    "MemoryDeleteTool",
    "MemoryListInput",
    "MemoryListTool",
    "MemoryShowInput",
    "MemoryShowTool",
    "MemoryUpsertInput",
    "MemoryUpsertTool",
    "Read",
    "SpawnAgent",
    "SpawnAgentInput",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "TrustedControlSurface",
    "Write",
    "create_default_tool_registry",
    "register_memory_tools",
]
