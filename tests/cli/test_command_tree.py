"""Contract tests for the audience-oriented public command tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.utils import strip_ansi
from typer.main import get_command
from typer.testing import CliRunner

import openharness.cli as cli_module

if TYPE_CHECKING:
    import pytest


def test_root_help_exposes_only_four_product_concepts() -> None:
    root = get_command(cli_module.app)

    visible_commands = {name for name, command in root.commands.items() if not command.hidden}

    assert visible_commands == {"config", "inspect", "state", "dev"}


def test_chat_remains_callable_as_a_hidden_compatibility_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _fake_run_chat(**_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "_run_chat", _fake_run_chat)

    result = CliRunner().invoke(cli_module.app, ["chat"])

    assert result.exit_code == 0
    assert called
    root = get_command(cli_module.app)
    assert root.commands["chat"].hidden


def test_agent_options_live_on_the_root_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_chat(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "_run_chat", _fake_run_chat)

    result = CliRunner().invoke(
        cli_module.app,
        ["--model", "test-model", "--auto", "--resume"],
    )

    assert result.exit_code == 0
    assert captured["model_override"] == "test-model"
    assert captured["reviewer_posture_override"] == "auto"
    assert captured["resume"] is True


def test_root_help_only_shows_everyday_session_options() -> None:
    root = get_command(cli_module.app)

    visible_options = {
        parameter.name for parameter in root.params if not getattr(parameter, "hidden", False)
    }

    assert visible_options == {
        "_version",
        "model",
        "auto",
        "dry_run",
        "sandbox",
        "sandbox_backend",
        "resume",
        "resume_id",
    }
    assert all(parameter.help for parameter in root.params if parameter.name in visible_options)
    backend = next(parameter for parameter in root.params if parameter.name == "sandbox_backend")
    assert backend.help == "Select the verified sandbox backend."


def test_hidden_advanced_agent_options_remain_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_chat(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "_run_chat", _fake_run_chat)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "--max-tokens",
            "321",
            "--log-level",
            "INFO",
            "--no-skills",
            "--sandbox-image",
            "python:3.11",
        ],
    )

    assert result.exit_code == 0
    assert captured["max_tokens"] == 321
    assert captured["log_level_override"] == "INFO"
    assert captured["no_skills"] is True
    assert captured["sandbox_image_override"] == "python:3.11"


def test_config_defaults_to_show(monkeypatch: pytest.MonkeyPatch) -> None:
    formats: list[str] = []

    def _fake_config_show(format: str = "text") -> None:
        formats.append(format)

    monkeypatch.setattr(cli_module, "config_show", _fake_config_show)

    result = CliRunner().invoke(cli_module.app, ["config"])

    assert result.exit_code == 0
    assert formats == ["text"]


def test_new_command_groups_expose_the_migrated_subcommands() -> None:
    runner = CliRunner()
    expected = {
        "inspect": {"tools", "hooks", "plugins"},
        "state": {"memory", "snapshots"},
        "dev": {"eval", "bench"},
    }

    root = get_command(cli_module.app)
    for group_name, expected_children in expected.items():
        result = runner.invoke(cli_module.app, [group_name, "--help"])
        assert result.exit_code == 0
        group = root.commands[group_name]
        assert set(group.commands) == expected_children  # type: ignore[attr-defined]


def test_group_help_includes_copyable_leaf_command_examples() -> None:
    runner = CliRunner()
    expected_examples = {
        "inspect": "oh inspect tools list",
        "state": "oh state memory list",
        "dev": "oh dev eval --help",
    }

    for group_name, example in expected_examples.items():
        result = runner.invoke(cli_module.app, [group_name, "--help"], color=True)
        assert result.exit_code == 0
        assert example in " ".join(strip_ansi(result.stdout).split())


def test_old_top_level_group_paths_are_removed() -> None:
    runner = CliRunner()

    for old_name in (
        "tools",
        "hooks",
        "memory",
        "plugins",
        "snapshot",
        "eval",
        "bench",
    ):
        result = runner.invoke(cli_module.app, [old_name])
        assert result.exit_code != 0, old_name
        assert "No such command" in result.stderr
