"""Permission interface -- :class:`Decision` enum + :class:`PermissionChecker`
Protocol (P2-T4 sub-unit 4c).

Per D10.1 (P2-T4 Three-Axis): defining the interface in P2-T4 lets ``run_query``
call ``context.permission_checker.evaluate(...)`` and react to the result; the
deny-list implementation arrives in P2-T6 alongside the CLI ``--auto`` /
``--dry-run`` flags.

``Protocol`` (not ``ABC``) by design: Phase 5 plugins / MCP adapters can drop
in a checker without needing to inherit from a specific base class. Existing
code that wants ABC-style enforcement can declare its own ``BaseChecker(ABC)``
that *also* satisfies the Protocol.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel

    from openharness.tools.base import ToolExecutionContext


class Decision(Enum):
    """Outcome of a permission evaluation."""

    ALLOW = "allow"
    DENY = "deny"


class PermissionChecker(Protocol):
    """Decides whether a tool call may execute.

    The loop calls ``evaluate`` *after* Pydantic validation but *before*
    invoking ``BaseTool.execute``. ``args`` is a validated input model
    (so ``isinstance(args, BashInput)`` reliably narrows for tool-specific
    deny logic in Phase 3).
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> Decision:
        """Return ``Decision.ALLOW`` or ``Decision.DENY``."""
        ...
