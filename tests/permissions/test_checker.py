"""Tests for the permission interface and DenyListChecker — P2-T4.4c + P2-T6.6a.

Contract properties:

1. ``Decision`` is a closed 2-value enum; ``PermissionMode`` is a closed
   3-value enum. Direct identity comparison is the supported test idiom.
2. ``PermissionChecker`` is a Protocol — structural subtypes work without
   inheritance.
3. ``DenyListChecker`` rejects every catastrophic shell pattern in
   ``_DENY_PATTERNS`` and lets safe Bash commands through; non-Bash tools
   always pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from openharness.permissions import (
    Decision,
    DenyListChecker,
    PermissionChecker,
    PermissionMode,
)
from openharness.tools.base import ToolExecutionContext


class _DummyArgs(BaseModel):
    value: str


class _BashLikeArgs(BaseModel):
    """Mirrors ``BashInput.command`` shape without importing tools.bash."""

    command: str


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


class TestPermissionMode:
    def test_three_modes(self) -> None:
        # Membership test rather than direct != avoids mypy literal-narrowing.
        assert {PermissionMode.DEFAULT, PermissionMode.AUTO, PermissionMode.DRY_RUN} == set(
            PermissionMode
        )

    def test_string_values(self) -> None:
        assert PermissionMode.DEFAULT.value == "default"
        assert PermissionMode.AUTO.value == "auto"
        assert PermissionMode.DRY_RUN.value == "dry_run"


@pytest.fixture
def checker() -> DenyListChecker:
    return DenyListChecker()


@pytest.fixture
def ctx() -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"))


class TestDenyListCheckerSafeCommands:
    def test_safe_bash_command_allowed(
        self, checker: DenyListChecker, ctx: ToolExecutionContext
    ) -> None:
        decision = checker.evaluate("Bash", _BashLikeArgs(command="ls /tmp"), ctx)
        assert decision is Decision.ALLOW

    def test_safe_command_with_substring_close_to_pattern(
        self, checker: DenyListChecker, ctx: ToolExecutionContext
    ) -> None:
        # "rm -rf ./build" should be allowed; only "rm -rf /" / "/* trigger.
        decision = checker.evaluate(
            "Bash",
            _BashLikeArgs(command="rm -rf ./build"),
            ctx,
        )
        assert decision is Decision.ALLOW


class TestDenyListCheckerDangerousCommands:
    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /",
            "rm -rf /*",
            "rm -rf /etc",  # contains "rm -rf /" as substring
            ":(){ :|:& };:",
            "echo dangerous; :(){ :|:& };:",  # embedded fork bomb
            "mkfs.ext4 /dev/sda1",  # contains "mkfs"
            "dd if=/dev/zero of=/dev/sda",
            "echo y | chmod -R 777 /",
        ],
    )
    def test_dangerous_pattern_rejected(
        self,
        checker: DenyListChecker,
        ctx: ToolExecutionContext,
        command: str,
    ) -> None:
        decision = checker.evaluate("Bash", _BashLikeArgs(command=command), ctx)
        assert decision is Decision.DENY


class TestDenyListCheckerScope:
    @pytest.mark.parametrize(
        "tool_name",
        ["Read", "Write", "Edit", "Grep"],
    )
    def test_non_bash_tools_always_allow(
        self,
        checker: DenyListChecker,
        ctx: ToolExecutionContext,
        tool_name: str,
    ) -> None:
        # D12.2: deny-list only scopes Bash; other tools pass through.
        decision = checker.evaluate(tool_name, _DummyArgs(value="rm -rf /"), ctx)
        assert decision is Decision.ALLOW

    def test_bash_args_without_command_attribute_allowed(
        self, checker: DenyListChecker, ctx: ToolExecutionContext
    ) -> None:
        # Defensive duck-typing path: shouldn't happen in practice (loop
        # validates BashInput before evaluate), but behaves gracefully.
        decision = checker.evaluate("Bash", _DummyArgs(value="anything"), ctx)
        assert decision is Decision.ALLOW
