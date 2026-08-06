"""S1 public contracts separating configured intent from installed facts."""

from __future__ import annotations

from typing import get_args

from openharness.execution.boundary import (
    BackendSupport,
    BoundaryVerification,
    BoundaryViolation,
    CommandOperation,
    EnforcedBoundary,
    ExecutionEffect,
    ExecutionFailed,
    ExecutionResult,
    FileEditOperation,
    FileReadOperation,
    FileSearchOperation,
    FileWriteOperation,
    OperationCompleted,
    ProcessCompleted,
    TimedOut,
)


def test_verified_boundary_records_exact_coverage_and_profile_identity() -> None:
    boundary = EnforcedBoundary(
        profile_fingerprint="a" * 64,
        backend="seatbelt",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND, ExecutionEffect.FILE_READ),
        verification=BoundaryVerification.VERIFIED,
    )

    assert boundary.covers(ExecutionEffect.COMMAND)
    assert not boundary.covers(ExecutionEffect.FILE_WRITE)
    assert boundary.is_verified is True


def test_backend_support_is_explicit_about_unsupported_dimensions() -> None:
    support = BackendSupport.unsupported(
        backend="docker-command",
        features=("filesystem.deny_read", "external_tools"),
        reason="command backend cannot cover the session data plane",
    )

    assert support.supported is False
    assert support.unsupported_features == (
        "external_tools",
        "filesystem.deny_read",
    )


def test_execution_result_is_a_closed_union_not_an_empty_protocol() -> None:
    assert set(get_args(ExecutionResult)) == {
        ProcessCompleted,
        OperationCompleted,
        TimedOut,
        ExecutionFailed,
        BoundaryViolation,
    }


def test_every_local_operation_declares_the_effect_a_backend_must_cover(tmp_path) -> None:
    assert CommandOperation("true", tmp_path).required_effect is ExecutionEffect.COMMAND
    assert FileReadOperation(tmp_path / "a").required_effect is ExecutionEffect.FILE_READ
    assert FileWriteOperation(tmp_path / "a", "x").required_effect is ExecutionEffect.FILE_WRITE
    assert FileEditOperation(tmp_path / "a", "x", "y").required_effect is ExecutionEffect.FILE_WRITE
    assert FileSearchOperation("x", tmp_path).required_effect is ExecutionEffect.FILE_SEARCH
