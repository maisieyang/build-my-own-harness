"""Tests for the Edit tool — P2-T3 sub-unit 3c.

Coverage:
- Happy path: single occurrence replaced (default replace_all=False)
- ``replace_all=True`` replaces every occurrence
- ``old_str`` not found → is_error
- Multi-line ``old_str`` supported
- Empty ``old_str`` rejected at Pydantic validation
- Path scope guard (D9.2): outside-cwd path → is_error
- File missing → is_error
- File not valid UTF-8 → is_error
- Metadata: replacements + path populated
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from openharness.tools.base import ToolExecutionContext
from openharness.tools.edit import Edit, EditInput

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tool() -> Edit:
    return Edit()


def _ctx(cwd: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=cwd)


class TestEditHappyPath:
    async def test_replaces_first_occurrence_by_default(self, tool: Edit, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("foo bar foo")
        result = await tool.execute(
            EditInput(path=str(target), old_str="foo", new_str="qux"),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert target.read_text() == "qux bar foo"
        assert result.metadata["replacements"] == 1

    async def test_replace_all_true_replaces_every_occurrence(
        self, tool: Edit, tmp_path: Path
    ) -> None:
        target = tmp_path / "f.txt"
        target.write_text("foo bar foo baz foo")
        result = await tool.execute(
            EditInput(
                path=str(target),
                old_str="foo",
                new_str="X",
                replace_all=True,
            ),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert target.read_text() == "X bar X baz X"
        assert result.metadata["replacements"] == 3

    async def test_multiline_old_str_replaced(self, tool: Edit, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("alpha\nbeta\ngamma\n")
        result = await tool.execute(
            EditInput(
                path=str(target),
                old_str="alpha\nbeta",
                new_str="ALPHA-BETA",
            ),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert target.read_text() == "ALPHA-BETA\ngamma\n"

    async def test_new_str_can_be_empty_for_deletion(self, tool: Edit, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("delete-me-here remaining")
        result = await tool.execute(
            EditInput(path=str(target), old_str="delete-me-here ", new_str=""),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert target.read_text() == "remaining"


class TestEditFailures:
    async def test_old_str_not_found_returns_error(self, tool: Edit, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("hello world")
        result = await tool.execute(
            EditInput(path=str(target), old_str="ghost", new_str="x"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "old_str not found" in result.output
        # File untouched on miss.
        assert target.read_text() == "hello world"

    async def test_file_missing_returns_error(self, tool: Edit, tmp_path: Path) -> None:
        result = await tool.execute(
            EditInput(path="ghost.txt", old_str="x", new_str="y"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "file not found" in result.output

    async def test_path_outside_cwd_rejected(self, tool: Edit, tmp_path: Path) -> None:
        inner = tmp_path / "inner"
        inner.mkdir()
        # Create a file in tmp_path itself (parent of cwd).
        outside = tmp_path / "outside.txt"
        outside.write_text("anything")
        result = await tool.execute(
            EditInput(path="../outside.txt", old_str="any", new_str="X"),
            _ctx(inner),
        )
        assert result.is_error is True
        assert "outside project root" in result.output
        # File untouched.
        assert outside.read_text() == "anything"

    async def test_non_utf8_file_returns_error(self, tool: Edit, tmp_path: Path) -> None:
        target = tmp_path / "binary.bin"
        # 0xFF 0xFE 0xFD is not valid UTF-8.
        target.write_bytes(b"\xff\xfe\xfd")
        result = await tool.execute(
            EditInput(path=str(target), old_str="x", new_str="y"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "not valid UTF-8" in result.output


class TestEditValidation:
    def test_empty_old_str_rejected_at_input(self) -> None:
        # Pydantic's min_length=1 catches this before ever reaching execute().
        with pytest.raises(ValidationError):
            EditInput(path="x", old_str="", new_str="y")
