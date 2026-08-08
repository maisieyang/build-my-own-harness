"""Independent runtime postures for review selection and execution."""

from __future__ import annotations

from enum import Enum

from openharness.permissions.checker import PermissionMode


class ReviewerPosture(str, Enum):
    """How an exact permission request is reviewed."""

    MANUAL = "manual"
    AUTO = "auto"


class ExecutionPosture(str, Enum):
    """Whether authorized tool calls execute or are only described."""

    EXECUTE = "execute"
    DRY_RUN = "dry_run"


def postures_from_legacy_mode(
    mode: PermissionMode,
) -> tuple[ReviewerPosture, ExecutionPosture]:
    """Map the retained public config enum at the CLI compatibility edge."""
    reviewer = ReviewerPosture.AUTO if mode is PermissionMode.AUTO else ReviewerPosture.MANUAL
    execution = (
        ExecutionPosture.DRY_RUN if mode is PermissionMode.DRY_RUN else ExecutionPosture.EXECUTE
    )
    return reviewer, execution
