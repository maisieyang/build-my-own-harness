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

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel

    from openharness.tools.base import ToolExecutionContext


class Decision(Enum):
    """Outcome of a permission evaluation.

    Three states (P3-T3.3d expanded from binary):

    - ``ALLOW``:framework lets the call run.
    - ``DENY``:framework refuses; caller feeds reason back to LLM
      (ToolResult is_error=True). LLM 自己 plan B.
    - ``ASK``:framework wants human-in-the-loop confirmation. Resolution
      depends on ``PermissionMode``:AUTO → treat as ALLOW;
      DEFAULT → Phase 4+ will prompt;until then fail-safe to DENY with
      a hint pointing at ``--auto``.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class DecisionResult:
    """A permission decision + optional reason.

    P3-T3.3d:Phase 2 ``Decision`` enum was bare yes/no — when DENY fired,
    the LLM only saw ``"permission denied: <tool_name>"`` with no
    explanation. ``DecisionResult`` wraps the decision with a reason
    string so the deny message can guide the LLM's next step.

    Use the classmethods (:meth:`allow` / :meth:`deny` / :meth:`ask`)
    rather than constructing directly — they encode the convention that
    non-ALLOW results carry a reason.
    """

    decision: Decision
    reason: str | None = None  # populated for DENY / ASK

    @classmethod
    def allow(cls) -> DecisionResult:
        """Construct an ALLOW result (no reason needed)."""
        return cls(decision=Decision.ALLOW)

    @classmethod
    def deny(cls, reason: str) -> DecisionResult:
        """Construct a DENY result with a reason explaining why."""
        return cls(decision=Decision.DENY, reason=reason)

    @classmethod
    def ask(cls, reason: str) -> DecisionResult:
        """Construct an ASK result (boundary case warranting confirmation).

        ``reason`` explains the boundary; loop layer maps to ALLOW (AUTO
        mode) / DENY-with-hint (DEFAULT mode, Phase 4+ replaces with
        interactive prompt).
        """
        return cls(decision=Decision.ASK, reason=reason)


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
    ) -> DecisionResult:
        """Return ``DecisionResult.allow()`` / ``deny(reason)`` / ``ask(reason)``.

        Signature widened in P3-T3.3d:Phase 2 returned bare ``Decision``;
        Phase 3 needs reason + three-state semantics for the AuthZ Tiers.
        """
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

    .. deprecated:: P3-T3.3f
       Phase 2's checker, kept as a simpler stub for tests + as the smaller
       behavioral floor. Production ``cli.py`` now wires
       :class:`TierBasedPermissionChecker` instead (full Tier 1/2/3 +
       Bash deny-list inherited from here). Will be removed when no test
       depends on the smaller surface.
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DecisionResult:
        del context  # unused at this layer; TierBasedPermissionChecker (3e) uses cwd
        if tool_name != "Bash":
            return DecisionResult.allow()
        # ``args`` is a validated BashInput at runtime; getattr keeps the
        # check duck-typed (no import dependency on tools.bash).
        command = getattr(args, "command", None)
        if not isinstance(command, str):
            return DecisionResult.allow()
        for pattern in _DENY_PATTERNS:
            if pattern in command:
                return DecisionResult.deny(f"matches catastrophic Bash pattern {pattern!r}")
        return DecisionResult.allow()
