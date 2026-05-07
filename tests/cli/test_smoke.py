"""CLI smoke tests -- verify the command surface is wired up.

These are the cheapest possible checks: ``oh --help`` and ``oh ask
--help`` both return zero and mention the expected commands/options.
They guarantee that the Typer app stays importable and registers the
``ask`` subcommand even after later refactors.
"""

from __future__ import annotations

from typer.testing import CliRunner

from openharness import Settings, __version__
from openharness.cli import app


def test_top_level_help_returns_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "ask" in result.stdout
    assert "OpenHarness" in result.stdout


def test_ask_help_lists_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["ask", "--help"])

    assert result.exit_code == 0
    assert "--model" in result.stdout
    assert "--max-tokens" in result.stdout


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
