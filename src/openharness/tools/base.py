"""Tool abstractions — :class:`ToolResult` / :class:`ToolExecutionContext`
(P2-T2 sub-unit 2a).

Per the P2-T2 Three-Axis discussion (D8.4 / D8.5 / D8.7):

- **D8.4**: ``ToolResult.output`` is a flat ``str``. Multi-modal results are
  Phase 5+ territory; ``metadata`` is the escape hatch for structured info.
- **D8.5**: ``is_error=True`` represents a *recoverable* failure the LLM can
  see and react to (file not found, command non-zero exit). Programming
  errors propagate via ``raise`` instead of being smuggled into the result.
- **D8.7**: ``ToolExecutionContext`` carries only ``cwd`` for now. Field set
  is intentionally minimal — adding fields later is non-breaking because
  the only caller (``run_query`` in P2-T4) is internal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ToolResult:
    """A tool's outcome, fed back to the LLM as a ``ToolResultBlock``.

    Construction patterns:

    - Success: ``ToolResult(output="contents of file X")``
    - Recoverable failure: ``ToolResult(output="permission denied", is_error=True)``
    - With structured metadata: ``ToolResult(output="...", metadata={"bytes": 42})``
    """

    output: str
    is_error: bool = False
    # Per-instance dict — ``field(default_factory=dict)`` avoids the classic
    # mutable-default-argument trap (every instance shares one dict).
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionContext:
    """Runtime context handed to ``BaseTool.execute``.

    P2-T2 carries only ``cwd``. P2-T3 file tools resolve relative paths
    against this; ``Bash`` uses it as the subprocess cwd; ``Grep`` walks
    from it. Future fields (settings excerpts, hook data) land here as
    additive frozen-dataclass fields when concrete capabilities surface them.
    """

    cwd: Path
