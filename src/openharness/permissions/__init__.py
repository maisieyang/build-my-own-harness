"""Permission system -- decides whether a tool call may execute.

P2-T4.4c shipped the *interface*; P2-T6 shipped the binary implementation;
P3-T3.3d adds ``Decision.ASK`` + ``DecisionResult`` for three-state HITL
semantics:

- :class:`Decision` enum (ALLOW / DENY / ASK)
- :class:`DecisionResult` dataclass wrapping decision + reason
- :class:`PermissionMode` enum (DEFAULT / AUTO / DRY_RUN)
- :class:`PermissionChecker` Protocol with ``evaluate(...)`` -> DecisionResult
- :class:`DenyListChecker` -- minimal Bash deny-list

Public API:

    from openharness.permissions import (
        Decision,
        DecisionResult,
        DenyListChecker,
        PermissionChecker,
        PermissionMode,
    )
"""

from __future__ import annotations

from openharness.permissions.checker import (
    Decision,
    DecisionResult,
    DenyListChecker,
    PermissionChecker,
    PermissionMode,
)
from openharness.permissions.tier_based import TierBasedPermissionChecker

__all__ = [
    "Decision",
    "DecisionResult",
    "DenyListChecker",
    "PermissionChecker",
    "PermissionMode",
    "TierBasedPermissionChecker",
]
