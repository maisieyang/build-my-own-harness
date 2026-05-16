"""Tests for ``Bundle`` dataclass + ``parse_bundle`` — P5d-T1.

Three surfaces (mirrors ``tests/commands/test_model.py`` shape):

1. **Dataclass shape** — frozen, name regex, description non-empty
2. **Parser happy path** — minimal bundle, full 4-layer bundle, each
   optional field absent
3. **Parser error paths** — every malformed shape returns ``None`` +
   warning (never raises). Bootstrap discipline.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from openharness.bundles.model import Bundle, parse_bundle

if TYPE_CHECKING:
    from pathlib import Path


class TestBundleDataclass:
    def test_minimal_construction(self, tmp_path: Path) -> None:
        b = Bundle(
            name="x",
            description="d",
            system_prompt=None,
            tools_whitelist=None,
            deny_paths=(),
            hook_names=(),
            source_path=tmp_path / "x.md",
        )
        assert b.name == "x"
        assert b.description == "d"
        assert b.system_prompt is None
        assert b.tools_whitelist is None
        assert b.deny_paths == ()
        assert b.hook_names == ()

    def test_is_frozen(self, tmp_path: Path) -> None:
        b = Bundle(
            name="x",
            description="d",
            system_prompt=None,
            tools_whitelist=None,
            deny_paths=(),
            hook_names=(),
            source_path=tmp_path / "x.md",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            b.name = "y"  # type: ignore[misc]

    @pytest.mark.parametrize("name", ["a", "review", "code-review", "security_audit", "X1"])
    def test_valid_names_accepted(self, name: str, tmp_path: Path) -> None:
        Bundle(
            name=name,
            description="d",
            system_prompt=None,
            tools_whitelist=None,
            deny_paths=(),
            hook_names=(),
            source_path=tmp_path / "x.md",
        )

    @pytest.mark.parametrize("name", ["", "1abc", "_underscore", "-dash", "with space", "with.dot"])
    def test_invalid_names_rejected(self, name: str, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid bundle name"):
            Bundle(
                name=name,
                description="d",
                system_prompt=None,
                tools_whitelist=None,
                deny_paths=(),
                hook_names=(),
                source_path=tmp_path / "x.md",
            )

    def test_empty_description_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="description must be non-empty"):
            Bundle(
                name="x",
                description="   ",
                system_prompt=None,
                tools_whitelist=None,
                deny_paths=(),
                hook_names=(),
                source_path=tmp_path / "x.md",
            )


class TestParseBundleHappy:
    def test_minimal_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: minimal bundle\n---\n",
            encoding="utf-8",
        )
        b = parse_bundle(path)
        assert b is not None
        assert b.name == "x"
        assert b.description == "minimal bundle"
        # All override fields default to "absent" semantics.
        assert b.system_prompt is None
        assert b.tools_whitelist is None
        assert b.deny_paths == ()
        assert b.hook_names == ()

    def test_full_4layer_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "code-review.md"
        path.write_text(
            "---\n"
            "name: code-review\n"
            "description: Read-only code review mode\n"
            "system_prompt: |\n"
            "  You are a code reviewer.\n"
            "tools:\n"
            "  whitelist: [Read, Grep]\n"
            "deny_paths:\n"
            "  - secrets/**\n"
            '  - "*.env"\n'
            "hooks:\n"
            "  - audit_log\n"
            "  - deny_writes\n"
            "---\n",
            encoding="utf-8",
        )
        b = parse_bundle(path)
        assert b is not None
        assert b.system_prompt is not None
        assert "code reviewer" in b.system_prompt
        assert b.tools_whitelist == ("Read", "Grep")
        assert b.deny_paths == ("secrets/**", "*.env")
        assert b.hook_names == ("audit_log", "deny_writes")

    def test_only_system_prompt(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: d\nsystem_prompt: 'Be helpful'\n---\n",
            encoding="utf-8",
        )
        b = parse_bundle(path)
        assert b is not None
        assert b.system_prompt == "Be helpful"
        assert b.tools_whitelist is None
        assert b.deny_paths == ()

    def test_only_tools_whitelist(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: d\ntools:\n  whitelist: [Read]\n---\n",
            encoding="utf-8",
        )
        b = parse_bundle(path)
        assert b is not None
        assert b.tools_whitelist == ("Read",)


class TestParseBundleErrors:
    """Every malformed input returns ``None``, never raises."""

    def test_file_not_exist(self, tmp_path: Path) -> None:
        assert parse_bundle(tmp_path / "missing.md") is None

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("just markdown\n")
        assert parse_bundle(path) is None

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: : :\n---\n")
        assert parse_bundle(path) is None

    def test_frontmatter_not_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\n- alpha\n---\n")
        assert parse_bundle(path) is None

    def test_missing_name(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\ndescription: y\n---\n")
        assert parse_bundle(path) is None

    def test_missing_description(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\n---\n")
        assert parse_bundle(path) is None

    def test_invalid_name_regex(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: 'with space'\ndescription: y\n---\n")
        assert parse_bundle(path) is None

    def test_system_prompt_not_string(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: y\nsystem_prompt: 42\n---\n", encoding="utf-8")
        assert parse_bundle(path) is None

    def test_tools_block_not_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: y\ntools: 42\n---\n", encoding="utf-8")
        assert parse_bundle(path) is None

    def test_tools_whitelist_not_list(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: y\ntools:\n  whitelist: bare-string\n---\n",
            encoding="utf-8",
        )
        assert parse_bundle(path) is None

    def test_tools_whitelist_non_string_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: y\ntools:\n  whitelist: [Read, 42]\n---\n",
            encoding="utf-8",
        )
        assert parse_bundle(path) is None

    def test_deny_paths_not_list(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: y\ndeny_paths: bare\n---\n",
            encoding="utf-8",
        )
        assert parse_bundle(path) is None

    def test_hooks_not_list(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: y\nhooks: bare\n---\n", encoding="utf-8")
        assert parse_bundle(path) is None
