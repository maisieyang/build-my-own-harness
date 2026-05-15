"""Tests for ``Skill`` dataclass + ``parse_skill`` — P5c-T1.1a / 1b.

Three surfaces:

1. **Dataclass shape** — frozen, name regex enforced, description
   non-empty.
2. **Parser happy path** — typical skill markdown round-trips into a
   :class:`Skill` instance with body preserved verbatim.
3. **Parser error paths** — every malformed input returns ``None`` and
   emits a warning(NEVER raises). One bad skill must not crash
   bootstrap;the store discovery loop relies on this contract.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openharness.skills.model import Skill, parse_skill


class TestSkillDataclass:
    def test_minimal_construction(self) -> None:
        s = Skill(
            name="x",
            description="d",
            body="hello",
            version=None,
            source_path=Path("/tmp/x.md"),
        )
        assert s.name == "x"
        assert s.description == "d"
        assert s.body == "hello"
        assert s.version is None
        assert s.source_path == Path("/tmp/x.md")

    def test_is_frozen(self) -> None:
        s = Skill(
            name="x",
            description="d",
            body="b",
            version=None,
            source_path=Path("/tmp/x.md"),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.name = "y"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "name",
        ["a", "abc", "react-testing", "snake_case", "PascalCase", "X1", "a-b-c"],
    )
    def test_valid_names_accepted(self, name: str) -> None:
        Skill(
            name=name,
            description="d",
            body="b",
            version=None,
            source_path=Path("/tmp/x.md"),
        )

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "1abc",  # starts with digit
            "_underscore",  # starts with underscore
            "-dash",  # starts with dash
            "with space",
            "with.dot",  # would collide with `Server.Tool` namespace separator
            "with/slash",
            "name$dollar",
        ],
    )
    def test_invalid_names_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="invalid skill name"):
            Skill(
                name=name,
                description="d",
                body="b",
                version=None,
                source_path=Path("/tmp/x.md"),
            )

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValueError, match="description must be non-empty"):
            Skill(
                name="x",
                description="   ",
                body="b",
                version=None,
                source_path=Path("/tmp/x.md"),
            )


class TestParseSkillHappy:
    """P5c-T1.1a — typical frontmatter parses round-trip."""

    def test_minimal_skill(self, tmp_path: Path) -> None:
        path = tmp_path / "react.md"
        path.write_text(
            "---\n"
            "name: react-testing\n"
            "description: When to write React component tests\n"
            "---\n"
            "\n"
            "Write tests that simulate user interactions.\n"
        )
        skill = parse_skill(path)
        assert skill is not None
        assert skill.name == "react-testing"
        assert skill.description == "When to write React component tests"
        assert skill.version is None
        assert "user interactions" in skill.body
        assert skill.source_path == path

    def test_with_version(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: y\nversion: 2\n---\nbody\n")
        skill = parse_skill(path)
        assert skill is not None
        # ``version: 2`` parses as int via YAML, we coerce to str.
        assert skill.version == "2"

    def test_with_string_version(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text('---\nname: x\ndescription: y\nversion: "1.2.3"\n---\nbody\n')
        skill = parse_skill(path)
        assert skill is not None
        assert skill.version == "1.2.3"

    def test_body_preserved_verbatim(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: y\n---\n"
            "Line 1\n\nLine 3 with   spaces\n```\ncode block\n```\n"
        )
        skill = parse_skill(path)
        assert skill is not None
        assert skill.body == ("Line 1\n\nLine 3 with   spaces\n```\ncode block\n```\n")

    def test_extra_frontmatter_fields_ignored(self, tmp_path: Path) -> None:
        # Forward-compat: unknown fields must be tolerated.
        path = tmp_path / "x.md"
        path.write_text(
            "---\nname: x\ndescription: y\nauthor: alice\ntags: [foo, bar]\n---\nbody\n"
        )
        skill = parse_skill(path)
        assert skill is not None
        assert skill.name == "x"


class TestParseSkillErrors:
    """P5c-T1.1b — every malformed input returns ``None``,never raises."""

    def test_file_not_exist(self, tmp_path: Path) -> None:
        assert parse_skill(tmp_path / "missing.md") is None

    def test_no_frontmatter_at_all(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("Just a markdown body, no frontmatter\n")
        assert parse_skill(path) is None

    def test_unclosed_frontmatter(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: y\nbody without closing fence\n")
        assert parse_skill(path) is None

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: : :\nx: [unbalanced\n---\nbody\n")
        assert parse_skill(path) is None

    def test_frontmatter_not_mapping(self, tmp_path: Path) -> None:
        # YAML list at top level → not a dict.
        path = tmp_path / "x.md"
        path.write_text("---\n- alpha\n- beta\n---\nbody\n")
        assert parse_skill(path) is None

    def test_missing_name(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\ndescription: only desc\n---\nbody\n")
        assert parse_skill(path) is None

    def test_missing_description(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\n---\nbody\n")
        assert parse_skill(path) is None

    def test_empty_name(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: ''\ndescription: y\n---\nbody\n")
        assert parse_skill(path) is None

    def test_invalid_name_regex(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: 'with space'\ndescription: y\n---\nbody\n")
        assert parse_skill(path) is None

    def test_empty_description_string(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: '   '\n---\nbody\n")
        assert parse_skill(path) is None

    def test_name_not_a_string(self, tmp_path: Path) -> None:
        # ``name: 1`` would parse as int — must be rejected.
        path = tmp_path / "x.md"
        path.write_text("---\nname: 1\ndescription: y\n---\nbody\n")
        assert parse_skill(path) is None

    def test_open_fence_without_newline(self, tmp_path: Path) -> None:
        # ``---no_newline...`` — opening fence not followed by a newline
        # is malformed; the parser must reject (defense in depth).
        path = tmp_path / "x.md"
        path.write_bytes(b"---name: x\ndescription: y\n---\nbody\n")
        assert parse_skill(path) is None


class TestParseSkillLineEndings:
    """Edge case: Windows CRLF line endings + closing fence at EOF.

    Markdown editors on Windows write ``\\r\\n``. The parser must accept
    both line-ending styles. Files written via ``write_text`` get the
    platform's default; we use ``write_bytes`` to force CRLF for the test.
    """

    def test_crlf_line_endings(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_bytes(b"---\r\nname: x\r\ndescription: y\r\n---\r\nbody line\r\n")
        skill = parse_skill(path)
        assert skill is not None
        assert skill.name == "x"
        assert "body line" in skill.body

    def test_closing_fence_at_eof_no_trailing_newline(self, tmp_path: Path) -> None:
        # Some editors strip the final newline; parser must still find
        # the closing fence and treat body as empty string.
        path = tmp_path / "x.md"
        path.write_bytes(b"---\nname: x\ndescription: y\n---")
        skill = parse_skill(path)
        assert skill is not None
        assert skill.body == ""
