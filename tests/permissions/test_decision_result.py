"""Tests for ``Decision.ASK`` + ``DecisionResult`` — P3-T3.3d.

Per ``decisions/08`` D13.1/D13.2 + P3-T3 Three-Axis G:permission decisions
gain a third state (ASK) and carry a reason string. Both are encapsulated
in :class:`DecisionResult`, which the ``PermissionChecker`` Protocol now
returns instead of the bare ``Decision`` enum.

Coverage:

- ``Decision.ASK`` is a third enum value alongside ALLOW / DENY
- ``DecisionResult`` carries ``decision`` + optional ``reason``
- ``DecisionResult.allow()`` / ``deny(reason)`` / ``ask(reason)`` constructors
  enforce the invariant (reason required for non-ALLOW)
- ``DecisionResult`` is frozen (no in-place mutation)
"""

from __future__ import annotations

import dataclasses

import pytest

from openharness.permissions import Decision, DecisionResult


class TestDecisionEnum:
    """``Decision`` is now a three-state enum (P3-T3.3d adds ASK)."""

    def test_allow_value_exists(self) -> None:
        assert Decision.ALLOW.value == "allow"

    def test_deny_value_exists(self) -> None:
        assert Decision.DENY.value == "deny"

    def test_ask_value_exists(self) -> None:
        # P3-T3.3d:第三态。Tier 3 命中映射到 ASK,Mode 解释为 ALLOW
        # (AUTO) 或 DENY-with-hint (DEFAULT,Phase 4+ 替换为 prompt)。
        assert Decision.ASK.value == "ask"

    def test_enum_has_exactly_three_members(self) -> None:
        # 防止意外加更多状态 —— 多了要更新决策记录 + 这个测试。
        assert len(Decision) == 3


class TestDecisionResultClassmethods:
    """``DecisionResult.allow()`` / ``deny()`` / ``ask()`` are the canonical
    constructors. They encode the invariant: reason populated for non-ALLOW.
    """

    def test_allow_classmethod(self) -> None:
        result = DecisionResult.allow()
        assert result.decision is Decision.ALLOW
        assert result.reason is None

    def test_deny_classmethod(self) -> None:
        result = DecisionResult.deny("matches sensitive path")
        assert result.decision is Decision.DENY
        assert result.reason == "matches sensitive path"

    def test_ask_classmethod(self) -> None:
        result = DecisionResult.ask("path outside project root")
        assert result.decision is Decision.ASK
        assert result.reason == "path outside project root"

    def test_default_reason_for_allow_is_none(self) -> None:
        # ALLOW 不需要 reason —— positive outcomes 不说理由也行。
        result = DecisionResult(decision=Decision.ALLOW)
        assert result.reason is None

    def test_direct_construction_allowed(self) -> None:
        # 给 Phase 4+ migration 路径留口子;classmethods 只是 ergonomic helpers。
        result = DecisionResult(decision=Decision.DENY, reason="x")
        assert result.decision is Decision.DENY
        assert result.reason == "x"


class TestDecisionResultFrozen:
    """``DecisionResult`` is immutable — never mutated after construction."""

    def test_frozen_dataclass(self) -> None:
        result = DecisionResult.allow()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.decision = Decision.DENY  # type: ignore[misc]

    def test_reason_cannot_be_changed(self) -> None:
        result = DecisionResult.deny("foo")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.reason = "bar"  # type: ignore[misc]
