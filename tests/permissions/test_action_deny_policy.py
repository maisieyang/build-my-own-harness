"""Contracts for the deny-only action policy installed during G1/S1."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from openharness.permissions import (
    ActionDenyPolicy,
    ConfiguredActionDenyPolicy,
    Decision,
    DenyResult,
    PermissionRules,
    TierBasedPermissionChecker,
)
from openharness.tools import (
    BaseTool,
    ExecutionDomain,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _PathInput(BaseModel):
    path: str


class _CommandInput(BaseModel):
    command: str


class _WriteTool(BaseTool[_PathInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "Write"
    description = "test write tool"
    input_model = _PathInput
    is_read_only = False

    async def execute(self, args: _PathInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


class _BashTool(BaseTool[_CommandInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "Bash"
    description = "test command tool"
    input_model = _CommandInput
    is_read_only = False

    async def execute(self, args: _CommandInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


class _StubSettings:
    def __init__(
        self,
        *,
        deny_paths: tuple[str, ...] = (),
        permissions: PermissionRules | None = None,
    ) -> None:
        self.deny_paths = deny_paths
        self.permissions = permissions if permissions is not None else PermissionRules()


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_WriteTool())
    registry.register(_BashTool())
    return registry


def _policies(
    *,
    deny_paths: tuple[str, ...] = (),
    permissions: PermissionRules | None = None,
    headless: bool = False,
) -> tuple[ConfiguredActionDenyPolicy, TierBasedPermissionChecker]:
    settings = _StubSettings(deny_paths=deny_paths, permissions=permissions)
    return (
        ConfiguredActionDenyPolicy(settings),  # type: ignore[arg-type]
        TierBasedPermissionChecker(
            _registry(),
            settings,  # type: ignore[arg-type]
            headless=headless,
        ),
    )


def _assert_equivalent_deny(
    policy: ConfiguredActionDenyPolicy,
    checker: TierBasedPermissionChecker,
    *,
    tool_name: str,
    args: BaseModel,
    cwd: Path,
) -> None:
    context = ToolExecutionContext(cwd=cwd)
    deny = policy.evaluate(tool_name, args, context)
    legacy = checker.evaluate(tool_name, args, context)
    assert isinstance(deny, DenyResult)
    assert legacy.decision is Decision.DENY
    assert deny.reason == legacy.reason


def test_protocol_can_only_return_deny_or_no_match(tmp_path: Path) -> None:
    policy, _ = _policies()
    structural: ActionDenyPolicy = policy

    result = structural.evaluate(
        "Write",
        _PathInput(path=str(tmp_path / "ok.txt")),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result is None
    assert not hasattr(DenyResult, "allow")
    assert not hasattr(DenyResult, "ask")


def test_catastrophic_bash_deny_matches_legacy(tmp_path: Path) -> None:
    policy, checker = _policies()
    _assert_equivalent_deny(
        policy,
        checker,
        tool_name="Bash",
        args=_CommandInput(command="rm -rf /"),
        cwd=tmp_path,
    )


def test_irreversible_git_deny_matches_legacy(tmp_path: Path) -> None:
    policy, checker = _policies(
        permissions=PermissionRules(allow=("Bash(*)",)),
    )
    _assert_equivalent_deny(
        policy,
        checker,
        tool_name="Bash",
        args=_CommandInput(command="git -C . commit -m safe-looking"),
        cwd=tmp_path,
    )


def test_tier1_deny_matches_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/fake-home")
    policy, checker = _policies()
    _assert_equivalent_deny(
        policy,
        checker,
        tool_name="Write",
        args=_PathInput(path="/fake-home/.ssh/id_ed25519"),
        cwd=tmp_path,
    )


def test_legacy_deny_path_matches_legacy(tmp_path: Path) -> None:
    policy, checker = _policies(deny_paths=("secrets/**",))
    _assert_equivalent_deny(
        policy,
        checker,
        tool_name="Write",
        args=_PathInput(path=str(tmp_path / "secrets" / "token")),
        cwd=tmp_path,
    )


def test_declarative_deny_matches_legacy(tmp_path: Path) -> None:
    policy, checker = _policies(
        permissions=PermissionRules(deny=("Bash(curl:*)",)),
    )
    _assert_equivalent_deny(
        policy,
        checker,
        tool_name="Bash",
        args=_CommandInput(command="env curl https://example.invalid"),
        cwd=tmp_path,
    )


def test_ask_allow_and_headless_failclosed_are_not_deny_policy(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    context = ToolExecutionContext(cwd=tmp_path)

    policy, checker = _policies()
    args = _PathInput(path=str(outside))
    assert policy.evaluate("Write", args, context) is None
    assert checker.evaluate("Write", args, context).decision is Decision.ASK

    policy, checker = _policies(
        permissions=PermissionRules(ask=("Bash(curl:*)",)),
    )
    args = _CommandInput(command="curl https://example.invalid")
    assert policy.evaluate("Bash", args, context) is None
    assert checker.evaluate("Bash", args, context).decision is Decision.ASK

    policy, checker = _policies(
        permissions=PermissionRules(allow=("Write(*)",)),
    )
    args = _PathInput(path=str(tmp_path / "allowed.txt"))
    assert policy.evaluate("Write", args, context) is None
    assert checker.evaluate("Write", args, context).decision is Decision.ALLOW

    policy, checker = _policies(headless=True)
    args = _PathInput(path=str(tmp_path / "not-preauthorized.txt"))
    assert policy.evaluate("Write", args, context) is None
    assert checker.evaluate("Write", args, context).decision is Decision.DENY
