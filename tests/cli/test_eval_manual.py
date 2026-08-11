"""Manual-only contract for repository eval workflows."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import openharness.cli as cli_module

EVAL_NAMES = (
    "error_feedback",
    "focus_state",
    "memory_compact",
    "memory_decision",
    "memory_read",
    "permission_review",
    "skill_trigger",
    "tool_choice",
    "verify_judge",
)


def test_eval_help_lists_every_capability() -> None:
    result = CliRunner().invoke(cli_module.app, ["dev", "eval", "--help"])

    assert result.exit_code == 0
    for name in EVAL_NAMES:
        assert name in result.stdout


@pytest.mark.parametrize("name", EVAL_NAMES)
def test_eval_requires_explicit_mode(name: str) -> None:
    result = CliRunner().invoke(cli_module.app, ["dev", "eval", name])

    assert result.exit_code == 2
    assert "--mode" in result.stderr
    assert "live" in result.stderr


def test_eval_dispatches_explicit_mode_model_and_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "scripts" / "spike_error_feedback_eval.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_run(
        command: list[str], *, cwd: Path, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        observed.update(command=command, cwd=cwd, env=env, check=check)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli_module, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "dev",
            "eval",
            "error_feedback",
            "--mode",
            "live",
            "--model",
            "qwen3.7-max",
            "--case",
            "A6-grep-launch-denied",
        ],
    )

    assert result.exit_code == 0
    assert observed["cwd"] == tmp_path
    assert observed["check"] is False
    env = observed["env"]
    assert isinstance(env, dict)
    assert env["OPENHARNESS_EVAL_MODE"] == "live"
    assert env["OPENHARNESS_MODEL"] == "qwen3.7-max"
    assert env["OPENHARNESS_EVAL_CASE"] == "A6-grep-launch-denied"
    assert str(script) in observed["command"]


def test_eval_rejects_invalid_mode() -> None:
    result = CliRunner().invoke(
        cli_module.app,
        ["dev", "eval", "error_feedback", "--mode", "automatic"],
    )

    assert result.exit_code == 2
    assert "expected one of: live / record / replay" in result.stderr


def test_eval_reports_missing_repository_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module, "_repository_root", lambda: tmp_path)

    result = CliRunner().invoke(
        cli_module.app,
        ["dev", "eval", "error_feedback", "--mode", "replay"],
    )

    assert result.exit_code == 1
    assert "Eval script not found" in result.stderr


def test_eval_propagates_script_failure_and_clears_stale_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "scripts" / "spike_error_feedback_eval.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    observed_env: dict[str, str] = {}

    def fake_run(
        command: list[str], *, cwd: Path, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check
        observed_env.update(env)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(cli_module, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("OPENHARNESS_EVAL_CASE", "stale-case")

    result = CliRunner().invoke(
        cli_module.app,
        ["dev", "eval", "error_feedback", "--mode", "replay"],
    )

    assert result.exit_code == 7
    assert "OPENHARNESS_EVAL_CASE" not in observed_env


@pytest.mark.parametrize("name", EVAL_NAMES)
def test_legacy_eval_scripts_require_explicit_mode(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENHARNESS_EVAL_MODE", raising=False)
    root = Path(__file__).parents[2]
    env = dict(os.environ)
    env.pop("OPENHARNESS_EVAL_MODE", None)

    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / f"spike_{name}_eval.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "OPENHARNESS_EVAL_MODE is required" in completed.stderr
