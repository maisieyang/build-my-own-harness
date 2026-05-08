"""Permission interface + :class:`DenyListChecker` minimal implementation.

P2-T4.4c shipped the ``Decision`` enum + ``PermissionChecker`` Protocol so
``run_query`` could call ``context.permission_checker.evaluate(...)``.
P2-T6.6a adds the concrete pieces:

- :class:`PermissionMode` enum (DEFAULT / AUTO / DRY_RUN) for the CLI flags
  threaded through ``Settings`` and ``QueryContext`` (D12.4).
- :class:`DenyListChecker` -- a small substring-based deny-list scoped to
  ``Bash`` commands per D12.2 (Write/Edit already have D9.2 cwd scope guard;
  Read/Grep are read-only, lower risk). The 9-step algorithm is Phase 3
  territory; this layer is the safety floor.

``PermissionChecker`` remains a Protocol (not ABC) by design: Phase 5
plugins / MCP adapters can drop in a checker without needing to inherit.
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


class PermissionMode(Enum):
    """High-level permission policy threaded from CLI flags down through
    Settings into :class:`QueryContext`.

    - ``DEFAULT`` -- run the configured ``PermissionChecker`` normally.
    - ``AUTO`` -- reserved for Phase 3 (skip interactive confirmation).
      Phase 2 treats it as DEFAULT but threads the flag for forward compat.
    - ``DRY_RUN`` -- ``run_query`` short-circuits every tool call and emits
      a synthetic ``ToolExecutionCompleted(output="would call ...")`` event
      instead. The PermissionChecker is bypassed entirely.
    """

    DEFAULT = "default"
    AUTO = "auto"
    DRY_RUN = "dry_run"


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


# Per D12.3: small, well-known catastrophic patterns. Substring containment
# (no regex) keeps the rule set readable and free of injection risk. Phase 3
# 9-step algorithm replaces this with structured rules.
_DENY_PATTERNS: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf /*",
    ":(){ :|:& };:",  # classic fork bomb
    "mkfs",
    "dd if=/dev/zero of=/dev/",
    "> /dev/sda",
    "chmod -R 777 /",
)


class DenyListChecker:
    """Reject Bash commands that contain any pattern in :data:`_DENY_PATTERNS`.

    Per D12.2: only Bash is checked. Write/Edit have D9.2 cwd scope guards
    built in; Read/Grep are read-only. ``evaluate`` returns ``Decision.ALLOW``
    for every other tool name and for Bash commands that don't match.

    Structurally satisfies :class:`PermissionChecker` -- no inheritance needed.
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> Decision:
        del context  # unused at this layer; Phase 3 may consult cwd
        if tool_name != "Bash":
            return Decision.ALLOW
        # ``args`` is a validated BashInput at runtime; getattr keeps the
        # check duck-typed (no import dependency on tools.bash).
        command = getattr(args, "command", None)
        if not isinstance(command, str):
            return Decision.ALLOW
        if any(pattern in command for pattern in _DENY_PATTERNS):
            return Decision.DENY
        return Decision.ALLOW
