"""Permission system -- authority, reviewer posture, and execution evidence.

The public legacy checker surface remains available for host execution and
configuration compatibility. Canonical verified dispatch is governed by a
runtime profile and proven sandbox boundary; external deltas use the durable
exact approval lifecycle. The exported building blocks include:

- :class:`Decision` enum (ALLOW / DENY / ASK)
- :class:`DecisionResult` dataclass wrapping decision + reason
- :class:`PermissionMode` legacy config enum (DEFAULT / AUTO / DRY_RUN)
- :class:`ReviewerPosture` and :class:`ExecutionPosture`
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
    PlanActionDenyPolicy,
)
from openharness.permissions.checker import (
    Decision,
    DecisionResult,
    DenyListChecker,
    PermissionChecker,
    PermissionMode,
)
from openharness.permissions.posture import (
    ExecutionPosture,
    ReviewerPosture,
    postures_from_legacy_mode,
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
    "ExecutionPosture",
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
    "PlanActionDenyPolicy",
    "ProcessPolicy",
    "ReviewerPosture",
    "RuntimePermissionProfile",
    "TierBasedPermissionChecker",
    "accept_edits_preset",
    "match_rules",
    "parse_rule",
    "plan_mode_preset",
    "postures_from_legacy_mode",
    "workspace_runtime_profile",
]
