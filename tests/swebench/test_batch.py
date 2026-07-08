"""T5 (D40.8) — batch orchestrator: dual-track output (standard
predictions.jsonl + records.jsonl), per-instance failure isolation,
idempotent resume. All git/subprocess seams injected — zero external deps.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openharness.swebench.batch import BatchPaths, run_batch
from openharness.swebench.model import SWEBenchInstance
from openharness.swebench.runner import InvocationResult, RunConfig
from openharness.swebench.workspace import WorkspaceError

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.swebench.runner import Invocation


def make_instance(n: int) -> SWEBenchInstance:
    return SWEBenchInstance.model_validate(
        {
            "instance_id": f"acme__widget-{n}",
            "repo": "acme/widget",
            "base_commit": "a" * 40,
            "problem_statement": f"bug {n}",
        }
    )


def good_invoke(inv: Invocation) -> InvocationResult:
    envelope = json.dumps(
        {
            "type": "result",
            "result": "done",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "num_turns": 2,
            "session_id": "s",
            "verification": None,
        }
    )
    return InvocationResult(exit_code=0, stdout=envelope, stderr="")


def make_paths(tmp_path: Path) -> BatchPaths:
    return BatchPaths(
        cache_root=tmp_path / "cache",
        workspaces_root=tmp_path / "ws",
        predictions_path=tmp_path / "out" / "predictions.jsonl",
        records_path=tmp_path / "out" / "records.jsonl",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestRunBatch:
    def test_three_instances_produce_dual_track_output(self, tmp_path: Path) -> None:
        paths = make_paths(tmp_path)

        summary = run_batch(
            [make_instance(1), make_instance(2), make_instance(3)],
            paths,
            RunConfig(model="m1"),
            invoke=good_invoke,
            ensure_cache=lambda root, repo: root / "fake.git",
            prepare=lambda cache, commit, dest: None,
            extract=lambda ws: "diff --git a/f b/f\n",
        )

        predictions = read_jsonl(paths.predictions_path)
        records = read_jsonl(paths.records_path)
        assert len(predictions) == len(records) == 3
        row = predictions[0]
        assert set(row) == {"instance_id", "model_name_or_path", "model_patch"}
        assert row["model_patch"].startswith("diff --git")
        assert "m1" in row["model_name_or_path"]
        assert "openharness" in row["model_name_or_path"]
        assert records[0]["status"] == "completed"
        assert summary.total == 3
        assert summary.by_status == {"completed": 3}

    def test_single_failure_does_not_abort_batch(self, tmp_path: Path) -> None:
        paths = make_paths(tmp_path)

        def prepare(cache: Path, commit: str, dest: Path) -> None:
            if "widget-2" in str(dest):
                raise WorkspaceError("checkout", "bad commit")

        summary = run_batch(
            [make_instance(1), make_instance(2), make_instance(3)],
            paths,
            RunConfig(),
            invoke=good_invoke,
            ensure_cache=lambda root, repo: root / "fake.git",
            prepare=prepare,
            extract=lambda ws: "",
        )

        records = read_jsonl(paths.records_path)
        assert len(records) == 3
        statuses = {r["instance_id"]: r["status"] for r in records}
        assert statuses["acme__widget-2"] == "workspace-failed"
        assert statuses["acme__widget-1"] == "completed"
        assert summary.by_status["workspace-failed"] == 1
        # the failed instance still gets an (empty) prediction row → resume skips it
        predictions = read_jsonl(paths.predictions_path)
        assert len(predictions) == 3

    def test_model_name_falls_back_to_env_configured_model(self, tmp_path: Path) -> None:
        """Taxonomy fidelity: when --model isn't passed, the effective model
        comes from OPENHARNESS_MODEL — records must name it, not 'default'."""
        paths = make_paths(tmp_path)

        run_batch(
            [make_instance(1)],
            paths,
            RunConfig(model=None),
            invoke=good_invoke,
            ensure_cache=lambda root, repo: root / "fake.git",
            prepare=lambda cache, commit, dest: None,
            extract=lambda ws: "",
            base_env={"OPENHARNESS_MODEL": "qwen3.7-max"},
        )

        row = read_jsonl(paths.predictions_path)[0]
        assert "qwen3.7-max" in row["model_name_or_path"]
        assert "default" not in row["model_name_or_path"]

    def test_resume_skips_already_predicted_instances(self, tmp_path: Path) -> None:
        paths = make_paths(tmp_path)
        invoked: list[str] = []

        def counting_invoke(inv: Invocation) -> InvocationResult:
            invoked.append(inv.argv[-1])
            return good_invoke(inv)

        kwargs: dict[str, Any] = {
            "invoke": counting_invoke,
            "ensure_cache": lambda root, repo: root / "fake.git",
            "prepare": lambda cache, commit, dest: None,
            "extract": lambda ws: "d\n",
        }
        run_batch([make_instance(1), make_instance(2)], paths, RunConfig(), **kwargs)
        first_count = len(invoked)

        summary = run_batch(
            [make_instance(1), make_instance(2), make_instance(3)], paths, RunConfig(), **kwargs
        )

        assert first_count == 2
        assert len(invoked) == 3  # only instance 3 ran on resume
        assert summary.skipped == 2
        assert len(read_jsonl(paths.predictions_path)) == 3
