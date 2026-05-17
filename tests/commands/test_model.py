"""Tests for ``Command`` dataclass + ``parse_command`` — P5b-T1.1a / 1b.

Three surfaces (mirrors ``tests/skills/test_model.py`` structure):

1. **Dataclass shape** — frozen, name regex enforced, description non-empty.
2. **Parser happy path** — typical command markdown round-trips into
   a :class:`Command` instance with body preserved verbatim.
3. **Parser error paths** — every malformed input returns ``None`` and
   emits a warning(NEVER raises). Bootstrap discipline.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from openharness.commands.model import Command, parse_command

if TYPE_CHECKING:
    from pathlib import Path


class TestCommandDataclass:
    def test_minimal_construction(self, tmp_path: Path) -> None:
        c = Command(
            name="review",
            description="Review pending changes",
            body="Please review:\n\n{args}\n",
            source_path=tmp_path / "review.md",
        )
        assert c.name == "review"
        assert c.body == "Please review:\n\n{args}\n"

    def test_is_frozen(self, tmp_path: Path) -> None:
        c = Command(name="x", description="d", body="b", source_path=tmp_path / "x.md")
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.name = "y"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "name",
        ["a", "review", "plan", "security-review", "snake_case", "X1"],
    )
    def test_valid_names_accepted(self, name: str, tmp_path: Path) -> None:
        Command(name=name, description="d", body="b", source_path=tmp_path / "x.md")

    @pytest.mark.parametrize(
        "name",
        ["", "1abc", "_private", "-dash", "with space", "with.dot", "with/slash"],
    )
    def test_invalid_names_rejected(self, name: str, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid command name"):
            Command(name=name, description="d", body="b", source_path=tmp_path / "x.md")

    def test_empty_description_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="description must be non-empty"):
            Command(name="x", description="   ", body="b", source_path=tmp_path / "x.md")


class TestParseCommandHappy:
    def test_minimal_command(self, tmp_path: Path) -> None:
        path = tmp_path / "review.md"
        path.write_text(
            "---\n"
            "name: review\n"
            "description: Review pending changes\n"
            "---\n"
            "Please review the following:\n\n{args}\n",
            encoding="utf-8",
        )
        cmd = parse_command(path)
        assert cmd is not None
        assert cmd.name == "review"
        assert cmd.description == "Review pending changes"
        assert "{args}" in cmd.body
        assert cmd.source_path == path

    def test_body_preserved_verbatim(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: y\n---\nLine 1\n\nLine 3 with   spaces\n",
            encoding="utf-8",
        )
        cmd = parse_command(path)
        assert cmd is not None
        assert cmd.body == "Line 1\n\nLine 3 with   spaces\n"

    def test_extra_frontmatter_fields_ignored(self, tmp_path: Path) -> None:
        # P5d-T4: ``mode:`` is now consumed (see TestCommandModeField).
        # ``hooks:`` remains forward-compat-tolerated.
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: y\nhooks: [audit_log]\n---\nbody\n",
            encoding="utf-8",
        )
        cmd = parse_command(path)
        assert cmd is not None
        assert cmd.name == "x"


class TestParseCommandErrors:
    """Every malformed input returns ``None``,never raises."""

    def test_file_not_exist(self, tmp_path: Path) -> None:
        assert parse_command(tmp_path / "missing.md") is None

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("Just a body, no frontmatter\n")
        assert parse_command(path) is None

    def test_unclosed_frontmatter(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: y\nbody without closing\n")
        assert parse_command(path) is None

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: : :\n---\nbody\n")
        assert parse_command(path) is None

    def test_frontmatter_not_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\n- alpha\n---\nbody\n")
        assert parse_command(path) is None

    def test_missing_name(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\ndescription: only desc\n---\nbody\n")
        assert parse_command(path) is None

    def test_missing_description(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\n---\nbody\n")
        assert parse_command(path) is None

    def test_invalid_name_regex(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: 'with space'\ndescription: y\n---\nbody\n")
        assert parse_command(path) is None

    def test_empty_description_string(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: '   '\n---\nbody\n")
        assert parse_command(path) is None

    def test_name_not_a_string(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: 1\ndescription: y\n---\nbody\n")
        assert parse_command(path) is None

    def test_closing_fence_at_eof_no_trailing_newline(self, tmp_path: Path) -> None:
        # Some editors strip the final newline; parser still finds the
        # closing fence and yields an empty body.
        path = tmp_path / "x.md"
        path.write_bytes(b"---\nname: x\ndescription: y\n---")
        cmd = parse_command(path)
        assert cmd is not None
        assert cmd.body == ""


class TestCommandModeField:
    """P5d-T4: ``Command.mode`` optional reference to a ModeBundle."""

    def test_mode_defaults_to_none(self, tmp_path: Path) -> None:
        c = Command(name="x", description="d", body="b", source_path=tmp_path / "x.md")
        assert c.mode is None

    def test_mode_accepts_valid_identifier(self, tmp_path: Path) -> None:
        c = Command(
            name="review",
            description="d",
            body="b",
            source_path=tmp_path / "x.md",
            mode="code-review",
        )
        assert c.mode == "code-review"

    @pytest.mark.parametrize(
        "mode",
        ["", "1abc", "_priv", "-dash", "has space", "has.dot", "has/slash"],
    )
    def test_invalid_mode_identifier_rejected(self, mode: str, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid mode"):
            Command(
                name="x",
                description="d",
                body="b",
                source_path=tmp_path / "x.md",
                mode=mode,
            )

    def test_parse_command_reads_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: d\nmode: code-review\n---\nbody\n",
            encoding="utf-8",
        )
        cmd = parse_command(path)
        assert cmd is not None
        assert cmd.mode == "code-review"

    def test_parse_command_no_mode_field(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
        cmd = parse_command(path)
        assert cmd is not None
        assert cmd.mode is None

    def test_parse_command_mode_wrong_type_drops(self, tmp_path: Path) -> None:
        # Non-string ``mode:`` → warn + drop (cmd still loads with mode=None).
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: d\nmode: [list, not, string]\n---\nbody\n",
            encoding="utf-8",
        )
        cmd = parse_command(path)
        assert cmd is not None
        assert cmd.mode is None

    def test_parse_command_invalid_mode_identifier_returns_none(self, tmp_path: Path) -> None:
        # Invalid identifier (e.g. space) → ValueError from __post_init__
        # → parse_command returns None.
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: d\nmode: 'has space'\n---\nbody\n",
            encoding="utf-8",
        )
        assert parse_command(path) is None
