"""Legacy host-path permission interface and deny-list implementation.

``PermissionChecker`` remains public for compatibility and for execution
without a selected sandbox. Verified local/delegated dispatch instead uses
the canonical runtime profile plus the sandbox's reported boundary; external
effects use the exact approval lifecycle. ``PermissionMode`` is likewise a
legacy config/CLI compatibility type which is split into independent reviewer
and execution postures at bootstrap.
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
    - ``ASK``:the legacy checker cannot prove authority. It always fails
      closed as an unresolved exact approval; AUTO never upgrades it to
      ALLOW. Durable approve/deny/park/resume is handled by
      :class:`~openharness.permissions.runtime.PermissionRuntime`.
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
        """Construct an unresolved exact-approval result.

        The legacy host path fails closed on this result in every reviewer
        posture. AUTO selects an automated reviewer for canonical approval
        requests; it is not blanket authorization for legacy ASK.
        """
        return cls(decision=Decision.ASK, reason=reason)


class PermissionMode(Enum):
    """Legacy public config type mapped to orthogonal runtime postures.

    - ``DEFAULT`` maps to manual review plus real execution.
    - ``AUTO`` maps to automated review plus real execution and does not
      authorize a legacy ``Decision.ASK``.
    - ``DRY_RUN`` maps to manual review plus simulated execution.

    New authority code should consume ``ReviewerPosture`` and
    ``ExecutionPosture`` rather than branch on this compatibility enum.
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
