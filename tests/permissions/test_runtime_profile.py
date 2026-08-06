"""S1 contract tests for the canonical runtime permission profile."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.permissions.profile import (
    EnvironmentPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemRule,
    NetworkPolicy,
    RuntimePermissionProfile,
    workspace_runtime_profile,
)


def test_semantically_identical_profiles_have_identical_fingerprints() -> None:
    first = RuntimePermissionProfile(
        name="workspace",
        filesystem=FilesystemPolicy(
            rules=(
                FilesystemRule(path="./src", access=FilesystemAccess.WRITE),
                FilesystemRule(path="./docs", access=FilesystemAccess.READ),
            )
        ),
        environment=EnvironmentPolicy(include=("PATH", "LANG")),
    )
    reordered = RuntimePermissionProfile(
        name="workspace",
        filesystem=FilesystemPolicy(
            rules=(
                FilesystemRule(path="docs", access=FilesystemAccess.READ),
                FilesystemRule(path="src", access=FilesystemAccess.WRITE),
            )
        ),
        environment=EnvironmentPolicy(include=("LANG", "PATH")),
    )

    assert first.normalized() == reordered.normalized()
    assert first.fingerprint == reordered.fingerprint


def test_conflicting_filesystem_rules_fail_validation() -> None:
    with pytest.raises(ValidationError, match="conflicting filesystem rules"):
        FilesystemPolicy(
            rules=(
                FilesystemRule(path="src", access=FilesystemAccess.READ),
                FilesystemRule(path="./src", access=FilesystemAccess.DENY),
            )
        )


def test_disabled_network_cannot_contain_allow_rules() -> None:
    with pytest.raises(ValidationError, match="network is disabled"):
        NetworkPolicy(enabled=False, allow_domains=("pypi.org",))


def test_network_domains_and_unix_socket_paths_are_canonical_policy_inputs() -> None:
    with pytest.raises(ValidationError, match="domain rule"):
        NetworkPolicy(enabled=True, allow_domains=("https://pypi.org",))
    with pytest.raises(ValidationError, match="absolute"):
        NetworkPolicy(enabled=True, allow_unix_sockets=("relative.sock",))


def test_environment_include_and_exclude_cannot_overlap() -> None:
    with pytest.raises(ValidationError, match="both included and excluded"):
        EnvironmentPolicy(include=("PATH",), exclude=("PATH",))


def test_unknown_profile_fields_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RuntimePermissionProfile(name="workspace", magic_allow=True)  # type: ignore[call-arg]


def test_workspace_posture_writes_project_but_protects_control_paths() -> None:
    profile = workspace_runtime_profile()

    assert profile.network.enabled is False
    assert profile.process.timeout_seconds == 600.0
    assert profile.filesystem.rules == (
        FilesystemRule(path=".", access=FilesystemAccess.WRITE),
        FilesystemRule(path=".git", access=FilesystemAccess.DENY_WRITE),
        FilesystemRule(path=".codex", access=FilesystemAccess.DENY_WRITE),
        FilesystemRule(path=".agents", access=FilesystemAccess.DENY_WRITE),
    )
