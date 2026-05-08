"""Tests for the Write tool — P2-T3 sub-unit 3b.

Coverage:
- Happy path: create new file in cwd
- Overwrite existing file
- D9.1: relative path resolves against cwd
- D9.2 scope guard:
  - Absolute path outside cwd → is_error
  - Relative path with ``..`` that escapes cwd → is_error
- Parent directory missing → is_error
- Cannot overwrite directory → is_error
- Multibyte UTF-8: bytes_written counts encoded bytes, not characters
- Metadata: bytes_written + path populated
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openharness.tools.base import ToolExecutionContext
from openharness.tools.write import Write, WriteInput

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tool() -> Write:
    return Write()


def _ctx(cwd: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=cwd)


class TestWriteHappyPath:
    async def test_creates_new_file_with_bytes_count(self, tool: Write, tmp_path: Path) -> None:
        target = tmp_path / "fresh.txt"
        result = await tool.execute(
            WriteInput(path=str(target), content="hello"),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert target.read_text() == "hello"
        assert result.metadata["bytes_written"] == 5

    async def test_overwrites_existing_file(self, tool: Write, tmp_path: Path) -> None:
        target = tmp_path / "existing.txt"
        target.write_text("old content")
        result = await tool.execute(
            WriteInput(path=str(target), content="new"),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert target.read_text() == "new"
        assert result.metadata["bytes_written"] == 3

    async def test_relative_path_resolves_against_cwd(self, tool: Write, tmp_path: Path) -> None:
        result = await tool.execute(
            WriteInput(path="under_cwd.txt", content="x"),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert (tmp_path / "under_cwd.txt").read_text() == "x"


class TestWriteScopeGuard:
    async def test_absolute_path_outside_cwd_rejected(self, tool: Write, tmp_path: Path) -> None:
        # tmp_path lives under /tmp/...; /tmp itself is not inside it.
        result = await tool.execute(
            WriteInput(path="/tmp/escape.txt", content="x"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "outside project root" in result.output

    async def test_relative_dotdot_escape_rejected(self, tool: Write, tmp_path: Path) -> None:
        # cwd is tmp_path/inner; "../escape.txt" lands in tmp_path itself,
        # which is outside cwd.
        inner = tmp_path / "inner"
        inner.mkdir()
        result = await tool.execute(
            WriteInput(path="../escape.txt", content="x"),
            _ctx(inner),
        )
        assert result.is_error is True
        assert "outside project root" in result.output


class TestWriteFailures:
    async def test_missing_parent_directory_returns_error(
        self, tool: Write, tmp_path: Path
    ) -> None:
        result = await tool.execute(
            WriteInput(path="ghost_dir/file.txt", content="x"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "parent directory does not exist" in result.output

    async def test_cannot_overwrite_directory(self, tool: Write, tmp_path: Path) -> None:
        # Path that points at an existing directory, not a file.
        existing_dir = tmp_path / "adir"
        existing_dir.mkdir()
        result = await tool.execute(
            WriteInput(path="adir", content="x"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "cannot overwrite directory" in result.output


class TestWriteEncoding:
    async def test_multibyte_utf8_bytes_count_matches_encoded_length(
        self, tool: Write, tmp_path: Path
    ) -> None:
        # "你好" is 6 bytes in UTF-8 (3 bytes per character) but 2 characters.
        target = tmp_path / "cn.txt"
        result = await tool.execute(
            WriteInput(path=str(target), content="你好"),
            _ctx(tmp_path),
        )
        assert result.metadata["bytes_written"] == 6
        assert target.read_bytes() == "你好".encode()
