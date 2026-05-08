"""Tests for the permission interface — P2-T4 sub-unit 4c.

Two contract properties matter:

1. ``Decision`` is a closed enum with two values; tests can rely on direct
   identity comparison rather than string matching.
2. ``PermissionChecker`` is a Protocol — any object with the right
   ``evaluate(...)`` signature is assignable to it. The implementation in
   P2-T6 will be a concrete class, but the loop only depends on the Protocol.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from openharness.permissions import Decision, PermissionChecker
from openharness.tools.base import ToolExecutionContext


class _DummyArgs(BaseModel):
    value: str


class TestDecision:
    def test_two_outcomes(self) -> None:
        # mypy literal-narrowing flags `Decision.ALLOW != Decision.DENY` as
        # statically obvious; comparing via list membership keeps the runtime
        # check meaningful while staying mypy-strict-clean.
        assert Decision.ALLOW in {Decision.ALLOW}
        assert Decision.DENY not in {Decision.ALLOW}

    def test_string_values_for_serialization(self) -> None:
        # Useful for emitting decisions in logs / hooks downstream.
        assert Decision.ALLOW.value == "allow"
        assert Decision.DENY.value == "deny"


class TestPermissionCheckerProtocol:
    def test_structural_subtype_satisfies_protocol(self) -> None:
        # Any object that implements ``evaluate(...)`` with the right shape
        # should be assignable to a PermissionChecker-typed binding.

        class _Allow:
            def evaluate(
                self,
                tool_name: str,
                args: BaseModel,
                context: ToolExecutionContext,
            ) -> Decision:
                del tool_name, args, context  # marker: unused in stub
                return Decision.ALLOW

        checker: PermissionChecker = _Allow()
        decision = checker.evaluate(
            "Bash",
            _DummyArgs(value="hi"),
            ToolExecutionContext(cwd=Path("/tmp")),
        )
        assert decision is Decision.ALLOW

    def test_deny_path_returns_deny(self) -> None:
        class _Deny:
            def evaluate(
                self,
                tool_name: str,
                args: BaseModel,
                context: ToolExecutionContext,
            ) -> Decision:
                del tool_name, args, context
                return Decision.DENY

        checker: PermissionChecker = _Deny()
        assert (
            checker.evaluate(
                "Bash",
                _DummyArgs(value="x"),
                ToolExecutionContext(cwd=Path("/tmp")),
            )
            is Decision.DENY
        )
