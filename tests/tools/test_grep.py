"""Tests for the Grep tool — P2-T3 sub-unit 3e.

Most tests require a real ripgrep on PATH and skip cleanly if absent.
The "rg missing" path is tested separately by mocking :func:`shutil.which`.

Coverage:
- Match found: file:line prefix appears in output
- No matches → "(no matches)" sentinel
- ignore_case true vs false
- glob restricts file types
- line_cap caps output with truncation marker
- rg not on PATH → is_error=True with descriptive hint
- Pydantic validation: line_cap > 2000 rejected; pattern empty rejected
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openharness.tools.base import ToolExecutionContext
from openharness.tools.grep import (
    HARD_LINE_CAP,
    NO_MATCHES_SENTINEL,
    Grep,
    GrepInput,
)

if TYPE_CHECKING:
    from pathlib import Path


# Skip all real-rg tests on machines without ripgrep installed.
pytestmark_rg_required = pytest.mark.skipif(
    shutil.which("rg") is None,
    reason="ripgrep (rg) not installed; skipping real-binary Grep tests",
)


@pytest.fixture
def tool() -> Grep:
    return Grep()


def _ctx(cwd: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=cwd)


@pytestmark_rg_required
class TestGrepMatching:
    async def test_finds_match_with_file_line_prefix(self, tool: Grep, tmp_path: Path) -> None:
        target = tmp_path / "code.py"
        target.write_text("def hello():\n    return 'world'\n")
        result = await tool.execute(
            GrepInput(pattern="hello"),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert "hello" in result.output
        # rg --line-number prefixes "<path>:<line>:"
        assert ":1:" in result.output
        assert result.metadata["match_count"] >= 1

    async def test_no_matches_returns_sentinel(self, tool: Grep, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("hello\n")
        result = await tool.execute(
            GrepInput(pattern="ghost-string-not-here"),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert result.output == NO_MATCHES_SENTINEL
        assert result.metadata["match_count"] == 0

    async def test_ignore_case_flag(self, tool: Grep, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("Hello World\n")
        # Without ignore_case: lowercase 'hello' should not match 'Hello'
        case_sensitive = await tool.execute(
            GrepInput(pattern="hello"),
            _ctx(tmp_path),
        )
        assert case_sensitive.output == NO_MATCHES_SENTINEL
        # With ignore_case: matches.
        case_insensitive = await tool.execute(
            GrepInput(pattern="hello", ignore_case=True),
            _ctx(tmp_path),
        )
        assert case_insensitive.is_error is False
        assert "Hello World" in case_insensitive.output

    async def test_glob_restricts_file_types(self, tool: Grep, tmp_path: Path) -> None:
        (tmp_path / "match.py").write_text("target\n")
        (tmp_path / "match.txt").write_text("target\n")
        result = await tool.execute(
            GrepInput(pattern="target", glob="**/*.py"),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert "match.py" in result.output
        assert "match.txt" not in result.output


@pytestmark_rg_required
class TestGrepLineCap:
    async def test_output_truncated_when_above_cap(self, tool: Grep, tmp_path: Path) -> None:
        # Generate 30 matches; cap at 5.
        target = tmp_path / "many.txt"
        target.write_text("\n".join(f"line-{i} match" for i in range(30)) + "\n")
        result = await tool.execute(
            GrepInput(pattern="match", line_cap=5),
            _ctx(tmp_path),
        )
        assert result.is_error is False
        assert "[truncated to 5 lines" in result.output
        # Match count metadata reflects actual total, not capped.
        assert result.metadata["match_count"] == 30


class TestGrepRgMissing:
    async def test_returns_error_when_rg_not_on_path(self, tool: Grep, tmp_path: Path) -> None:
        with patch("openharness.tools.grep.shutil.which", return_value=None):
            result = await tool.execute(
                GrepInput(pattern="x"),
                _ctx(tmp_path),
            )
        assert result.is_error is True
        assert "ripgrep" in result.output


class TestGrepValidation:
    def test_empty_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GrepInput(pattern="")

    def test_line_cap_above_hard_ceiling_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GrepInput(pattern="x", line_cap=HARD_LINE_CAP + 1)


# P3-T6.6b — close the per-module 90% gate by covering edge branches.


@pytestmark_rg_required
class TestGrepEdgeBranches:
    """Three branches missed by the happy-path tests above:

    - ``--hidden`` flag append (line 88 of grep.py)
    - rg exit code >= 2 (real failure, not just "no match") (lines 109-110)
    - ``_resolve`` absolute-path branch (lines 130-133)
    """

    async def test_hidden_flag_includes_dotfiles(self, tool: Grep, tmp_path: Path) -> None:
        hidden = tmp_path / ".secret.txt"
        hidden.write_text("needle\n")

        # Without --hidden: rg ignores dotfiles, returns NO_MATCHES.
        result = await tool.execute(
            GrepInput(pattern="needle", hidden=False),
            _ctx(tmp_path),
        )
        assert "no matches" in result.output.lower()

        # With --hidden: dotfile is searched.
        result = await tool.execute(
            GrepInput(pattern="needle", hidden=True),
            _ctx(tmp_path),
        )
        assert "needle" in result.output
        assert ".secret.txt" in result.output

    async def test_rg_invalid_regex_returns_error_result(self, tool: Grep, tmp_path: Path) -> None:
        # An unbalanced character class is a ripgrep regex parse error
        # → exit code 2 → ``ToolResult(is_error=True, output="rg failed: ...")``
        result = await tool.execute(
            GrepInput(pattern="[unbalanced"),
            _ctx(tmp_path),
        )
        assert result.is_error is True
        assert "rg failed" in result.output

    async def test_absolute_path_arg_used_verbatim(self, tool: Grep, tmp_path: Path) -> None:
        """``_resolve`` should leave absolute paths alone (not prepend cwd)."""
        target = tmp_path / "abs_file.txt"
        target.write_text("findme\n")

        # cwd is a sibling tmp dir so cwd-relative resolution would miss.
        sibling = tmp_path.parent
        result = await tool.execute(
            GrepInput(pattern="findme", path=str(target)),
            _ctx(sibling),
        )
        assert "findme" in result.output
