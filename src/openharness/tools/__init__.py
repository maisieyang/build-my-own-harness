"""Tool system -- :class:`BaseTool` ABC, :class:`ToolRegistry`, and the
runtime types tools consume.

P2-T2 ships the abstraction; concrete tools (Read / Write / Edit / Bash /
Grep) land in P2-T3.

Public API:

    from openharness.tools import (
        BaseTool,
        ToolExecutionContext,
        ToolRegistry,
        ToolResult,
    )
"""

from __future__ import annotations

from openharness.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)

__all__ = [
    "BaseTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
]
