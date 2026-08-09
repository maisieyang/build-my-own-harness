"""Independent runtime postures for review selection and execution."""

from __future__ import annotations

from enum import Enum


class ReviewerPosture(str, Enum):
    """How an exact permission request is reviewed."""

    MANUAL = "manual"
    AUTO = "auto"


class ExecutionPosture(str, Enum):
    """Whether authorized tool calls execute or are only described."""

    EXECUTE = "execute"
    DRY_RUN = "dry_run"
