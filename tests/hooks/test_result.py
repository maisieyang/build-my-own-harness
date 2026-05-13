"""Tests for ``HookResult`` + ``HookEvent`` — P3-T4.4a.

Foundation types of the middleware (hook) layer. P3-T4 Three-Axis F locked:

- Single ``HookResult`` class (not 5 per-event types) — contract small,
  evolves additively
- 7 fields covering all 5 events' modify surfaces
- Classmethods encode per-event correctness;直接构造允许但不 validate
- ``HookEvent`` Literal with exactly 5 values

Detailed per-event field semantics (which fields each event honors) is
tested in ``test_executor.py`` (4d);this file covers the type/dataclass
contract only.
"""

from __future__ import annotations

import dataclasses
import typing
from typing import get_args

import pytest

from openharness.hooks import HookEvent, HookResult


class TestHookEventLiteral:
    """``HookEvent`` is a closed Literal of 5 lifecycle event names."""

    def test_event_names_match_three_axis(self) -> None:
        # Boundary D13.1 锁定 5 events. Three-Axis F documented them.
        expected = {
            "PreToolUse",
            "PostToolUse",
            "PreApiCall",
            "PostApiCall",
            "OnError",
        }
        assert set(get_args(HookEvent)) == expected

    def test_event_count_is_exactly_five(self) -> None:
        # 防止意外加 / 删 event;改要更新 decision + tests + docstring 三处。
        assert len(get_args(HookEvent)) == 5


class TestHookResultClassmethods:
    """Common-case constructors. 90% 用户走这 5 个。"""

    def test_allow_classmethod(self) -> None:
        result = HookResult.allow()
        assert result.decision == "allow"
        # All modify fields stay None for allow.
        assert result.message is None
        assert result.new_input is None
        assert result.new_request is None
        assert result.new_output is None
        assert result.new_metadata is None
        assert result.new_is_error is None

    def test_deny_classmethod(self) -> None:
        result = HookResult.deny("policy violation")
        assert result.decision == "deny"
        assert result.message == "policy violation"

    def test_modify_input_classmethod(self) -> None:
        result = HookResult.modify_input({"path": "/safe.txt"})
        assert result.decision == "modify"
        assert result.new_input == {"path": "/safe.txt"}
        # Other modify fields untouched.
        assert result.new_output is None
        assert result.new_request is None

    def test_modify_request_classmethod(self) -> None:
        # PreApiCall modify — request is typed `object` to avoid protocols
        # circular import (Phase 5 may tighten).
        fake_request = object()
        result = HookResult.modify_request(fake_request)
        assert result.decision == "modify"
        assert result.new_request is fake_request

    def test_modify_output_only(self) -> None:
        result = HookResult.modify_output("[REDACTED]")
        assert result.decision == "modify"
        assert result.new_output == "[REDACTED]"
        assert result.new_is_error is None
        assert result.new_metadata is None

    def test_modify_output_with_is_error(self) -> None:
        result = HookResult.modify_output("post-hook deny: secret leak", new_is_error=True)
        assert result.decision == "modify"
        assert result.new_output == "post-hook deny: secret leak"
        assert result.new_is_error is True

    def test_modify_output_with_metadata(self) -> None:
        result = HookResult.modify_output(
            "result",
            new_metadata={"redacted": True, "scrubbed_keys": 3},
        )
        assert result.decision == "modify"
        assert result.new_metadata == {"redacted": True, "scrubbed_keys": 3}


class TestHookResultFrozen:
    """Once constructed, HookResult is immutable."""

    def test_decision_cannot_be_mutated(self) -> None:
        result = HookResult.allow()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.decision = "deny"  # type: ignore[misc]

    def test_message_cannot_be_mutated(self) -> None:
        result = HookResult.deny("x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.message = "y"  # type: ignore[misc]


class TestHookResultDirectConstruction:
    """Direct construction is allowed (power-user) but classmethods are recommended.

    Per Three-Axis F:no runtime validation of field/decision consistency —
    classmethods enforce the convention;direct construction trusts the user.
    """

    def test_direct_construction_with_only_decision(self) -> None:
        result = HookResult(decision="allow")
        assert result.decision == "allow"

    def test_contradictory_combo_silently_accepted(self) -> None:
        # decision="allow" with new_input set is contradictory — but
        # construction succeeds. Executor only honors new_input when
        # decision == "modify"; allow → new_input is ignored at runtime.
        result = HookResult(decision="allow", new_input={"x": 1})
        assert result.decision == "allow"
        assert result.new_input == {"x": 1}  # stored but executor ignores

    def test_no_post_init_validation(self) -> None:
        # Make sure no __post_init__ validator was sneakily added.
        # If someone wants validation in Phase 4+, this test signals the
        # change (it would need updating).
        # Simply construct an unusual combo and assert it didn't raise:
        result = HookResult(decision="deny", new_output="x", new_input={"y": 1})
        assert result.decision == "deny"


class TestHookResultEquality:
    """Frozen dataclasses get __eq__ for free — let's make sure it works
    so tests can assert on full HookResult instances."""

    def test_two_allow_results_are_equal(self) -> None:
        assert HookResult.allow() == HookResult.allow()

    def test_two_deny_with_same_message_are_equal(self) -> None:
        assert HookResult.deny("x") == HookResult.deny("x")

    def test_two_deny_with_different_messages_are_not_equal(self) -> None:
        assert HookResult.deny("x") != HookResult.deny("y")


class TestPublicAPI:
    """Both names re-exported from ``openharness.hooks`` package root."""

    def test_hook_event_importable_from_package_root(self) -> None:
        from openharness.hooks import HookEvent as _HookEvent

        assert _HookEvent is HookEvent

    def test_hook_result_importable_from_package_root(self) -> None:
        from openharness.hooks import HookResult as _HookResult

        assert _HookResult is HookResult

    def test_typing_module_recognizes_hook_event(self) -> None:
        # HookEvent should be a real Literal (not a TypeAlias of str).
        # typing.get_origin returns Literal for Literal aliases.
        assert typing.get_origin(HookEvent) is typing.Literal
