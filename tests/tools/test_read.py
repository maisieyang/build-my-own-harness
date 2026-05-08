"""Tests for the Read tool — P2-T3 sub-unit 3a.

Coverage:
- Happy path: regular file → contents
- D9.1 path resolution: relative resolves against cwd; absolute used as-is
- Failure paths: nonexistent / directory / file >10 MiB (size checked, not
  actually read)
- Sentinels: empty file → "(empty)"
- Offset/limit: 1-indexed line slicing
- Encoding: invalid bytes replaced (errors="replace") rather than raising
- Metadata: size_bytes populated
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from openharness.tools.base import ToolExecutionContext
from openharness.tools.read import Read, ReadInput

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tool() -> Read:
    return Read()


def _ctx(cwd: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=cwd)


class TestReadHappyPath:
    async def test_reads_regular_file_contents(self, tool: Read, tmp_path: Path) -> None:
        target = tmp_path / "hello.txt"
        target.write_text("hello world\n")
        result = await tool.execute(ReadInput(path=str(target)), _ctx(tmp_path))
        assert result.is_error is False
        assert result.output == "hello world\n"
        assert result.metadata["size_bytes"] == 12

    async def test_relative_path_resolves_against_cwd(self, tool: Read, tmp_path: Path) -> None:
        # D9.1: "package.json" should resolve to tmp_path/package.json
        (tmp_path / "package.json").write_text('{"name":"x"}')
        result = await tool.execute(ReadInput(path="package.json"), _ctx(tmp_path))
        assert result.output == '{"name":"x"}'

    async def test_absolute_path_used_as_is(self, tool: Read, tmp_path: Path) -> None:
        # D9.1: absolute path bypasses cwd join entirely.
        external = tmp_path / "outside.txt"
        external.write_text("absolute")
        unrelated_cwd = tmp_path / "subdir"
        unrelated_cwd.mkdir()
        result = await tool.execute(
            ReadInput(path=str(external)),
            _ctx(unrelated_cwd),
        )
        assert result.output == "absolute"


class TestReadFailures:
    async def test_nonexistent_file_returns_error(self, tool: Read, tmp_path: Path) -> None:
        result = await tool.execute(
            ReadInput(path="ghost.txt"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "file not found" in result.output

    async def test_directory_path_returns_error(self, tool: Read, tmp_path: Path) -> None:
        subdir = tmp_path / "adir"
        subdir.mkdir()
        result = await tool.execute(
            ReadInput(path=str(subdir)),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "not a regular file" in result.output

    async def test_file_too_large_returns_error(self, tool: Read, tmp_path: Path) -> None:
        # Patching the constant is simpler than mocking stat() (which gets
        # called by exists() / is_file() too) or creating a real 10 MB file.
        target = tmp_path / "small.txt"
        target.write_text("hello world")  # 11 bytes
        with patch("openharness.tools.read.MAX_READ_BYTES", 5):
            result = await tool.execute(
                ReadInput(path=str(target)),
                _ctx(tmp_path),
            )
        assert result.is_error is True
        assert "file too large" in result.output


class TestReadSentinels:
    async def test_empty_file_returns_empty_sentinel(self, tool: Read, tmp_path: Path) -> None:
        target = tmp_path / "blank.txt"
        target.touch()
        result = await tool.execute(ReadInput(path=str(target)), _ctx(tmp_path))
        assert result.is_error is False
        assert result.output == "(empty)"
        assert result.metadata["size_bytes"] == 0


class TestReadOffsetLimit:
    @pytest.fixture
    def numbered_file(self, tmp_path: Path) -> Path:
        target = tmp_path / "lines.txt"
        target.write_text("line1\nline2\nline3\nline4\nline5\n")
        return target

    async def test_offset_starts_at_given_line_one_indexed(
        self, tool: Read, numbered_file: Path
    ) -> None:
        result = await tool.execute(
            ReadInput(path=str(numbered_file), offset=3),
            _ctx(numbered_file.parent),
        )
        assert result.output == "line3\nline4\nline5\n"

    async def test_limit_caps_line_count(self, tool: Read, numbered_file: Path) -> None:
        result = await tool.execute(
            ReadInput(path=str(numbered_file), limit=2),
            _ctx(numbered_file.parent),
        )
        assert result.output == "line1\nline2\n"

    async def test_offset_and_limit_combined(self, tool: Read, numbered_file: Path) -> None:
        result = await tool.execute(
            ReadInput(path=str(numbered_file), offset=2, limit=2),
            _ctx(numbered_file.parent),
        )
        assert result.output == "line2\nline3\n"


class TestReadEncoding:
    async def test_invalid_utf8_bytes_replaced_not_raised(self, tool: Read, tmp_path: Path) -> None:
        # 0xFF 0xFE is not valid UTF-8 -- errors="replace" maps each to U+FFFD.
        target = tmp_path / "binary.bin"
        target.write_bytes(b"hello\xff\xfeworld")
        result = await tool.execute(ReadInput(path=str(target)), _ctx(tmp_path))
        assert result.is_error is False
        assert "hello" in result.output
        assert "world" in result.output
        assert "�" in result.output  # replacement character
