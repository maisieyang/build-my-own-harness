"""loop-runtime Track B T5 -- ``_run_repair_loop`` journal wiring +
``start_attempt``/``seed_verification`` resume-entry-state.

New params: ``journal: RunJournal | None``, ``start_attempt: int = 1``,
``seed_verification: GateResult | None = None``, ``sub_goal_label: str |
None = None``. Not yet CLI-flag-reachable (T6/T7 wire ``--isolate``/
``--resume-run``) -- these are direct calls to ``_run_repair_loop``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import openharness.cli as cli_module
from engine.conftest import _StubApiClient
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.services.run_journal import RunJournal, load_journal
from openharness.verification.semantic_gate import SemanticGateResult

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.protocols.stream_events import ApiStreamEvent

_COMMON_RUN_ASK_KWARGS: dict[str, object] = {
    "model_override": None,
    "max_tokens": 8192,
    "permission_mode_override": None,
    "log_level_override": None,
    "log_format_override": None,
    "tool_result_cap_override": None,
    "auto_truncate_override": None,
}


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _hello_world_events(text: str = "hello") -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            stop_reason="end_turn",
        ),
    ]


class TestRepairLoopJournalWiring:
    async def test_journal_records_attempt_started_and_finished_each_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(
            events_per_turn=[_hello_world_events(), _hello_world_events(), _hello_world_events()]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        journal = RunJournal(tmp_path / "run-dir", "run-1")

        await cli_module._run_repair_loop(
            "fix it",
            max_iter=3,
            verify=["false"],  # always fails -> exhausts all 3 attempts
            verify_timeout=10.0,
            journal=journal,
            **_COMMON_RUN_ASK_KWARGS,
        )

        events = load_journal(journal.run_dir)
        started = [e for e in events if e["event"] == "attempt_started"]
        finished = [e for e in events if e["event"] == "attempt_finished"]
        assert [e["attempt"] for e in started] == [1, 2, 3]
        assert [e["attempt"] for e in finished] == [1, 2, 3]
        assert all(e["gate_passed"] is False for e in finished)
        assert all(e["succeeded"] is False for e in finished)

    async def test_journal_stops_after_first_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        journal = RunJournal(tmp_path / "run-dir", "run-1")

        await cli_module._run_repair_loop(
            "fix it",
            max_iter=3,
            verify=["true"],  # always passes -> succeeds on attempt 1
            verify_timeout=10.0,
            journal=journal,
            **_COMMON_RUN_ASK_KWARGS,
        )

        events = load_journal(journal.run_dir)
        finished = [e for e in events if e["event"] == "attempt_finished"]
        assert len(finished) == 1
        assert finished[0]["succeeded"] is True

    async def test_sub_goal_label_included_in_journal_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        journal = RunJournal(tmp_path / "run-dir", "run-1")

        await cli_module._run_repair_loop(
            "fix it",
            max_iter=1,
            verify=["true"],
            verify_timeout=10.0,
            journal=journal,
            sub_goal_label="step one",
            **_COMMON_RUN_ASK_KWARGS,
        )

        events = load_journal(journal.run_dir)
        assert all(e["sub_goal"] == "step one" for e in events)

    async def test_no_journal_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing (pre-Track-B) callers pass no journal at all -- must
        keep working exactly as before."""
        _set_minimum_env(monkeypatch)
        stub = _StubApiClient(events_per_turn=[_hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        outcome, attempts, succeeded = await cli_module._run_repair_loop(
            "fix it",
            max_iter=1,
            verify=["true"],
            verify_timeout=10.0,
            **_COMMON_RUN_ASK_KWARGS,
        )

        assert succeeded is True
        assert attempts == 1
        assert outcome.stop_reason == "end_turn"


class TestRepairLoopResumeEntryState:
    async def test_start_attempt_resumes_without_rerunning_earlier_attempts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        # Only 2 turns configured -- if the loop incorrectly restarted at
        # attempt 1 it would need 5 turns (attempts 1-5) and this stub
        # would raise "called for turn 3 but only 2 turns were configured".
        stub = _StubApiClient(events_per_turn=[_hello_world_events(), _hello_world_events()])
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        journal = RunJournal(tmp_path / "run-dir", "run-1")
        seed = SemanticGateResult(passed=False, feedback="attempt 3 failed before crash")

        _outcome, attempts, succeeded = await cli_module._run_repair_loop(
            "fix it",
            max_iter=5,
            verify=["false"],  # still fails -> exhausts remaining budget
            verify_timeout=10.0,
            journal=journal,
            start_attempt=4,
            seed_verification=seed,
            **_COMMON_RUN_ASK_KWARGS,
        )

        assert attempts == 5  # stopped at max_iter, counting from the ORIGINAL run's attempt 1
        assert succeeded is False
        assert len(stub.captured_requests) == 2  # only attempts 4 and 5 actually ran

        events = load_journal(journal.run_dir)
        started = [e for e in events if e["event"] == "attempt_started"]
        assert [e["attempt"] for e in started] == [4, 5]

    async def test_start_attempt_without_seed_verification_raises(self) -> None:
        with pytest.raises(ValueError, match="seed_verification"):
            await cli_module._run_repair_loop(
                "fix it",
                max_iter=5,
                verify=["true"],
                verify_timeout=10.0,
                start_attempt=2,
                **_COMMON_RUN_ASK_KWARGS,
            )

    async def test_start_attempt_less_than_one_raises(self) -> None:
        with pytest.raises(ValueError, match="start_attempt"):
            await cli_module._run_repair_loop(
                "fix it",
                max_iter=5,
                verify=["true"],
                verify_timeout=10.0,
                start_attempt=0,
                seed_verification=SemanticGateResult(passed=False, feedback="x"),
                **_COMMON_RUN_ASK_KWARGS,
            )
