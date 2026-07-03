"""loop-runtime Track B T7 -- ``--resume-run <run-id>`` flag.

Per the approved design (§3.4.2): NOT a fusion of --resume/--max-iter --
an orthogonal axis that resumes a repair-loop RUN's entry state (attempt
count, worktree, journal), not conversation history. Validation:
+--resume/--resume-id -> exit 2; +--decompose -> exit 2; unknown run_id ->
exit 1; prompt not matching the resumed run's original goal -> exit 2
(fail-closed, never silently pick one); no completed attempts to resume
from -> exit 1.
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


def _seed_crashed_run(
    cwd: Path,
    *,
    run_id: str,
    goal: str,
    worktree_path: str | None,
    branch_name: str | None,
    completed_attempts: list[tuple[int, bool, str]],
) -> None:
    """Write a journal.jsonl on disk simulating a prior run that
    crashed/exhausted after ``completed_attempts`` (each a
    (attempt_number, gate_passed, feedback) tuple). --resume-run
    reconstructs everything it needs from the journal alone -- see
    review fix in tasks/loop-runtime-trackb-plan.md (state.json was never
    written in production, so --resume-run no longer depends on it)."""
    run_dir = get_run_dir(cwd, run_id)
    journal = RunJournal(run_dir, run_id)
    journal.append(
        "run_started",
        attempt=None,
        goal=goal,
        worktree_path=worktree_path,
        branch_name=branch_name,
    )
    for attempt, gate_passed, feedback in completed_attempts:
        journal.append("attempt_started", attempt=attempt, sub_goal=None)
        journal.append(
            "attempt_finished",
            attempt=attempt,
            sub_goal=None,
            stop_reason="end_turn",
            gate_passed=gate_passed,
            gate_feedback=feedback,
            succeeded=gate_passed,
        )


class TestResumeRunValidation:
    def test_resume_run_with_resume_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--resume-run",
                "some-id",
                "--resume",
                "--verify",
                "true",
                "--max-iter",
                "3",
                "--output-format",
                "json",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "No such option" not in result.stderr
        assert "--resume-run" in result.stderr

    def test_resume_run_with_decompose_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--resume-run",
                "some-id",
                "--decompose",
                "--verify",
                "true",
                "--max-iter",
                "3",
                "--output-format",
                "json",
                "go",
            ],
        )

        assert result.exit_code == 2
        assert "--resume-run" in result.stderr
        assert "--decompose" in result.stderr

    def test_unknown_run_id_exits_1(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--resume-run",
                "does-not-exist",
                "--verify",
                "true",
                "--max-iter",
                "3",
                "--output-format",
                "json",
                "go",
            ],
        )

        assert result.exit_code == 1

    def test_mismatched_goal_exits_2(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _set_minimum_env(monkeypatch)
        repo = _init_repo_and_chdir(monkeypatch, tmp_path)
        _seed_crashed_run(
            repo,
            run_id="run-1",
            goal="original goal",
            worktree_path=None,
            branch_name=None,
            completed_attempts=[(1, False, "attempt 1 failed")],
        )
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--resume-run",
                "run-1",
                "--verify",
                "true",
                "--max-iter",
                "3",
                "--output-format",
                "json",
                "a completely different goal",
            ],
        )

        assert result.exit_code == 2
        assert "goal" in result.stderr.lower()

    def test_no_completed_attempts_exits_1(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        repo = _init_repo_and_chdir(monkeypatch, tmp_path)
        _seed_crashed_run(
            repo,
            run_id="run-2",
            goal="fix it",
            worktree_path=None,
            branch_name=None,
            completed_attempts=[],  # crashed before any attempt finished
        )
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--resume-run",
                "run-2",
                "--verify",
                "true",
                "--max-iter",
                "3",
                "--output-format",
                "json",
                "fix it",
            ],
        )

        assert result.exit_code == 1


class TestResumeRunContinuation:
    def test_resumes_from_correct_attempt_without_rerunning_earlier_ones(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        repo = _init_repo_and_chdir(monkeypatch, tmp_path)
        _seed_crashed_run(
            repo,
            run_id="run-3",
            goal="fix it",
            worktree_path=None,
            branch_name=None,
            completed_attempts=[(1, False, "attempt 1 failed"), (2, False, "attempt 2 failed")],
        )
        # Only 1 turn configured -- if the loop incorrectly restarted at
        # attempt 1 it would need turns for attempts 1-3.
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--resume-run",
                "run-3",
                "--verify",
                "true",  # succeeds this time
                "--max-iter",
                "5",
                "--output-format",
                "json",
                "fix it",
            ],
        )

        assert result.exit_code == 0, result.stderr
        obj = json.loads(result.stdout)
        assert obj["attempts"] == 3  # continues numbering from the original run
        assert len(stub.captured_requests) == 1  # only the new attempt 3 ran
        assert obj["run"]["run_id"] == "run-3"


class TestResumeRunAgainstRealProductionRun:
    """Review fix regression: the prior test suite only exercised
    --resume-run against a hand-seeded journal (_seed_crashed_run),
    which masked that production code never actually wrote the
    persisted-state file --resume-run originally depended on. This test
    runs a REAL first invocation through production code (no test
    fixture writing journal/state directly), then resumes THAT exact
    run -- proving the whole path works end-to-end, not just against a
    fabricated fixture."""

    def test_resume_after_a_real_exhausted_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _init_repo_and_chdir(monkeypatch, tmp_path)

        # First invocation: a real repair loop that exhausts its budget
        # (verify always fails) -- journal.jsonl is written entirely by
        # production code (_run_repair_loop/open_run_session), nothing
        # hand-seeded.
        first_stub = _StubApiClient(events_per_turn=[_hello_world_events(), _hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: first_stub)
        runner = CliRunner()
        first_result = runner.invoke(
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
                "fix the real bug",
            ],
        )
        assert first_result.exit_code != 0  # exhausted without succeeding
        first_obj = json.loads(first_result.stdout)
        real_run_id = first_obj["run"]["run_id"]
        assert real_run_id

        # Second invocation: --resume-run against that SAME real run_id,
        # reading whatever production actually persisted.
        second_stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: second_stub)
        second_result = runner.invoke(
            cli_module.app,
            [
                "ask",
                "-p",
                "--resume-run",
                real_run_id,
                "--verify",
                "true",  # succeeds this time
                "--max-iter",
                "3",
                "--output-format",
                "json",
                "fix the real bug",
            ],
        )

        assert second_result.exit_code == 0, second_result.stderr
        second_obj = json.loads(second_result.stdout)
        assert second_obj["attempts"] == 3  # continues from the real prior run
        assert len(second_stub.captured_requests) == 1  # only the new attempt ran
        assert second_obj["run"]["run_id"] == real_run_id
