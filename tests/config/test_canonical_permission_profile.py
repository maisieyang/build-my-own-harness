"""G3/S6 deletion gates for the canonical permission product surface."""

from __future__ import annotations

import pytest

from openharness.config.settings import Settings
from openharness.permissions import (
    ExternalToolPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    FilesystemScope,
    LegacyPermissionInputs,
    LegacyPermissionMigrationError,
    NetworkPolicy,
    RuntimePermissionProfile,
    translate_legacy_permission_config,
    workspace_runtime_profile,
)


def _settings(**overrides: object) -> Settings:
    return Settings(api_key="sk-fake", base_url="https://fake.example/v1", **overrides)  # type: ignore[arg-type]


def test_settings_has_one_canonical_profile_default() -> None:
    settings = _settings()

    assert settings.permission_profile == workspace_runtime_profile()


def test_canonical_profile_loads_from_nested_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENHARNESS_PERMISSION_PROFILE__NETWORK__ENABLED", "true")
    monkeypatch.setenv(
        "OPENHARNESS_PERMISSION_PROFILE__NETWORK__ALLOW_DOMAINS",
        '["pypi.org"]',
    )
    monkeypatch.setenv("OPENHARNESS_PERMISSION_PROFILE__EXTERNAL_TOOLS__WEB", "allow")

    settings = Settings()

    assert settings.permission_profile.network == NetworkPolicy(
        enabled=True,
        allow_domains=("pypi.org",),
    )
    assert settings.permission_profile.external_tools == ExternalToolPolicy(web="allow")
    assert settings.permission_profile.filesystem == workspace_runtime_profile().filesystem


def test_semantically_duplicate_profile_rules_have_same_fingerprint() -> None:
    canonical = RuntimePermissionProfile(
        name="workspace",
        filesystem=FilesystemPolicy(
            rules=(FilesystemRule(path="src", access=FilesystemAccess.READ),)
        ),
    )
    duplicate = RuntimePermissionProfile(
        name="workspace",
        filesystem=FilesystemPolicy(
            rules=(
                FilesystemRule(path="src/.", access=FilesystemAccess.READ),
                FilesystemRule(path="src", access=FilesystemAccess.READ),
            )
        ),
    )

    assert canonical.fingerprint == duplicate.fingerprint


def test_safe_legacy_explicit_paths_translate_without_expanding_authority() -> None:
    result = translate_legacy_permission_config(
        workspace_runtime_profile(),
        LegacyPermissionInputs(
            deny_paths=("secrets",),
            allow=("Read(/opt/reference)",),
            deny=("Write(generated)",),
        ),
    )

    translated = {
        (rule.normalized_path(), rule.access, rule.scope)
        for rule in result.profile.filesystem.rules
    }
    assert ("secrets", FilesystemAccess.DENY, FilesystemScope.EXACT) in translated
    assert ("/opt/reference", FilesystemAccess.READ, FilesystemScope.EXACT) in translated
    assert ("generated", FilesystemAccess.DENY_WRITE, FilesystemScope.EXACT) in translated
    assert result.profile.network == workspace_runtime_profile().network
    assert result.profile.external_tools == workspace_runtime_profile().external_tools
    assert result.warnings


def test_safe_legacy_subtree_rule_preserves_subtree_scope() -> None:
    result = translate_legacy_permission_config(
        RuntimePermissionProfile(name="empty"),
        LegacyPermissionInputs(allow=("Read(src/**)",)),
    )

    assert result.profile.filesystem.rules == (
        FilesystemRule(
            path="src",
            access=FilesystemAccess.READ,
            scope=FilesystemScope.SUBTREE,
        ),
    )


def test_empty_legacy_input_is_a_warning_free_identity_translation() -> None:
    base = workspace_runtime_profile()

    result = translate_legacy_permission_config(base, LegacyPermissionInputs())

    assert result.profile == base
    assert result.warnings == ()


@pytest.mark.parametrize(
    "legacy",
    [
        LegacyPermissionInputs(ask=("Write(out.txt)",)),
        LegacyPermissionInputs(allow=("Bash(uv run pytest:*)",)),
        LegacyPermissionInputs(allow=("Read(secrets/*.txt)",)),
        LegacyPermissionInputs(deny_paths=("*.env",)),
        LegacyPermissionInputs(deny_paths=("",)),
        LegacyPermissionInputs(deny_paths=("/**",)),
        LegacyPermissionInputs(allow=("not-a-rule",)),
        LegacyPermissionInputs(deny_paths=("./**",)),
    ],
)
def test_unsafe_or_unrepresentable_legacy_rules_fail_closed(
    legacy: LegacyPermissionInputs,
) -> None:
    with pytest.raises(LegacyPermissionMigrationError, match="cannot migrate"):
        translate_legacy_permission_config(workspace_runtime_profile(), legacy)


def test_legacy_network_and_external_policy_translate_as_typed_profile_facts() -> None:
    network = NetworkPolicy(enabled=True, allow_domains=("pypi.org",))
    external = ExternalToolPolicy(web="allow")

    result = translate_legacy_permission_config(
        workspace_runtime_profile(),
        LegacyPermissionInputs(
            sandbox_network_policy=network,
            sandbox_external_tool_policy=external,
        ),
    )

    assert result.profile.network == network
    assert result.profile.external_tools == external


@pytest.mark.parametrize(
    "domain",
    [
        "\ud800",
        f"{'a' * 64}.example",
    ],
)
def test_network_profile_rejects_domains_that_cannot_be_safely_normalized(
    domain: str,
) -> None:
    with pytest.raises(ValueError, match="invalid network domain rule"):
        NetworkPolicy(enabled=True, allow_domains=(domain,))
