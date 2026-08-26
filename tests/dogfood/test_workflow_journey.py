"""Deterministic boundaries for the manual-only workflow dogfood journey."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from dogfood.workflow_journey import (
    FIXTURE_SOURCE,
    JOURNEY_STEPS,
    VALIDATION_COMMAND_TEXT,
    JourneyFailure,
    _run_baseline,
    build_run_manifest,
    compose_process_env,
    parse_args,
    prepare_journey,
    render_manual_runbook,
)

from openharness.execution import CommandOperation, ProcessCompleted
from openharness.memory.paths import get_project_memory_dir
from openharness.services.snapshot import get_snapshot_dir

if TYPE_CHECKING:
    from pathlib import Path


def test_manual_journey_covers_the_complete_cross_process_workflow() -> None:
    assert [step.case_id for step in JOURNEY_STEPS] == [
        "DPG-016",
        "DPG-017",
        "DPG-018",
        "DPG-019",
        "DPG-020",
        "DPG-021",
    ]
    assert [step.phase for step in JOURNEY_STEPS] == [
        "default",
        "plan",
        "goal",
        "compact-snapshot",
        "resume",
        "memory",
    ]
    assert JOURNEY_STEPS[4].new_process is True
    assert all(step.inputs for step in JOURNEY_STEPS)
    assert all(step.observe for step in JOURNEY_STEPS)


def test_user_inputs_are_natural_language_without_hidden_oracles() -> None:
    all_inputs = "\n".join(value for step in JOURNEY_STEPS for value in step.inputs)

    for implementation_detail in (
        "MemoryUpsert",
        "MemoryShow",
        "workflow-collaboration-preference",
        "user 类型",
        "feedback 类型",
        "body 必须",
        "OPENHARNESS-JOURNEY-V1",
    ):
        assert implementation_detail not in all_inputs
    assert "请记住我的协作习惯" in all_inputs
    assert "按刚才批准的计划执行" in all_inputs
    assert "你还记得我之前告诉你的协作习惯吗" in all_inputs


def test_goal_input_materializes_the_approved_plan_as_a_bounded_contract() -> None:
    goal_input = JOURNEY_STEPS[2].inputs[0]

    assert "修复折扣计算" in goal_input
    assert "只接受 0 到 100" in goal_input
    assert "0%、100%、非法范围和小数百分比" in goal_input
    assert VALIDATION_COMMAND_TEXT in goal_input
    assert "不要用替代命令宣告完成" in goal_input
    assert "最多检查 8 轮" in goal_input


def test_fixture_validation_command_is_self_contained() -> None:
    fixture_root = FIXTURE_SOURCE
    instructions = (fixture_root / "AGENTS.md").read_text(encoding="utf-8")

    assert f"`{VALIDATION_COMMAND_TEXT}`" in instructions
    assert "../../.." not in instructions
    assert (fixture_root / "pytest.ini").read_text(encoding="utf-8") == "[pytest]\n"


def test_baseline_runs_the_fixture_command_through_the_workspace_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []

    class FakeSession:
        async def execute(self, operation: CommandOperation) -> ProcessCompleted:
            events.append(operation)
            return ProcessCompleted(output="1 failed, 1 passed\n", exit_code=1)

        async def close(self) -> None:
            events.append("closed")

    class FakeBackend:
        def __init__(self, *, cwd: Path) -> None:
            events.append(("backend", cwd))

        async def open(self, profile: object) -> FakeSession:
            events.append(("profile", profile))
            return FakeSession()

    monkeypatch.setattr("dogfood.workflow_journey.WORK_DIR", tmp_path)
    monkeypatch.setattr("dogfood.workflow_journey.SeatbeltBackend", FakeBackend)

    result = _run_baseline()

    assert result.returncode == 1
    assert result.output == "1 failed, 1 passed\n"
    assert events[0] == ("backend", tmp_path)
    assert events[1][0] == "profile"
    assert isinstance(events[2], CommandOperation)
    assert events[2].command == VALIDATION_COMMAND_TEXT
    assert events[2].cwd == tmp_path
    assert events[3] == "closed"


def test_manual_runbook_is_rendered_from_the_same_prompts_and_observations() -> None:
    runbook = render_manual_runbook(run_id="manual-01")

    assert "manual-01" in runbook
    assert "由你判断" in runbook
    assert "dogfood.workflow_journey repl" not in runbook
    assert "cwd preflight" not in runbook
    for step in JOURNEY_STEPS:
        assert f"## {step.case_id}" in runbook
        for value in step.inputs:
            assert f"```text\n{value}\n```" in runbook
        for observation in step.observe:
            assert observation in runbook


def test_manifest_records_a_manual_experiment_not_automatic_expectations() -> None:
    manifest = build_run_manifest(run_id="manual-01")

    assert manifest["schema"] == "openharness.dogfood.workflow-journey.manual.v2"
    assert manifest["run_id"] == "manual-01"
    assert manifest["mode"] == "manual"
    assert set(manifest["launch"]) == {"cwd", "fresh", "resume"}
    assert "dogfood.workflow_journey repl" not in manifest["launch"]["fresh"]
    assert " oh " in f" {manifest['launch']['fresh']} "
    assert "--resume" in manifest["launch"]["resume"]
    assert all("actions" not in step for step in manifest["steps"])
    assert all("wait_for" not in step for step in manifest["steps"])
    assert all("acceptance" not in step for step in manifest["steps"])
    assert json.dumps(manifest, ensure_ascii=False)


def test_cli_does_not_offer_an_automatic_journey() -> None:
    with pytest.raises(SystemExit):
        parse_args(["auto"])

    with pytest.raises(SystemExit):
        parse_args(["repl"])


def test_process_env_loads_layers_without_overriding_the_shell(
    tmp_path: Path,
) -> None:
    user_env = tmp_path / "user.env"
    user_env.write_text("OPENHARNESS_MODEL=user-model\nUSER_ONLY=yes\n", encoding="utf-8")
    project_env = tmp_path / "project.env"
    project_env.write_text(
        "OPENHARNESS_MODEL=project-model\nPROJECT_ONLY=yes\n",
        encoding="utf-8",
    )

    result = compose_process_env(
        env_files=(user_env, project_env),
        environ={"OPENHARNESS_MODEL": "shell-model", "PATH": "/bin"},
    )

    assert result["OPENHARNESS_MODEL"] == "shell-model"
    assert result["USER_ONLY"] == "yes"
    assert result["PROJECT_ONLY"] == "yes"
    assert result["PATH"] == "/bin"


def test_prepare_resets_only_the_disposable_fixture_and_its_control_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    source = tmp_path / "source"
    source.mkdir()
    (source / "pricing.py").write_text("baseline\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    target = runtime_root / "work" / "journey"
    target.mkdir(parents=True)
    (target / "pricing.py").write_text("mutated\n", encoding="utf-8")

    snapshot_dir = get_snapshot_dir(target)
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "current.json").write_text("stale", encoding="utf-8")
    memory_dir = get_project_memory_dir(target)
    memory_dir.mkdir(parents=True)
    (memory_dir / "old.md").write_text("stale", encoding="utf-8")

    result = prepare_journey(source=source, target=target, runtime_root=runtime_root)

    assert (target / "pricing.py").read_text(encoding="utf-8") == "baseline\n"
    assert not snapshot_dir.exists()
    assert not memory_dir.exists()
    assert result.snapshot_dir == snapshot_dir
    assert result.memory_dir == memory_dir


def test_prepare_refuses_a_target_outside_the_dogfood_runtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pricing.py").write_text("baseline\n", encoding="utf-8")

    with pytest.raises(JourneyFailure, match="outside dogfood runtime root"):
        prepare_journey(
            source=source,
            target=tmp_path / "unrelated",
            runtime_root=tmp_path / "runtime",
        )
