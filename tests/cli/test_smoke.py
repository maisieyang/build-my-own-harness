"""CLI smoke tests for the public front door and private headless adapter."""

from __future__ import annotations

from typer.testing import CliRunner

from openharness import Settings, __version__
from openharness.cli import app, headless_app


def test_top_level_help_returns_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "ask" not in result.stdout
    assert "OpenHarness" in result.stdout


def test_ask_help_lists_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(headless_app, ["run", "--help"])

    assert result.exit_code == 0
    # Typer 0.13+ renders help via rich with ANSI escape codes + line-
    # wrapping; on narrow terminals ``--model`` may be split across
    # columns by box-drawing chars. Strip ANSI + collapse whitespace
    # before substring-checking so the assertion is stable across
    # CI runner widths.
    import re

    plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
    plain = re.sub(r"\s+", " ", plain)
    assert "--model" in plain
    assert "--max-tokens" in plain


def test_version_flag_prints_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_settings_is_top_level_reexport() -> None:
    """Sub-unit 4e contract: ``from openharness import Settings`` works."""
    # Just touching the symbol is enough; the import at the top of this
    # file would have failed otherwise.
    assert Settings.__name__ == "Settings"
