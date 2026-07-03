"""loop-runtime Track B T8 (stretch) -- ``oh run show <run-id>`` read-only
inspector.

Reconstructs a run's summary (goal/worktree/branch/status/attempts) from
its append-only journal.jsonl (NOT from state.json -- Wave 3's review
found state.json is never actually written in production, so --resume-run
was rebuilt to depend only on the journal; this command follows the same
lesson and depends only on the journal too, to avoid the same trap).

Primary happy-path test runs a REAL repair loop through production code
first (no hand-seeded fixture), then inspects THAT run -- mirroring the
end-to-end regression test added for --resume-run after the state.json
bug was found.
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
from openharness.services.run_journal import RunJournal, get_run_dir

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from openharness.protocols.stream_events import ApiStreamEvent


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


class TestRunShowAgainstRealProductionRun:
    def test_shows_goal_status_and_attempts_from_a_real_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)

        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        runner = CliRunner()
        ask_result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--isolate",
                "--verify",
                "true",
                "--max-iter",
                "2",
                "--output-format",
                "json",
                "fix the real bug",
            ],
        )
        assert ask_result.exit_code == 0, ask_result.stderr
        run_id = json.loads(ask_result.stdout)["run"]["run_id"]

        show_result = runner.invoke(cli_module.app, ["run", "show", run_id])

        assert show_result.exit_code == 0, show_result.stderr
        assert run_id in show_result.stdout
        assert "fix the real bug" in show_result.stdout
        assert "completed" in show_result.stdout
        assert "attempts:" in show_result.stdout.lower()

    def test_json_format_reflects_real_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)

        stub = _StubApiClient(events_per_turn=[_hello_world_events(), _hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        runner = CliRunner()
        ask_result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--verify",
                "false",
                "--max-iter",
                "2",
                "--output-format",
                "json",
                "another goal",
            ],
        )
        assert ask_result.exit_code != 0
        run_id = json.loads(ask_result.stdout)["run"]["run_id"]

        show_result = runner.invoke(cli_module.app, ["run", "show", run_id, "--format", "json"])

        assert show_result.exit_code == 0, show_result.stderr
        obj = json.loads(show_result.stdout)
        assert obj["run_id"] == run_id
        assert obj["goal"] == "another goal"
        assert obj["status"] == "failed"
        assert obj["attempts"] == 2
        assert len(obj["events"]) > 0


class TestRunShowFailClosed:
    def test_unknown_run_id_exits_1(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["run", "show", "does-not-exist"])

        assert result.exit_code == 1
        assert "does-not-exist" in result.stderr

    def test_malformed_journal_exits_1(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        repo = _init_repo_and_chdir(monkeypatch, tmp_path)
        run_dir = get_run_dir(repo, "broken-run")
        run_dir.mkdir(parents=True)
        (run_dir / "journal.jsonl").write_text("not json\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["run", "show", "broken-run"])

        assert result.exit_code == 1

    def test_run_with_no_finished_event_shows_running_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A run interrupted before open_run_session's finally block ran
        (e.g. SIGKILL) has no run_finished event -- show it as still
        running rather than crashing."""
        _set_minimum_env(monkeypatch)
        repo = _init_repo_and_chdir(monkeypatch, tmp_path)
        run_dir = get_run_dir(repo, "still-running")
        journal = RunJournal(run_dir, "still-running")
        journal.append(
            "run_started",
            attempt=None,
            goal="an unfinished goal",
            worktree_path=None,
            branch_name=None,
        )
        journal.append("attempt_started", attempt=1, sub_goal=None)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["run", "show", "still-running"])

        assert result.exit_code == 0, result.stderr
        assert "running" in result.stdout.lower()

    def test_run_started_missing_required_field_exits_1_not_crash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Review fix: a run_started event missing a required field must
        fail closed with a clean message, not an uncaught KeyError."""
        _set_minimum_env(monkeypatch)
        repo = _init_repo_and_chdir(monkeypatch, tmp_path)
        run_dir = get_run_dir(repo, "schema-drifted")
        run_dir.mkdir(parents=True)
        (run_dir / "journal.jsonl").write_text(
            json.dumps({"event": "run_started", "goal": "x"})
            + "\n",  # missing worktree_path/branch_name
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["run", "show", "schema-drifted"])

        assert result.exit_code == 1
        assert "missing required field" in result.stderr

    def test_path_traversal_run_id_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Review fix: run_id must never escape the intended runs/ tree."""
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["run", "show", "../../../../etc"])

        assert result.exit_code == 1
        assert "run_id" in result.stderr

    def test_resumed_run_shows_running_not_stale_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Review fix (end-to-end): a run that finished, then got
        resumed via --resume-run and is still mid-attempt, must report
        status=running -- not the stale terminal status from before the
        resume."""
        _set_minimum_env(monkeypatch)
        repo = _init_repo_and_chdir(monkeypatch, tmp_path)
        run_dir = get_run_dir(repo, "resumed-run")
        journal = RunJournal(run_dir, "resumed-run")
        journal.append(
            "run_started", attempt=None, goal="fix it", worktree_path=None, branch_name=None
        )
        journal.append("attempt_started", attempt=1, sub_goal=None)
        journal.append(
            "attempt_finished",
            attempt=1,
            sub_goal=None,
            stop_reason="end_turn",
            gate_passed=False,
            gate_feedback="failed",
            succeeded=False,
        )
        journal.append("run_finished", attempt=None, status="failed")
        # Simulate --resume-run having started a new session that hasn't
        # finished yet (no new run_finished appended).
        journal.append(
            "run_resumed", attempt=None, goal="fix it", worktree_path=None, branch_name=None
        )
        journal.append("attempt_started", attempt=2, sub_goal=None)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["run", "show", "resumed-run"])

        assert result.exit_code == 0, result.stderr
        assert "status:        running" in result.stdout
