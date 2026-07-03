"""loop-runtime Track B T6 -- ``--isolate`` CLI flag + validation +
unified ``_dispatch_ask`` + JSON ``"run"`` field.

Per the approved Track B design (§3.4.1/§4.1, decoupled from loop scope
per the Claude-Code-worktree calibration): ``--isolate`` requires
``-p``/``--output-format json`` like other loop-runtime flags, but does
NOT require ``--max-iter>1``/``--decompose`` -- a plain single-shot run
can opt into worktree isolation too. The journal (`run.journal_path`) is
independently triggered by ``--max-iter>1``/``--decompose``, NOT by
``--isolate`` -- so a `run` field can carry worktree info, journal info,
both, or neither populated, depending on which flags were passed.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from engine.conftest import _StubApiClient
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


def _hello_world_events(text: str = "hello") -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            stop_reason="end_turn",
        ),
    ]


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _init_repo_and_chdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
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
    return repo


class TestIsolateValidation:
    def test_isolate_without_print_mode_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "--isolate", "go"])

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--isolate" in result.stderr

    def test_isolate_with_text_output_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "--isolate", "go"])

        assert result.exit_code == 2
        assert "--isolate" in result.stderr
        assert "json" in result.stderr

    def test_isolate_standalone_does_not_require_verify_or_max_iter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Decoupled from loop scope: --isolate alone (no --verify/
        --max-iter/--decompose) must be accepted, not rejected."""
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app, ["ask", "-p", "--isolate", "--output-format", "json", "go"]
        )

        assert result.exit_code == 0, result.stderr


class TestRunJsonField:
    def test_no_isolate_no_loop_has_no_run_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Byte-identical to pre-Track-B behavior: no session opened at
        all -> no "run" key in the emitted json."""
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "-p", "--output-format", "json", "go"])

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert "run" not in obj

    def test_isolate_alone_populates_worktree_not_journal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app, ["ask", "-p", "--isolate", "--output-format", "json", "go"]
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["run"]["worktree_path"] is not None
        assert obj["run"]["branch_name"] is not None
        assert obj["run"]["journal_path"] is None
        assert obj["run"]["status"] == "completed"

    def test_max_iter_without_isolate_populates_journal_not_worktree(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        monkeypatch.chdir(tmp_path)
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "-p", "--verify", "true", "--max-iter", "2", "--output-format", "json", "go"],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["run"]["worktree_path"] is None
        assert obj["run"]["branch_name"] is None
        assert obj["run"]["journal_path"] is not None
        assert obj["run"]["status"] == "completed"

    def test_isolate_plus_max_iter_populates_both(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)
        stub = _StubApiClient(events_per_turn=[_hello_world_events(), _hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--isolate",
                "--verify",
                "false",
                "--max-iter",
                "2",
                "--output-format",
                "json",
                "go",
            ],
        )

        assert result.exit_code != 0  # verify always fails -> exhausts attempts
        obj = json.loads(result.stdout)
        assert obj["run"]["worktree_path"] is not None
        assert obj["run"]["branch_name"] is not None
        assert obj["run"]["journal_path"] is not None
        assert obj["run"]["status"] == "failed"
