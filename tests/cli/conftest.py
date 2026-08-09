"""Cross-platform verified-boundary fixtures for CLI behavior tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import openharness.cli as cli_module
from openharness.execution import (
    BackendSupport,
    BoundaryVerification,
    CommandOperation,
    EnforcedBoundary,
    ExecutionEffect,
    FileEditOperation,
    FileReadOperation,
    FileSearchOperation,
    FileWriteOperation,
    OperationCompleted,
    ProcessCompleted,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.execution.boundary import DataPlaneOperation, ExecutionResult
    from openharness.permissions import RuntimePermissionProfile


@pytest.fixture
def verified_seatbelt_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the CLI with a verified local backend that is safe on every OS.

    These CLI tests exercise permission dispatch, not Seatbelt itself. Commands
    are therefore acknowledged without execution; filesystem operations retain
    their observable semantics. Real Seatbelt behavior has dedicated macOS tests.
    """

    class _Session:
        def __init__(self, profile: RuntimePermissionProfile) -> None:
            self.boundary = EnforcedBoundary(
                profile_fingerprint=profile.fingerprint,
                backend="test-verified-local",
                backend_version="1",
                covered_effects=(
                    ExecutionEffect.COMMAND,
                    ExecutionEffect.FILE_READ,
                    ExecutionEffect.FILE_WRITE,
                    ExecutionEffect.FILE_SEARCH,
                ),
                verification=BoundaryVerification.VERIFIED,
            )

        async def execute(self, operation: DataPlaneOperation) -> ExecutionResult:
            if isinstance(operation, CommandOperation):
                return ProcessCompleted(output="", exit_code=0)
            if isinstance(operation, FileWriteOperation):
                operation.path.write_text(operation.content, encoding="utf-8")
                return OperationCompleted(output=f"wrote {operation.path}", metadata={})
            if isinstance(operation, FileEditOperation):
                original = operation.path.read_text(encoding="utf-8")
                updated = original.replace(
                    operation.old_str,
                    operation.new_str,
                    -1 if operation.replace_all else 1,
                )
                operation.path.write_text(updated, encoding="utf-8")
                return OperationCompleted(output=f"edited {operation.path}", metadata={})
            if isinstance(operation, FileReadOperation):
                return OperationCompleted(
                    output=operation.path.read_text(encoding="utf-8"), metadata={}
                )
            if isinstance(operation, FileSearchOperation):
                return OperationCompleted(output="(no matches)", metadata={})
            raise AssertionError(f"unsupported test operation: {type(operation).__name__}")

        async def close(self) -> None:
            pass

    class _Backend:
        def __init__(self, *, cwd: Path) -> None:
            self.cwd = cwd

        def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
            del profile
            return BackendSupport.available(backend="test-verified-local")

        async def open(self, profile: RuntimePermissionProfile) -> _Session:
            return _Session(profile)

    monkeypatch.setattr(cli_module, "SeatbeltBackend", _Backend)
