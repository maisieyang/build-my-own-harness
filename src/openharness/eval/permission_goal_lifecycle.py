"""Hard scoring for recorded permission/goal lifecycle dogfood traces.

The live producer is intentionally separate from this deterministic scorer.
Replay proves that a recorded trace still satisfies the declared lifecycle
contract; it never claims that current model behaviour has been re-ratified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from openharness.eval.protocol import Score

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class LifecycleSample:
    case_id: str
    capability: str
    goal: str
    target_path: str
    target_content: str
    required_checkpoints: tuple[str, ...]
    expected_decisions: tuple[str, ...]
    expected_file: dict[str, object]
    expected_runtime: dict[str, object]
    expected_goal: dict[str, object]
    notes: str


@dataclass(frozen=True)
class LifecycleObservation:
    case_id: str
    checkpoints: tuple[str, ...]
    decisions: tuple[str, ...]
    file: dict[str, object]
    runtime: dict[str, object]
    goal: dict[str, object]


@dataclass(frozen=True)
class LifecycleCaseResult:
    sample: LifecycleSample
    output: LifecycleObservation
    scores: list[Score]


def normalize_status_line(line: str) -> str:
    """Strip terminal control/prompt prefixes without searching model text."""
    normalized = line.lstrip(" \t\r\n\a")
    if normalized.startswith(">>> "):
        normalized = normalized.removeprefix(">>> ").lstrip(" \t\r\n\a")
    return normalized


def status_line_contains(line: str, marker: str) -> bool:
    """Match a harness status marker only at the normalized line start."""
    return normalize_status_line(line).startswith(marker)


def load_lifecycle_dataset(path: Path) -> list[LifecycleSample]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        LifecycleSample(
            case_id=entry["case_id"],
            capability=entry["capability"],
            goal=entry["goal"],
            target_path=entry["target_path"],
            target_content=entry["target_content"],
            required_checkpoints=tuple(entry["required_checkpoints"]),
            expected_decisions=tuple(entry["expected_decisions"]),
            expected_file=dict(entry["expected_file"]),
            expected_runtime=dict(entry["expected_runtime"]),
            expected_goal=dict(entry["expected_goal"]),
            notes=entry.get("notes", ""),
        )
        for entry in data["samples"]
    ]


def load_lifecycle_observations(path: Path) -> dict[str, LifecycleObservation]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    observations: dict[str, LifecycleObservation] = {}
    for entry in data["observations"]:
        observation = LifecycleObservation(
            case_id=entry["case_id"],
            checkpoints=tuple(entry["checkpoints"]),
            decisions=tuple(entry["decisions"]),
            file=dict(entry["file"]),
            runtime=dict(entry["runtime"]),
            goal=dict(entry["goal"]),
        )
        if observation.case_id in observations:
            raise ValueError(f"duplicate lifecycle observation: {observation.case_id}")
        observations[observation.case_id] = observation
    return observations


def _ordered_subsequence(required: tuple[str, ...], observed: tuple[str, ...]) -> bool:
    position = 0
    for checkpoint in observed:
        if position < len(required) and checkpoint == required[position]:
            position += 1
    return position == len(required)


def _missing_checkpoints(required: tuple[str, ...], observed: tuple[str, ...]) -> tuple[str, ...]:
    position = 0
    for checkpoint in observed:
        if position < len(required) and checkpoint == required[position]:
            position += 1
    return required[position:]


def _subset_matches(expected: dict[str, object], observed: dict[str, object]) -> bool:
    return all(observed.get(key) == value for key, value in expected.items())


def _binary_score(*, case_id: str, dim: str, passed: bool, reason: str) -> Score:
    return Score(dim=dim, value=1.0 if passed else 0.0, reason=reason, case_id=case_id)


def score_lifecycle_case(sample: LifecycleSample, observation: LifecycleObservation) -> list[Score]:
    ordered = _ordered_subsequence(sample.required_checkpoints, observation.checkpoints)
    missing = _missing_checkpoints(sample.required_checkpoints, observation.checkpoints)
    decisions_match = observation.decisions == sample.expected_decisions
    file_match = _subset_matches(sample.expected_file, observation.file)
    runtime_match = _subset_matches(sample.expected_runtime, observation.runtime)
    goal_match = _subset_matches(sample.expected_goal, observation.goal)
    return [
        _binary_score(
            case_id=sample.case_id,
            dim="checkpoint_order",
            passed=ordered,
            reason=(
                "required checkpoints observed in order"
                if ordered
                else f"missing or out-of-order checkpoints: {list(missing)}"
            ),
        ),
        _binary_score(
            case_id=sample.case_id,
            dim="decision_sequence",
            passed=decisions_match,
            reason=(
                f"decisions matched {list(sample.expected_decisions)}"
                if decisions_match
                else (
                    f"expected {list(sample.expected_decisions)}, "
                    f"observed {list(observation.decisions)}"
                )
            ),
        ),
        _binary_score(
            case_id=sample.case_id,
            dim="side_effect",
            passed=file_match,
            reason=(
                "file effect matched"
                if file_match
                else f"expected {sample.expected_file}, observed {observation.file}"
            ),
        ),
        _binary_score(
            case_id=sample.case_id,
            dim="runtime_final",
            passed=runtime_match,
            reason=(
                "permission runtime final state matched"
                if runtime_match
                else f"expected {sample.expected_runtime}, observed {observation.runtime}"
            ),
        ),
        _binary_score(
            case_id=sample.case_id,
            dim="goal_final",
            passed=goal_match,
            reason=(
                "goal controller final state matched"
                if goal_match
                else f"expected {sample.expected_goal}, observed {observation.goal}"
            ),
        ),
    ]


def run_permission_goal_lifecycle_eval(
    dataset_path: Path, observation_path: Path
) -> list[LifecycleCaseResult]:
    samples = load_lifecycle_dataset(dataset_path)
    observations = load_lifecycle_observations(observation_path)
    expected_ids = {sample.case_id for sample in samples}
    extra_ids = set(observations) - expected_ids
    if extra_ids:
        raise ValueError(f"observations contain unknown cases: {sorted(extra_ids)}")
    results: list[LifecycleCaseResult] = []
    for sample in samples:
        try:
            observation = observations[sample.case_id]
        except KeyError as exc:
            raise ValueError(f"missing lifecycle observation: {sample.case_id}") from exc
        results.append(
            LifecycleCaseResult(
                sample=sample,
                output=observation,
                scores=score_lifecycle_case(sample, observation),
            )
        )
    return results
