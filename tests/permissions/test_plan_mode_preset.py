"""plan-mode D47.1 — ``plan_mode_preset()`` deny 预设 (RED).

验收(decisions/47 T1):预设生效时 Edit/Write/Bash 一律 deny——含交互态
in-cwd 写(Tier3 本会放行)与 deny 压过 acceptEdits allow;只读工具不受
影响."收编为规则预设"立场(accept_edits_preset 先例)的第二次应用:deny
镜像,不是新 PermissionMode 枚举值.

这些测试现在应当 RED:``plan_mode_preset`` 尚不存在.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from openharness.permissions import Decision, TierBasedPermissionChecker
from openharness.permissions.rules import (
    PermissionRules,
    accept_edits_preset,
    plan_mode_preset,
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


class _PathInput(BaseModel):
    path: str


class _CmdInput(BaseModel):
    command: str


class _WriteTool(BaseTool[_PathInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "Write"
    description = "mutating path tool"
    input_model = _PathInput
    is_read_only = False

    async def execute(self, args: _PathInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


class _EditTool(_WriteTool):
    name = "Edit"


class _ReadTool(BaseTool[_PathInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "Read"
    description = "read-only path tool"
    input_model = _PathInput
    is_read_only = True

    async def execute(self, args: _PathInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


class _BashTool(BaseTool[_CmdInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "Bash"
    description = "command tool"
    input_model = _CmdInput
    is_read_only = False

    async def execute(self, args: _CmdInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


def _registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_WriteTool())
    r.register(_EditTool())
    r.register(_ReadTool())
    r.register(_BashTool())
    return r


class _StubSettings:
    def __init__(self, *, permissions: PermissionRules) -> None:
        self.deny_paths: tuple[str, ...] = ()
        self.permissions = permissions


def _checker(permissions: PermissionRules) -> TierBasedPermissionChecker:
    # Interactive posture (headless=False) on purpose: Tier3 would normally
    # ALLOW in-cwd mutating calls there, so a DENY can only come from the
    # plan preset — the sharpest wiring assertion available.
    return TierBasedPermissionChecker(
        _registry(),
        _StubSettings(permissions=permissions),  # type: ignore[arg-type]
        headless=False,
    )


class TestPresetShape:
    def test_covers_edit_write_bash(self) -> None:
        preset = plan_mode_preset()
        assert "Edit(*)" in preset
        assert "Write(*)" in preset
        assert "Bash(*)" in preset

    def test_is_exactly_the_mutating_trio(self) -> None:
        # v1 整拒 Bash(D47 OUT:只读分类归 v2);不夹带其它规则.
        assert set(plan_mode_preset()) == {"Edit(*)", "Write(*)", "Bash(*)"}


class TestPlanDenyBlocksMutating:
    def test_write_in_cwd_denied(self, tmp_path: Path) -> None:
        checker = _checker(PermissionRules(deny=plan_mode_preset()))
        result = checker.evaluate(
            "Write",
            _PathInput(path=str(tmp_path / "src" / "main.py")),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY

    def test_edit_in_cwd_denied(self, tmp_path: Path) -> None:
        checker = _checker(PermissionRules(deny=plan_mode_preset()))
        result = checker.evaluate(
            "Edit",
            _PathInput(path=str(tmp_path / "README.md")),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY

    def test_bash_any_command_denied(self, tmp_path: Path) -> None:
        # v1 整拒:连无害命令也拦(声明简化 D47 OUT 表).
        checker = _checker(PermissionRules(deny=plan_mode_preset()))
        result = checker.evaluate(
            "Bash", _CmdInput(command="ls -la"), ToolExecutionContext(cwd=tmp_path)
        )
        assert result.decision is Decision.DENY


class TestDenyBeatsAllow:
    def test_plan_deny_overrides_accept_edits_allow(self, tmp_path: Path) -> None:
        # 预设叠加序:进 plan 时 acceptEdits 可能已在 allow 列;deny > allow
        # 的引擎序保证 plan 钳制仍然成立.
        checker = _checker(PermissionRules(allow=accept_edits_preset(), deny=plan_mode_preset()))
        result = checker.evaluate(
            "Write",
            _PathInput(path=str(tmp_path / "notes.md")),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY


class TestReadOnlyUnaffected:
    def test_read_still_allowed(self, tmp_path: Path) -> None:
        checker = _checker(PermissionRules(deny=plan_mode_preset()))
        result = checker.evaluate(
            "Read",
            _PathInput(path=str(tmp_path / "src" / "main.py")),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ALLOW
