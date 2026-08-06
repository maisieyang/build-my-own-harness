from __future__ import annotations

from pathlib import Path

from openharness.eval.permission_goal_lifecycle import (
    load_lifecycle_dataset,
    load_lifecycle_observations,
    run_permission_goal_lifecycle_eval,
)

_ROOT = Path(__file__).parents[2] / "evals" / "permission_goal_lifecycle"


def test_committed_live_observations_hold_the_lifecycle_bar() -> None:
    results = run_permission_goal_lifecycle_eval(
        _ROOT / "dataset.yaml",
        _ROOT / "observations" / "qwen3.7-max-live-2026-08-06.yaml",
    )

    assert len(results) == 3
    assert all(score.value == 1.0 for result in results for score in result.scores)


def test_loader_keeps_case_order_and_typed_expectations() -> None:
    samples = load_lifecycle_dataset(_ROOT / "dataset.yaml")
    observations = load_lifecycle_observations(
        _ROOT / "observations" / "qwen3.7-max-live-2026-08-06.yaml"
    )

    assert [sample.case_id for sample in samples] == [
        "PGL1-approve-two-minimal-overlays",
        "PGL2-deny-no-side-effect",
        "PGL3-cross-process-resume",
    ]
    assert observations["PGL1-approve-two-minimal-overlays"].decisions == (
        "approve",
        "approve",
    )


def test_scorer_reports_missing_checkpoint_without_hiding_other_dimensions(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.yaml"
    observation = tmp_path / "observation.yaml"
    dataset.write_text(
        """samples:
  - case_id: probe
    capability: exact-resume
    goal: probe goal
    target_path: /tmp/probe
    target_content: ok
    required_checkpoints: [parked, resumed, goal_met]
    expected_decisions: [approve]
    expected_file: {exists: true, content: ok}
    expected_runtime: {parked: false, grant_count: 0, decision_resumed: true}
    expected_goal: {status: met, judge_calls_while_parked: 0}
    notes: probe
""",
        encoding="utf-8",
    )
    observation.write_text(
        """metadata: {model: fake, mode: live}
observations:
  - case_id: probe
    checkpoints: [parked, goal_met]
    decisions: [approve]
    file: {exists: true, content: ok}
    runtime: {parked: false, grant_count: 0, decision_resumed: true}
    goal: {status: met, judge_calls_while_parked: 0}
""",
        encoding="utf-8",
    )

    [result] = run_permission_goal_lifecycle_eval(dataset, observation)
    scores = {score.dim: score for score in result.scores}

    assert scores["checkpoint_order"].value == 0.0
    assert "resumed" in scores["checkpoint_order"].reason
    assert scores["decision_sequence"].value == 1.0
    assert scores["side_effect"].value == 1.0
    assert scores["runtime_final"].value == 1.0
    assert scores["goal_final"].value == 1.0
