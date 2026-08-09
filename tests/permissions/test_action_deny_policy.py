"""Contracts for the canonical negative-only semantic guard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from openharness.permissions import (
    ActionDenyPolicy,
    ConfiguredActionDenyPolicy,
    DenyResult,
    PlanActionDenyPolicy,
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

    async def execute(self, args: _PathInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


class _BashTool(BaseTool[_CommandInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "Bash"
    description = "test command tool"
    input_model = _CommandInput

    async def execute(self, args: _CommandInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_WriteTool())
    registry.register(_BashTool())
    return registry


def _deny(policy: ActionDenyPolicy, tool: str, args: BaseModel, cwd: Path) -> DenyResult:
    result = policy.evaluate(tool, args, ToolExecutionContext(cwd=cwd))
    assert isinstance(result, DenyResult)
    return result


def test_protocol_can_only_return_deny_or_no_match(tmp_path: Path) -> None:
    structural: ActionDenyPolicy = ConfiguredActionDenyPolicy()
    assert (
        structural.evaluate(
            "Write",
            _PathInput(path=str(tmp_path / "ok.txt")),
            ToolExecutionContext(cwd=tmp_path),
        )
        is None
    )
    assert not hasattr(DenyResult, "allow")
    assert not hasattr(DenyResult, "ask")


def test_catastrophic_bash_is_a_framework_hard_deny(tmp_path: Path) -> None:
    result = _deny(
        ConfiguredActionDenyPolicy(),
        "Bash",
        _CommandInput(command="rm -rf /"),
        tmp_path,
    )
    assert "catastrophic" in result.reason


def test_irreversible_git_is_a_framework_hard_deny(tmp_path: Path) -> None:
    result = _deny(
        ConfiguredActionDenyPolicy(),
        "Bash",
        _CommandInput(command="git -C . commit -m safe-looking"),
        tmp_path,
    )
    assert "irreversible git action" in result.reason


def test_sensitive_identity_path_is_a_framework_hard_deny(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/fake-home")
    result = _deny(
        ConfiguredActionDenyPolicy(),
        "Write",
        _PathInput(path="/fake-home/.ssh/id_ed25519"),
        tmp_path,
    )
    assert "sensitive system path" in result.reason


def test_user_scope_is_not_duplicated_in_negative_semantic_guard(tmp_path: Path) -> None:
    policy = ConfiguredActionDenyPolicy()
    context = ToolExecutionContext(cwd=tmp_path)
    assert policy.evaluate("Write", _PathInput(path="outside.txt"), context) is None
    assert (
        policy.evaluate("Bash", _CommandInput(command="curl https://example.invalid"), context)
        is None
    )


def test_plan_policy_denies_mutating_and_delegated_capabilities(tmp_path: Path) -> None:
    from openharness.tools import SpawnAgent

    registry = _registry()
    delegate = SpawnAgent()
    delegate.is_read_only = True
    registry.register(delegate)
    policy = PlanActionDenyPolicy(registry=registry, base=ConfiguredActionDenyPolicy())
    context = ToolExecutionContext(cwd=tmp_path)

    write = policy.evaluate("Write", _PathInput(path="notes.md"), context)
    delegated = policy.evaluate(
        "Agent",
        delegate.input_model(description="inspect", prompt="inspect only"),
        context,
    )

    assert write is not None and write.kind.value == "plan_capability"
    assert delegated is not None and delegated.kind.value == "plan_capability"


def test_plan_policy_preserves_base_deny_and_allows_read_only_no_match(
    tmp_path: Path,
) -> None:
    from openharness.tools import Read

    registry = _registry()
    registry.register(Read())
    policy = PlanActionDenyPolicy(registry=registry, base=ConfiguredActionDenyPolicy())
    context = ToolExecutionContext(cwd=tmp_path)

    denied = policy.evaluate("Bash", _CommandInput(command="rm -rf /"), context)
    assert denied is not None and denied.kind.value == "catastrophic_command"
    assert policy.evaluate("Read", _PathInput(path="README.md"), context) is None
    assert policy.evaluate("Unknown", _PathInput(path="ignored"), context) is None
