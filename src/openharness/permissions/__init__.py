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

from openharness.permissions.action_policy import (
    ActionDenyKind,
    ActionDenyPolicy,
    ConfiguredActionDenyPolicy,
    DenyResult,
)
from openharness.permissions.checker import (
    Decision,
    DecisionResult,
    DenyListChecker,
    PermissionChecker,
    PermissionMode,
)
from openharness.permissions.profile import (
    EnvironmentInheritance,
    EnvironmentPolicy,
    ExternalToolMode,
    ExternalToolPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    NetworkPolicy,
    ProcessPolicy,
    RuntimePermissionProfile,
    workspace_runtime_profile,
)
from openharness.permissions.rules import (
    PermissionRule,
    PermissionRules,
    accept_edits_preset,
    match_rules,
    parse_rule,
    plan_mode_preset,
)
from openharness.permissions.runtime import (
    ExternalPolicyEvidence,
    LocalBoundaryEvidence,
    PermissionDelta,
    PermissionDeltaKind,
    PermissionDeltaRequest,
    PermissionEnforcementEvidence,
    PermissionEvidenceKind,
    PermissionFilesystemAccess,
    PermissionResolution,
    PermissionResolutionStatus,
    PermissionResumeTransition,
    PermissionReviewDecision,
    PermissionReviewer,
    PermissionReviewVerdict,
    PermissionRuntime,
    PermissionRuntimeState,
)
from openharness.permissions.tier_based import TierBasedPermissionChecker

__all__ = [
    "ActionDenyKind",
    "ActionDenyPolicy",
    "ConfiguredActionDenyPolicy",
    "Decision",
    "DecisionResult",
    "DenyListChecker",
    "DenyResult",
    "EnvironmentInheritance",
    "EnvironmentPolicy",
    "ExternalPolicyEvidence",
    "ExternalToolMode",
    "ExternalToolPolicy",
    "FilesystemAccess",
    "FilesystemPolicy",
    "FilesystemRule",
    "LocalBoundaryEvidence",
    "NetworkPolicy",
    "PermissionChecker",
    "PermissionDelta",
    "PermissionDeltaKind",
    "PermissionDeltaRequest",
    "PermissionEnforcementEvidence",
    "PermissionEvidenceKind",
    "PermissionFilesystemAccess",
    "PermissionMode",
    "PermissionResolution",
    "PermissionResolutionStatus",
    "PermissionResumeTransition",
    "PermissionReviewDecision",
    "PermissionReviewVerdict",
    "PermissionReviewer",
    "PermissionRule",
    "PermissionRules",
    "PermissionRuntime",
    "PermissionRuntimeState",
    "ProcessPolicy",
    "RuntimePermissionProfile",
    "TierBasedPermissionChecker",
    "accept_edits_preset",
    "match_rules",
    "parse_rule",
    "plan_mode_preset",
    "workspace_runtime_profile",
]
