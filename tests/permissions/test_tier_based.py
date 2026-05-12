"""Tests for the Tier-based permission checker building blocks — P3-T3.3a.

Sub-unit 3a ships only the foundations: the Tier 1 hardcoded pattern list
and the ``_glob_match`` helper that every Tier will use. Tier 2 / 3 / full
``TierBasedPermissionChecker`` land in 3b - 3e.

Coverage:
- ``_glob_match`` handles plain fnmatch + the ``dir/**`` recursive suffix
- ``~`` is expanded in both pattern and path before matching
- ``HARDCODED_SENSITIVE_PATHS`` covers the agreed 8 patterns
- ``_matches_tier1`` returns the matching pattern (for deny reason) or None
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.permissions.tier_based import (
    HARDCODED_SENSITIVE_PATHS,
    _glob_match,
    _matches_tier1,
)

if TYPE_CHECKING:
    import pytest


class TestGlobMatch:
    """``_glob_match`` is the shared matcher Tier 1 + Tier 2 both use."""

    def test_plain_fnmatch_extension(self) -> None:
        assert _glob_match("/tmp/foo.txt", "*.txt") is True
        assert _glob_match("/tmp/foo.md", "*.txt") is False

    def test_fnmatch_star_crosses_path_separator(self) -> None:
        # IMPORTANT fnmatch quirk: `*` matches ANY chars including `/`.
        # This differs from shell glob / .gitignore where `*` stays in one
        # path segment. Our policy (Three-Axis Micro-Decision B): document
        # the limitation; users who need single-level matching write
        # explicit patterns; recursive intent uses our `/**` suffix.
        assert _glob_match("/tmp/foo/bar", "/tmp/foo/*") is True
        # `*` crossing `/` means `/tmp/foo/*` ALSO matches deeper paths:
        assert _glob_match("/tmp/foo/bar/baz", "/tmp/foo/*") is True
        # The implication: most "deny" rules err on the side of broader
        # matching (safer for security; over-deny > under-deny).

    def test_recursive_double_star_suffix_matches_descendants(self) -> None:
        # `dir/**` is our extension — matches the dir itself and everything below.
        assert _glob_match("/etc/sensitive/x", "/etc/sensitive/**") is True
        assert _glob_match("/etc/sensitive/x/y/z", "/etc/sensitive/**") is True

    def test_recursive_double_star_matches_directory_itself(self) -> None:
        assert _glob_match("/etc/sensitive", "/etc/sensitive/**") is True

    def test_recursive_does_not_match_sibling_directories(self) -> None:
        # `/etc/sensitive/**` must not match `/etc/sensitivex/foo` or
        # `/etc/sensitive2/foo` — prefix-match must require trailing `/`.
        assert _glob_match("/etc/sensitivex/foo", "/etc/sensitive/**") is False
        assert _glob_match("/etc/other/foo", "/etc/sensitive/**") is False

    def test_tilde_expanded_in_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake/home")
        # Path explicit form; pattern uses ~
        assert _glob_match("/fake/home/.ssh/id_rsa", "~/.ssh/**") is True

    def test_tilde_expanded_in_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake/home")
        # Path uses ~; pattern explicit form (less common but supported)
        assert _glob_match("~/.ssh/id_rsa", "/fake/home/.ssh/**") is True

    def test_tilde_in_both_path_and_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake/home")
        assert _glob_match("~/.ssh/id_rsa", "~/.ssh/**") is True

    def test_exact_file_path_match(self) -> None:
        # Single-file pattern (no glob): exact match.
        assert _glob_match("/etc/passwd", "/etc/passwd") is True
        assert _glob_match("/etc/passwd.bak", "/etc/passwd") is False


class TestHardcodedSensitivePaths:
    """``HARDCODED_SENSITIVE_PATHS`` covers the 8 agreed Tier 1 patterns
    (decision/08 D13.2 + Three-Axis A)."""

    def test_includes_ssh_directory(self) -> None:
        assert "~/.ssh/**" in HARDCODED_SENSITIVE_PATHS

    def test_includes_aws_credentials_directory(self) -> None:
        assert "~/.aws/**" in HARDCODED_SENSITIVE_PATHS

    def test_includes_gpg_directory(self) -> None:
        assert "~/.gnupg/**" in HARDCODED_SENSITIVE_PATHS

    def test_includes_kube_directory(self) -> None:
        assert "~/.kube/**" in HARDCODED_SENSITIVE_PATHS

    def test_includes_github_cli_config(self) -> None:
        assert "~/.config/gh/**" in HARDCODED_SENSITIVE_PATHS

    def test_includes_system_secrets(self) -> None:
        assert "/etc/passwd" in HARDCODED_SENSITIVE_PATHS
        assert "/etc/shadow" in HARDCODED_SENSITIVE_PATHS
        assert "/etc/sudoers" in HARDCODED_SENSITIVE_PATHS

    def test_count_matches_three_axis_agreement(self) -> None:
        # Exactly 8 patterns — Three-Axis Micro-Decision A agreed list.
        # Adding more requires updating this test + the decision record.
        assert len(HARDCODED_SENSITIVE_PATHS) == 8

    def test_is_immutable(self) -> None:
        # Tuple, not list — runtime guarantees no accidental mutation.
        assert isinstance(HARDCODED_SENSITIVE_PATHS, tuple)


class TestMatchesTier1:
    """``_matches_tier1(path)`` returns the matching pattern (str) or None.

    Returning the pattern (not just bool) lets the caller build a reason
    message like 'matches sensitive system path (~/.ssh/**)'.
    """

    def test_ssh_key_path_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake/home")
        assert _matches_tier1("~/.ssh/id_rsa") == "~/.ssh/**"

    def test_aws_credentials_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake/home")
        assert _matches_tier1("~/.aws/credentials") == "~/.aws/**"

    def test_etc_passwd_exact_match(self) -> None:
        assert _matches_tier1("/etc/passwd") == "/etc/passwd"

    def test_etc_shadow_exact_match(self) -> None:
        assert _matches_tier1("/etc/shadow") == "/etc/shadow"

    def test_normal_project_file_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/fake/home")
        assert _matches_tier1("/Users/yang/project/src/main.py") is None

    def test_temp_file_returns_none(self) -> None:
        assert _matches_tier1("/tmp/scratch.txt") is None

    def test_ssh_lookalike_directory_returns_none(self) -> None:
        # ~/.sshtools (similar prefix) must not match ~/.ssh/**.
        assert _matches_tier1("/fake/home/.sshtools/foo") is None
