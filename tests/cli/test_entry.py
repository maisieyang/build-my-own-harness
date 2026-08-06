"""Tests for the bare ``oh`` entry point (repl-ux plan §1 — 正门).

Bare ``oh`` must enter the chat REPL — one word enters the session,
matching the mental model the plan targets. The command surface is
otherwise untouched: ``--help`` still lists subcommands, ``--version``
still short-circuits, and every subcommand keeps working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.execution import SandboxUnavailableError

if TYPE_CHECKING:
    import pytest


def _capture_run_chat(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub ``_run_chat`` (the async REPL driver) and record its kwargs."""
    captured: dict[str, Any] = {}

    async def _fake_run_chat(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "_run_chat", _fake_run_chat)
    return captured


class TestBareEntry:
    def test_bare_oh_enters_chat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_run_chat(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, [])

        assert result.exit_code == 0
        assert captured, "bare `oh` must invoke the chat REPL driver"

    def test_bare_oh_uses_chat_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_run_chat(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, [])

        assert result.exit_code == 0
        # Same defaults as an argless ``oh chat`` invocation.
        assert captured["model_override"] is None
        assert captured["permission_mode_override"] is None
        assert captured["sandbox_backend_override"] is None
        assert captured["resume"] is False

    def test_sandbox_startup_failure_is_rendered_without_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fail_to_start(**kwargs: Any) -> None:
            del kwargs
            raise SandboxUnavailableError("seatbelt failed to install boundary")

        monkeypatch.setattr(cli_module, "_run_chat", _fail_to_start)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 1
        assert "Sandbox error: seatbelt failed to install boundary" in result.stderr
        assert "Traceback" not in result.stderr

    def test_version_still_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_run_chat(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["--version"])

        assert result.exit_code == 0
        assert "openharness" in result.stdout
        assert not captured, "--version must not start the REPL"

    def test_help_still_lists_subcommands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_run_chat(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["--help"])

        assert result.exit_code == 0
        assert "ask" in result.stdout
        assert "chat" in result.stdout
        assert not captured, "--help must not start the REPL"

    def test_explicit_chat_subcommand_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_run_chat(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
        assert captured
