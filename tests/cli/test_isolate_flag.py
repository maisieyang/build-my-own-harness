"""Single-shot ``--isolate`` CLI behavior."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from openharness.protocols.stream_events import ApiStreamEvent


class _RecordingStubClient:
    def __init__(self, events: list[ApiStreamEvent]) -> None:
        self._events = events

    async def stream_message(self, request: object) -> object:
        for event in self._events:
            yield event


def _hello_world_events() -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text="hello"),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="hello")]),
            usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            stop_reason="end_turn",
        ),
    ]


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _init_repo_and_chdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    monkeypatch.chdir(repo)


def test_isolate_requires_headless_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_minimum_env(monkeypatch)
    stub = _RecordingStubClient(_hello_world_events())
    monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
    runner = CliRunner()

    no_print = runner.invoke(cli_module.app, ["ask", "--isolate", "go"])
    text_output = runner.invoke(cli_module.app, ["ask", "-p", "--isolate", "go"])

    assert no_print.exit_code == 2
    assert "--isolate" in no_print.stderr
    assert text_output.exit_code == 2
    assert "json" in text_output.stderr


def test_plain_json_run_has_no_run_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_minimum_env(monkeypatch)
    stub = _RecordingStubClient(_hello_world_events())
    monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

    result = CliRunner().invoke(cli_module.app, ["ask", "-p", "--output-format", "json", "go"])

    assert result.exit_code == 0, result.stderr
    assert "run" not in json.loads(result.stdout)


def test_isolate_populates_worktree_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_minimum_env(monkeypatch)
    _init_repo_and_chdir(monkeypatch, tmp_path)
    stub = _RecordingStubClient(_hello_world_events())
    monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

    result = CliRunner().invoke(
        cli_module.app, ["ask", "-p", "--isolate", "--output-format", "json", "go"]
    )

    assert result.exit_code == 0, result.stderr
    run = json.loads(result.stdout)["run"]
    assert run["worktree_path"] is not None
    assert run["branch_name"] is not None
    assert run["status"] == "completed"
    assert "journal_path" not in run
