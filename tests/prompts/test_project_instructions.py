"""Contracts for target-project instruction discovery and prompt loading."""

from __future__ import annotations

import os
import stat
import sys
from typing import TYPE_CHECKING

import pytest

from openharness.prompts.claudemd import load_claude_md_prompt
from openharness.prompts.project_instructions import (
    discover_project_instruction_files,
    load_project_instructions,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestDiscoverProjectInstructions:
    def test_loads_agents_and_claude_from_workspace_root(self, tmp_path: Path) -> None:
        agents = tmp_path / "AGENTS.md"
        claude = tmp_path / "CLAUDE.md"
        agents.write_text("agents rules\n", encoding="utf-8")
        claude.write_text("claude rules\n", encoding="utf-8")

        result = discover_project_instruction_files(tmp_path)

        assert result == [agents, claude]

    def test_does_not_read_ancestor_or_user_global_files(self, tmp_path: Path) -> None:
        parent_agents = tmp_path / "AGENTS.md"
        parent_agents.write_text("outside workspace\n", encoding="utf-8")
        workspace = tmp_path / "project"
        workspace.mkdir()
        project_claude = workspace / "CLAUDE.md"
        project_claude.write_text("inside workspace\n", encoding="utf-8")
        global_dir = pytest_home() / ".openharness"
        global_dir.mkdir(parents=True)
        (global_dir / "CLAUDE.md").write_text("user global\n", encoding="utf-8")

        result = discover_project_instruction_files(workspace)

        assert result == [project_claude]

    def test_root_to_working_directory_order_makes_nested_scope_later(self, tmp_path: Path) -> None:
        root_agents = tmp_path / "AGENTS.md"
        root_agents.write_text("root\n", encoding="utf-8")
        nested = tmp_path / "packages" / "api"
        nested.mkdir(parents=True)
        nested_agents = nested / "AGENTS.md"
        nested_agents.write_text("nested\n", encoding="utf-8")

        result = discover_project_instruction_files(
            tmp_path,
            working_directory=nested,
        )

        assert result == [root_agents, nested_agents]

    def test_supports_claude_compatibility_locations(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        rules_dir = claude_dir / "rules"
        rules_dir.mkdir(parents=True)
        alternate = claude_dir / "CLAUDE.md"
        alternate.write_text("alternate\n", encoding="utf-8")
        alpha = rules_dir / "alpha.md"
        zeta = rules_dir / "zeta.md"
        zeta.write_text("zeta\n", encoding="utf-8")
        alpha.write_text("alpha\n", encoding="utf-8")

        result = discover_project_instruction_files(tmp_path)

        assert result == [alternate, alpha, zeta]

    def test_rejects_working_directory_outside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        workspace.mkdir()

        with pytest.raises(ValueError, match="inside workspace_root"):
            discover_project_instruction_files(
                workspace,
                working_directory=tmp_path,
            )

    def test_symlink_target_outside_workspace_is_not_loaded(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        workspace.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        linked = workspace / "AGENTS.md"
        try:
            os.symlink(outside, linked)
        except OSError:
            pytest.skip("symlink creation not supported")

        assert discover_project_instruction_files(workspace) == []


class TestLoadProjectInstructions:
    def test_returns_none_when_workspace_has_no_instruction_files(self, tmp_path: Path) -> None:
        assert load_project_instructions(tmp_path) is None

    def test_renders_labeled_project_instruction_section(self, tmp_path: Path) -> None:
        agents = tmp_path / "AGENTS.md"
        claude = tmp_path / "CLAUDE.md"
        agents.write_text("agents rules\n", encoding="utf-8")
        claude.write_text("claude rules\n", encoding="utf-8")

        result = load_project_instructions(tmp_path)

        assert result is not None
        assert result.startswith("## Project Instructions\n\n")
        assert f"### {agents}" in result
        assert f"### {claude}" in result
        assert "```md\nagents rules\n```" in result
        assert "```md\nclaude rules\n```" in result

    def test_truncates_each_file_independently(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("A" * 2_000, encoding="utf-8")

        result = load_project_instructions(tmp_path, max_chars_per_file=1_000)

        assert result is not None
        assert "A" * 1_000 in result
        assert "A" * 1_500 not in result
        assert "[truncated]" in result

    def test_unreadable_file_is_skipped_while_siblings_load(self, tmp_path: Path) -> None:
        if sys.platform.startswith("win"):
            pytest.skip("POSIX chmod semantics required")
        bad = tmp_path / "AGENTS.md"
        good = tmp_path / "CLAUDE.md"
        bad.write_text("unreadable\n", encoding="utf-8")
        good.write_text("readable\n", encoding="utf-8")
        os.chmod(bad, 0)
        try:
            result = load_project_instructions(tmp_path)
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)

        assert result is not None
        assert "```md\nreadable\n```" in result
        assert "```md\nunreadable\n```" not in result

    def test_legacy_claude_loader_does_not_include_agents(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("agents\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("claude\n", encoding="utf-8")

        result = load_claude_md_prompt(tmp_path)

        assert result is not None
        assert "```md\nclaude\n```" in result
        assert "```md\nagents\n```" not in result


def pytest_home() -> Path:
    from pathlib import Path

    return Path.home()
