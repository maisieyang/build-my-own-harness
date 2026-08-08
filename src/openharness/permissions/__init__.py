"""Canonical authorization intent, exact review, and enforcement evidence.

``RuntimePermissionProfile`` is the sole product authority surface. Reviewer
and execution postures are orthogonal invocation controls; negative semantic
guards may only narrow the profile. Local authority is valid only when a
verified boundary proves that it enforces the same profile fingerprint.
"""

from __future__ import annotations

from openharness.permissions.action_policy import (
    ActionDenyKind,
    ActionDenyPolicy,
    ConfiguredActionDenyPolicy,
    DenyResult,
    PlanActionDenyPolicy,
)
from openharness.permissions.migration import (
    LegacyPermissionInputs,
    LegacyPermissionMigrationError,
    LegacyPermissionTranslation,
    translate_legacy_permission_config,
)
from openharness.permissions.posture import ExecutionPosture, ReviewerPosture
from openharness.permissions.profile import (
    EnvironmentInheritance,
    EnvironmentPolicy,
    ExternalToolMode,
    ExternalToolPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    FilesystemScope,
    NetworkPolicy,
    ProcessPolicy,
    RuntimePermissionProfile,
    workspace_runtime_profile,
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

__all__ = [
    "ActionDenyKind",
    "ActionDenyPolicy",
    "ConfiguredActionDenyPolicy",
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
    "FilesystemScope",
    "LegacyPermissionInputs",
    "LegacyPermissionMigrationError",
    "LegacyPermissionTranslation",
    "LocalBoundaryEvidence",
    "NetworkPolicy",
    "PermissionDelta",
    "PermissionDeltaKind",
    "PermissionDeltaRequest",
    "PermissionEnforcementEvidence",
    "PermissionEvidenceKind",
    "PermissionFilesystemAccess",
    "PermissionResolution",
    "PermissionResolutionStatus",
    "PermissionResumeTransition",
    "PermissionReviewDecision",
    "PermissionReviewVerdict",
    "PermissionReviewer",
    "PermissionRuntime",
    "PermissionRuntimeState",
    "PlanActionDenyPolicy",
    "ProcessPolicy",
    "ReviewerPosture",
    "RuntimePermissionProfile",
    "translate_legacy_permission_config",
    "workspace_runtime_profile",
]
