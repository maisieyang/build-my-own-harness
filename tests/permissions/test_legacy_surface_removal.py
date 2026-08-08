"""G3/S7 machine gates proving the legacy permission product surface is gone."""

from __future__ import annotations

from dataclasses import fields

import pytest

import openharness.permissions as permissions
from openharness.config.settings import Settings
from openharness.engine.context import QueryContext
from openharness.services.snapshot import SNAPSHOT_SCHEMA, SNAPSHOT_VERSION


def test_public_permission_api_exposes_only_canonical_authority() -> None:
    legacy_names = {
        "Decision",
        "DecisionResult",
        "DenyListChecker",
        "PermissionChecker",
        "PermissionMode",
        "PermissionRules",
        "TierBasedPermissionChecker",
        "accept_edits_preset",
        "match_rules",
        "parse_rule",
        "plan_mode_preset",
        "postures_from_legacy_mode",
    }

    assert legacy_names.isdisjoint(permissions.__all__)
    assert all(not hasattr(permissions, name) for name in legacy_names)


def test_query_context_has_no_legacy_authority_fields() -> None:
    names = {item.name for item in fields(QueryContext)}

    assert "permission_checker" not in names
    assert "permission_mode" not in names
    assert "external_tool_policy" not in names
    assert "runtime_permission_profile" in names


def test_settings_has_no_legacy_permission_or_sandbox_intent_fields() -> None:
    setting_fields = Settings.model_fields

    assert "permission_profile" in setting_fields
    assert {
        "permission_mode",
        "deny_paths",
        "permissions",
        "sandbox_network",
        "sandbox_network_policy",
        "sandbox_external_tool_policy",
    }.isdisjoint(setting_fields)


@pytest.mark.parametrize(
    "name",
    [
        "OPENHARNESS_PERMISSION_MODE",
        "OPENHARNESS_DENY_PATHS",
        "OPENHARNESS_PERMISSIONS__ALLOW",
        "OPENHARNESS_PERMISSIONS__DENY",
        "OPENHARNESS_PERMISSIONS__ASK",
        "OPENHARNESS_SANDBOX_NETWORK",
        "OPENHARNESS_SANDBOX_NETWORK_POLICY__ENABLED",
        "OPENHARNESS_SANDBOX_EXTERNAL_TOOL_POLICY__WEB",
    ],
)
def test_legacy_environment_is_rejected_with_canonical_replacement(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv(name, "true")

    with pytest.raises(ValueError, match="OPENHARNESS_PERMISSION_PROFILE"):
        Settings(_env_file=None)


def test_new_snapshots_use_v2_without_mixed_permission_mode() -> None:
    assert SNAPSHOT_VERSION == 2
    assert SNAPSHOT_SCHEMA == "openharness.snapshot.v2"
