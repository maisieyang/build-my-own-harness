"""loop-runtime L6 — ``oh autopilot`` CLI subcommand group.

``enqueue``/``list``/``run-next`` on top of ``services/autopilot.py``'s
queue. ``run-next`` calls the existing ``_run_repair_loop`` in-process
(mocked here) rather than shelling out to ``oh ask -p ...``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module

if TYPE_CHECKING:
    import pytest


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _set_queue_path(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("OPENHARNESS_AUTOPILOT__QUEUE_PATH", path)


class TestAutopilotEnqueue:
    def test_enqueue_adds_a_queued_card(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "autopilot",
                "enqueue",
                "--goal",
                "fix the flaky test",
                "--verify",
                "pytest -q",
                "--max-iter",
                "3",
                "--source-ref",
                "manual-1",
            ],
        )

        assert result.exit_code == 0, result.stderr

        from openharness.services.autopilot import load_queue

        cards = load_queue(tmp_path / "queue.json")
        assert len(cards) == 1
        assert cards[0].goal == "fix the flaky test"
        assert cards[0].status == "queued"


class TestAutopilotList:
    def test_list_shows_enqueued_card(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        runner.invoke(
            cli_module.app,
            [
                "autopilot",
                "enqueue",
                "--goal",
                "fix the flaky test",
                "--verify",
                "pytest -q",
                "--max-iter",
                "3",
                "--source-ref",
                "manual-1",
            ],
        )
        result = runner.invoke(cli_module.app, ["autopilot", "list"])

        assert result.exit_code == 0, result.stderr
        assert "fix the flaky test" in result.stdout
        assert "queued" in result.stdout

    def test_list_empty_queue_does_not_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["autopilot", "list"])

        assert result.exit_code == 0, result.stderr


class TestAutopilotRunNext:
    def test_run_next_invokes_repair_loop_with_card_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        runner.invoke(
            cli_module.app,
            [
                "autopilot",
                "enqueue",
                "--goal",
                "fix the flaky test",
                "--verify",
                "pytest -q",
                "--max-iter",
                "3",
                "--source-ref",
                "manual-1",
            ],
        )

        captured: dict[str, object] = {}

        async def _fake_repair_loop(goal: str, **kwargs: object) -> tuple[object, int, bool]:
            captured["goal"] = goal
            captured["verify"] = kwargs.get("verify")
            captured["max_iter"] = kwargs.get("max_iter")
            return (object(), 1, True)

        monkeypatch.setattr(cli_module, "_run_repair_loop", _fake_repair_loop)

        result = runner.invoke(cli_module.app, ["autopilot", "run-next"])

        assert result.exit_code == 0, result.stderr
        assert captured["goal"] == "fix the flaky test"
        assert captured["verify"] == ["pytest -q"]
        assert captured["max_iter"] == 3

        from openharness.services.autopilot import load_queue

        cards = load_queue(tmp_path / "queue.json")
        assert cards[0].status == "completed"

    def test_run_next_marks_card_failed_on_unsuccessful_loop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        runner.invoke(
            cli_module.app,
            [
                "autopilot",
                "enqueue",
                "--goal",
                "fix the flaky test",
                "--verify",
                "pytest -q",
                "--max-iter",
                "3",
                "--source-ref",
                "manual-1",
            ],
        )

        async def _fake_repair_loop(goal: str, **kwargs: object) -> tuple[object, int, bool]:
            del goal, kwargs
            return (object(), 3, False)

        monkeypatch.setattr(cli_module, "_run_repair_loop", _fake_repair_loop)

        result = runner.invoke(cli_module.app, ["autopilot", "run-next"])

        assert result.exit_code != 0

        from openharness.services.autopilot import load_queue

        cards = load_queue(tmp_path / "queue.json")
        assert cards[0].status == "failed"

    def test_run_next_on_empty_queue_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["autopilot", "run-next"])

        assert result.exit_code == 0, result.stderr
        assert "empty" in result.stdout.lower() or "empty" in result.stderr.lower()

    def test_run_next_marks_card_failed_when_repair_loop_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Review finding: an exception from _run_repair_loop (which
        propagates uncaught by design) must not strand the card at
        status="running" forever -- it must be marked "failed" so it's
        visible and the underlying issue can be fixed, even though the
        card itself can't be automatically retried without a fresh enqueue."""
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        runner.invoke(
            cli_module.app,
            [
                "autopilot",
                "enqueue",
                "--goal",
                "fix the flaky test",
                "--verify",
                "pytest -q",
                "--max-iter",
                "3",
                "--source-ref",
                "manual-1",
            ],
        )

        async def _raising_repair_loop(goal: str, **kwargs: object) -> tuple[object, int, bool]:
            del goal, kwargs
            raise RuntimeError("network error")

        monkeypatch.setattr(cli_module, "_run_repair_loop", _raising_repair_loop)

        result = runner.invoke(cli_module.app, ["autopilot", "run-next"])

        assert result.exit_code != 0
        assert "network error" in result.stderr

        from openharness.services.autopilot import load_queue

        cards = load_queue(tmp_path / "queue.json")
        assert cards[0].status == "failed"


class TestAutopilotEnqueueRequiresVerify:
    """Review finding: enqueue allowed empty --verify with no
    --goal-condition option at all, guaranteeing _run_repair_loop raises
    ValueError later at run-next time. Reject it at enqueue time instead."""

    def test_enqueue_without_verify_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _set_minimum_env(monkeypatch)
        _set_queue_path(monkeypatch, str(tmp_path / "queue.json"))

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["autopilot", "enqueue", "--goal", "fix it", "--source-ref", "manual-1"],
        )

        assert result.exit_code == 2
        assert "--verify" in result.stderr

        from openharness.services.autopilot import load_queue

        assert load_queue(tmp_path / "queue.json") == []
