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
    _matches_irreversible_git_action,
    _matches_tier1,
    _matches_tier2,
)

if TYPE_CHECKING:
    from pathlib import Path

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


class TestMatchesTier2:
    """``_matches_tier2(path, patterns, cwd)`` — user-config patterns with
    ``.gitignore``-style cwd-relative semantics. P3-T3.3b.

    Tests use the pytest ``tmp_path`` fixture as cwd; targets are constructed
    under it so ``Path.relative_to`` succeeds without needing real files.
    """

    def test_empty_patterns_returns_none(self, tmp_path: Path) -> None:
        # No user rules ⇒ no match.
        assert _matches_tier2("/anywhere", (), tmp_path) is None

    def test_matches_relative_pattern_under_cwd(self, tmp_path: Path) -> None:
        # User writes ``secrets/**``; expects cwd-local matching.
        target = tmp_path / "secrets" / "keys.txt"
        patterns = ("secrets/**", "*.env")
        assert _matches_tier2(str(target), patterns, tmp_path) == "secrets/**"

    def test_matches_env_glob_relative(self, tmp_path: Path) -> None:
        target = tmp_path / ".env"
        assert _matches_tier2(str(target), ("*.env",), tmp_path) == "*.env"

    def test_first_match_wins(self, tmp_path: Path) -> None:
        # Multiple patterns hit ⇒ first wins (deny-first, returns first match).
        target = tmp_path / "secrets" / "keys.txt"
        patterns = ("secrets/**", "secrets/keys.*")
        assert _matches_tier2(str(target), patterns, tmp_path) == "secrets/**"

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        target = tmp_path / "README.md"
        patterns = ("secrets/**", "*.env")
        assert _matches_tier2(str(target), patterns, tmp_path) is None

    def test_absolute_pattern_matches_directly(self, tmp_path: Path) -> None:
        # Absolute pattern is NOT cwd-translated — user explicitly anchored it.
        patterns = ("/etc/secret_config",)
        assert _matches_tier2("/etc/secret_config", patterns, tmp_path) == "/etc/secret_config"

    def test_path_outside_cwd_skips_relative_patterns(self, tmp_path: Path) -> None:
        # path 在 cwd 外 ⇒ 相对 pattern 算不出 rel,不命中。
        # 绝对 pattern 仍然 work(在 absolute_pattern test 里覆盖了)。
        patterns = ("secrets/**",)
        assert _matches_tier2("/tmp/other/secrets/x", patterns, tmp_path) is None


class TestInsideProjectRoot:
    """``_inside_project_root(path, cwd)`` — Tier 3 boundary check helper.

    Mirrors the logic currently in ``edit.py`` (P3-T3.3f will delete that
    copy and the framework's Checker becomes the single source).
    """

    def test_path_directly_under_cwd_is_inside(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _inside_project_root

        assert _inside_project_root(tmp_path / "foo.txt", tmp_path) is True

    def test_path_nested_under_cwd_is_inside(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _inside_project_root

        assert _inside_project_root(tmp_path / "src" / "deep" / "main.py", tmp_path) is True

    def test_cwd_itself_is_inside(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _inside_project_root

        assert _inside_project_root(tmp_path, tmp_path) is True

    def test_path_outside_cwd_is_outside(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _inside_project_root

        assert _inside_project_root("/etc/passwd", tmp_path) is False

    def test_path_with_traversal_climbing_out(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _inside_project_root

        # `tmp_path/../../etc/foo` resolves outside cwd.
        sneaky = tmp_path / ".." / ".." / "etc" / "passwd"
        assert _inside_project_root(sneaky, tmp_path) is False

    def test_path_with_traversal_staying_in(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _inside_project_root

        # `tmp_path/sub/../foo` resolves to `tmp_path/foo` → inside.
        roundtrip = tmp_path / "sub" / ".." / "foo.txt"
        assert _inside_project_root(roundtrip, tmp_path) is True

    def test_accepts_str_or_path(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _inside_project_root

        target_str = str(tmp_path / "foo.txt")
        assert _inside_project_root(target_str, tmp_path) is True


class TestMatchesTier3:
    """``_matches_tier3(is_read_only, path, cwd)`` — mode-based check.

    Per Three-Axis Micro-Decision C:read-only tools skip Tier 3
    entirely; strict tools (is_read_only=False) require the path to be
    inside the project root.

    Per Three-Axis Micro-Decision G:Tier 3 matches map to ASK in the
    final DecisionResult (not DENY) — but that mapping happens in the
    Checker layer (3e). This sub-unit returns a reason string when the
    boundary is crossed, ``None`` otherwise.
    """

    def test_read_only_tool_skips_tier3(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _matches_tier3

        # Read on a path outside cwd is FINE (Tier 3 lax for read-only).
        assert _matches_tier3(True, "/etc/passwd", tmp_path) is None

    def test_strict_tool_path_inside_cwd_passes(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _matches_tier3

        target = tmp_path / "src" / "main.py"
        assert _matches_tier3(False, str(target), tmp_path) is None

    def test_strict_tool_path_outside_cwd_returns_reason(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _matches_tier3

        # Write/Edit/Bash with a path outside cwd → reason string explains it.
        reason = _matches_tier3(False, "/tmp/elsewhere.txt", tmp_path)
        assert reason is not None
        assert "outside project root" in reason
        assert str(tmp_path) in reason  # cwd 显示在 reason 里让 LLM 理解

    def test_no_path_returns_none_even_for_strict_tool(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import _matches_tier3

        # Bash 不带 path 字段(args 没 path) → Tier 3 跳过,Bash 的安全
        # 完全在 Tier 1/2/Hook 那一层。
        assert _matches_tier3(False, None, tmp_path) is None

    def test_strict_tool_path_inside_memory_dir_passes(self, tmp_path: Path) -> None:
        """P16-T5 dogfood fix: writes targeting the per-project memory
        dir bypass the "outside cwd" rejection. Required so the main
        LLM can execute the Phase 16 D36.10/D36.11 memory write
        contract — its target dir is at
        ``~/.openharness/memory/<basename>-<sha1>/``, outside cwd by
        D28.1 design.
        """
        from openharness.memory.paths import get_project_memory_dir
        from openharness.permissions.tier_based import _matches_tier3

        memory_dir = get_project_memory_dir(tmp_path)
        memory_target = memory_dir / "my-feedback.md"
        assert _matches_tier3(False, str(memory_target), tmp_path) is None

    def test_strict_tool_memory_md_index_passes(self, tmp_path: Path) -> None:
        """Same as above but for the MEMORY.md index file specifically —
        this is the file the T5 dogfood saw rejected.
        """
        from openharness.memory.paths import get_project_memory_dir
        from openharness.permissions.tier_based import _matches_tier3

        memory_dir = get_project_memory_dir(tmp_path)
        memory_md = memory_dir / "MEMORY.md"
        assert _matches_tier3(False, str(memory_md), tmp_path) is None

    def test_strict_tool_other_outside_paths_still_ask(self, tmp_path: Path) -> None:
        """The memory-dir exception is *narrow* — other outside-cwd
        paths still surface the reason string. Guards against the
        exception turning into a general Tier 3 relaxation.
        """
        from openharness.permissions.tier_based import _matches_tier3

        # /tmp/elsewhere is neither cwd nor memory_dir → reason returned.
        reason = _matches_tier3(False, "/tmp/elsewhere.txt", tmp_path)
        assert reason is not None
        assert "outside project root" in reason


class TestInsideProjectMemoryDir:
    """``_inside_project_memory_dir(path, cwd)`` — Phase 16 T5 dogfood
    fix helper. Resolves ``cwd`` to its per-project memory dir via
    :func:`openharness.memory.paths.get_project_memory_dir` and checks
    whether ``path`` is under it.
    """

    def test_path_inside_memory_dir_is_true(self, tmp_path: Path) -> None:
        from openharness.memory.paths import get_project_memory_dir
        from openharness.permissions.tier_based import (
            _inside_project_memory_dir,
        )

        memory_dir = get_project_memory_dir(tmp_path)
        target = memory_dir / "user-role.md"
        assert _inside_project_memory_dir(target, tmp_path) is True

    def test_path_nested_inside_memory_dir_is_true(self, tmp_path: Path) -> None:
        from openharness.memory.paths import get_project_memory_dir
        from openharness.permissions.tier_based import (
            _inside_project_memory_dir,
        )

        memory_dir = get_project_memory_dir(tmp_path)
        # team/ subdir per Phase 11 D29.10
        target = memory_dir / "team" / "shared-fact.md"
        assert _inside_project_memory_dir(target, tmp_path) is True

    def test_path_outside_memory_dir_is_false(self, tmp_path: Path) -> None:
        from openharness.permissions.tier_based import (
            _inside_project_memory_dir,
        )

        assert _inside_project_memory_dir("/etc/passwd", tmp_path) is False

    def test_path_inside_cwd_but_not_memory_dir_is_false(self, tmp_path: Path) -> None:
        """cwd-internal paths aren't memory_dir paths — the two
        domains are disjoint by D28.1.
        """
        from openharness.permissions.tier_based import (
            _inside_project_memory_dir,
        )

        cwd_internal = tmp_path / "src" / "main.py"
        assert _inside_project_memory_dir(cwd_internal, tmp_path) is False

    def test_traversal_climbing_out_of_memory_dir_is_false(self, tmp_path: Path) -> None:
        from openharness.memory.paths import get_project_memory_dir
        from openharness.permissions.tier_based import (
            _inside_project_memory_dir,
        )

        memory_dir = get_project_memory_dir(tmp_path)
        sneaky = memory_dir / ".." / ".." / "etc" / "passwd"
        assert _inside_project_memory_dir(sneaky, tmp_path) is False


class TestMatchesIrreversibleGitAction:
    """L8 — ``_matches_irreversible_git_action(command)`` is a hardcoded,
    unconditional red line for Bash calls that would irreversibly change
    shared git state (commit/push). Real argv parsing (shell-metachar split
    + shlex + first-two-token check), NOT string prefix/substring matching
    — the existing Bash matchers (``_matches_bash_deny``, ``rules.py``'s
    ``_bash_matches``) are documented as bypassable and are NOT the model
    for this check.
    """

    def test_plain_git_commit_matches(self) -> None:
        assert _matches_irreversible_git_action("git commit -m x") is not None

    def test_plain_git_push_matches(self) -> None:
        assert _matches_irreversible_git_action("git push") is not None

    def test_git_push_force_matches(self) -> None:
        assert _matches_irreversible_git_action("git push --force origin main") is not None

    def test_unrelated_git_command_does_not_match(self) -> None:
        assert _matches_irreversible_git_action("git status") is None

    def test_lookalike_prefix_does_not_match(self) -> None:
        """A command whose first token merely STARTS WITH 'git' as a
        substring, or whose second token isn't exactly 'commit'/'push',
        must not false-positive under naive prefix/substring matching."""
        assert _matches_irreversible_git_action("git commit-msg-hook-checker") is None
        assert _matches_irreversible_git_action("git-wrapper commit") is None

    def test_chained_after_shell_operator_still_matches(self) -> None:
        """Real argv parsing (splitting on shell metacharacters first)
        catches chained invocations that a naive command.startswith(...)
        check would miss."""
        assert _matches_irreversible_git_action("foo && git commit -m x") is not None
        assert _matches_irreversible_git_action("echo hi; git push") is not None
        assert _matches_irreversible_git_action("foo || git push origin main") is not None

    def test_string_literal_containing_git_commit_does_not_match(self) -> None:
        """Honest limitation, deliberately locked in: this is a practical
        tripwire, not a sandbox. A command that merely echoes the string
        'git commit' (never executes it) is not something argv-level
        first-two-token parsing can or should try to detect — matching
        rules.py's own documented honesty about Bash-layer limits."""
        assert _matches_irreversible_git_action("echo 'git commit'") is None

    def test_global_git_options_before_subcommand_still_match(self) -> None:
        """Review finding: ordinary (non-adversarial) invocations with a
        git global option before the subcommand must still be caught —
        this is common usage, not evasion."""
        assert _matches_irreversible_git_action('git -C /path/to/repo commit -m "fix"') is not None
        assert _matches_irreversible_git_action("git --no-pager push") is not None
        assert _matches_irreversible_git_action("git -c user.name=x commit -m y") is not None
        assert _matches_irreversible_git_action("git --git-dir=/repo/.git commit -m x") is not None

    def test_newline_separated_commands_still_match(self) -> None:
        """Review finding: a literal newline is a real shell command
        separator (e.g. heredocs, multi-line scripts), not just an
        adversarial trick — must split on it like the other operators."""
        assert _matches_irreversible_git_action("echo done\ngit commit -m x") is not None

    def test_dry_run_is_not_denied(self) -> None:
        """Review finding: --dry-run doesn't mutate anything, so denying it
        identically to a real commit/push is an unnecessary false positive.
        NOTE: only the long-form --dry-run is special-cased -- git commit's
        `-n` means --no-verify (NOT dry-run), so it must NOT be treated as
        a dry-run alias here."""
        assert _matches_irreversible_git_action("git commit --dry-run -m x") is None
        assert _matches_irreversible_git_action("git push --dry-run") is None
        # -n on commit is --no-verify, not dry-run -- must still be denied.
        assert _matches_irreversible_git_action("git commit -n -m x") is not None
