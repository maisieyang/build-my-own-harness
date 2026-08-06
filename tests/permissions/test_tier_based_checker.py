"""Tests for ``TierBasedPermissionChecker`` — P3-T3.3e.

The class composes Tier 1 / 2 / 3 + a Bash catastrophic-pattern check
into a single ``PermissionChecker`` implementation that ``cli.py`` wires
into ``QueryContext`` (replaces Phase 2's ``DenyListChecker``).

Mapping (per ``decisions/08`` D13.2 + Three-Axis G):

- Tier 1 hit → ``DecisionResult.deny`` (framework-owned catastrophic)
- Tier 2 hit → ``DecisionResult.deny`` (user-configured)
- Bash deny-list hit → ``DecisionResult.deny`` (carry-over from DenyListChecker)
- Tier 3 hit → ``DecisionResult.ask`` (write-outside-cwd:boundary,
  maps to ALLOW under --auto, DENY-with-hint under DEFAULT)
- No hit → ``DecisionResult.allow``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from openharness.permissions import (
    Decision,
    DecisionResult,
    PermissionChecker,
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


# --------------------------------------------------------------------------- #
# Test fixtures: a tiny Tool registry covering one read-only + one write tool #
# --------------------------------------------------------------------------- #


class _PathInput(BaseModel):
    path: str


class _ReadyOnlyTool(BaseTool[_PathInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "FakeRead"
    description = "Fake read-only tool for tests."
    input_model = _PathInput
    is_read_only = True

    async def execute(self, args: _PathInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


class _WriteTool(BaseTool[_PathInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "FakeWrite"
    description = "Fake write tool for tests."
    input_model = _PathInput
    is_read_only = False

    async def execute(self, args: _PathInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


class _BashLikeInput(BaseModel):
    command: str


class _FakeBashTool(BaseTool[_BashLikeInput]):
    execution_domain = ExecutionDomain.LOCAL_DATA
    name = "Bash"
    description = "Fake bash for tests."
    input_model = _BashLikeInput
    is_read_only = False

    async def execute(self, args: _BashLikeInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="ok")


def _registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_ReadyOnlyTool())
    r.register(_WriteTool())
    r.register(_FakeBashTool())
    return r


class _StubSettings:
    """Minimal Settings-shaped stub: ``deny_paths`` (Tier 2) + ``permissions`` (L2 rules).

    loop-runtime L2 widened the checker constructor to read ``settings.permissions``;
    this fixture grows the field to honor that contract. Existing assertions below
    are unchanged — with empty ``permissions`` + the default ``headless=False``, the
    rule layer is a no-op and legacy Tier 1/2/3 behavior is preserved.
    """

    def __init__(
        self,
        deny_paths: tuple[str, ...] = (),
        permissions: PermissionRules | None = None,
    ) -> None:
        self.deny_paths = deny_paths
        self.permissions = permissions if permissions is not None else PermissionRules()


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


class TestSatisfiesProtocol:
    def test_class_satisfies_permission_checker(self, tmp_path: Path) -> None:
        # Structural typing — assigning to PermissionChecker-typed binding
        # is itself the assertion (mypy strict + runtime sanity).
        checker: PermissionChecker = TierBasedPermissionChecker(
            registry=_registry(),
            settings=_StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeRead",
            _PathInput(path=str(tmp_path / "README.md")),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert isinstance(result, DecisionResult)


class TestTier1Hardcoded:
    def test_read_ssh_key_denied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake-home")
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeRead",
            _PathInput(path="/fake-home/.ssh/id_rsa"),
            ToolExecutionContext(cwd=tmp_path),
        )
        # Even read-only is blocked by Tier 1 — SSH keys are universal NO.
        assert result.decision is Decision.DENY
        assert "sensitive system path" in (result.reason or "")
        assert "~/.ssh/**" in (result.reason or "")

    def test_etc_passwd_denied(self, tmp_path: Path) -> None:
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeWrite",
            _PathInput(path="/etc/passwd"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY
        assert "/etc/passwd" in (result.reason or "")


class TestTier2UserGlobs:
    def test_user_deny_pattern_blocks(self, tmp_path: Path) -> None:
        target = tmp_path / "secrets" / "keys.txt"
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(deny_paths=("secrets/**",)),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeRead",
            _PathInput(path=str(target)),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY
        assert "secrets/**" in (result.reason or "")

    def test_user_deny_does_not_match_other_paths(self, tmp_path: Path) -> None:
        target = tmp_path / "README.md"
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(deny_paths=("secrets/**",)),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeRead",
            _PathInput(path=str(target)),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ALLOW


class TestTier3ModeBased:
    def test_write_outside_cwd_asks(self, tmp_path: Path) -> None:
        # P3-T3 Three-Axis G:strict tool + outside cwd → ASK (not DENY).
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeWrite",
            _PathInput(path="/tmp/random-other-place.txt"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ASK
        assert "outside project root" in (result.reason or "")

    def test_read_outside_cwd_allowed(self, tmp_path: Path) -> None:
        # Read-only ⇒ lax;读外部文件是合理使用(看 ~/Documents/notes.md etc.)。
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeRead",
            _PathInput(path="/tmp/external-doc.txt"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ALLOW

    def test_write_inside_cwd_allowed(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "main.py"
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeWrite",
            _PathInput(path=str(target)),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ALLOW


class TestBashCatastrophicPatterns:
    """Carry-over from Phase 2 DenyListChecker — Bash substring deny-list."""

    def test_rm_rf_root_denied(self, tmp_path: Path) -> None:
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "Bash",
            _BashLikeInput(command="rm -rf /"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY
        assert "Bash pattern" in (result.reason or "")

    def test_fork_bomb_denied(self, tmp_path: Path) -> None:
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "Bash",
            _BashLikeInput(command=":(){ :|:& };:"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY

    def test_safe_bash_not_caught_by_denylist(self, tmp_path: Path) -> None:
        # Intent preserved (deny-list doesn't over-match a safe command), but
        # under D44 a bare interactive Bash is ASK not ALLOW; express the
        # "not denied" invariant via an allow rule so the assertion stays
        # about the deny-list, not about the D44 posture change.
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(permissions=PermissionRules(allow=("Bash(ls:*)",))),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "Bash",
            _BashLikeInput(command="ls /tmp"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ALLOW  # allowed, NOT denied by list


class TestTierResolutionOrder:
    """First-match-wins across Tier 1 / 2 / Bash deny / Tier 3."""

    def test_tier1_wins_over_tier2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake-home")
        # path matches BOTH Tier 1 (~/.ssh/**) and a user Tier 2 rule —
        # Tier 1 should be reported first (framework-owned takes priority).
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(deny_paths=("**/*id_rsa*",)),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "FakeRead",
            _PathInput(path="/fake-home/.ssh/id_rsa"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY
        assert "sensitive system path" in (result.reason or "")  # Tier 1 wording


class TestPathlessTools:
    def test_interactive_safe_bash_asks(self, tmp_path: Path) -> None:
        # D44 (F9 fix, dogfood Day 2): interactive mode, a mutating pathless
        # tool (Bash) that cleared the catastrophic deny-list + git red line +
        # allow rules must ASK — it is a general-compute channel that bypasses
        # every path-based file gate (echo>/tmp, brew install). Was ALLOW
        # (the F9 hole); this assertion IS the vulnerability nailed as a change.
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(deny_paths=("secrets/**",)),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "Bash",
            _BashLikeInput(command="echo hello > /tmp/x"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ASK

    def test_allow_rule_short_circuits_before_ask(self, tmp_path: Path) -> None:
        # A user-approved Bash prefix rule must NOT be re-asked (step 4 ALLOW
        # short-circuits before the D44 ASK) — aligns with CC "unless matched
        # by an approved rule".
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(permissions=PermissionRules(allow=("Bash(echo:*)",))),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "Bash",
            _BashLikeInput(command="echo hello > /tmp/x"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ALLOW

    def test_headless_bash_still_denies(self, tmp_path: Path) -> None:
        # D44 must not touch headless: the fail-closed gate (step 6) still
        # DENIES a pathless mutating tool with no allow rule.
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
            headless=True,
        )
        result = checker.evaluate(
            "Bash",
            _BashLikeInput(command="echo hello > /tmp/x"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY

    def test_catastrophic_still_denies_ahead_of_ask(self, tmp_path: Path) -> None:
        # Priority intact: the disaster deny-list (step 1) beats the D44 ASK.
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "Bash",
            _BashLikeInput(command="rm -rf /"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.DENY


class TestUnknownTool:
    def test_unknown_tool_allowed_loop_handles(self, tmp_path: Path) -> None:
        # Tool not in registry → ALLOW (the loop's registry.get raises
        # KeyError separately; AuthZ shouldn't second-guess that).
        checker = TierBasedPermissionChecker(
            _registry(),
            _StubSettings(),  # type: ignore[arg-type]
        )
        result = checker.evaluate(
            "DoesNotExist",
            _PathInput(path=str(tmp_path / "foo")),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision is Decision.ALLOW
