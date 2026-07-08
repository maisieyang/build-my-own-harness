"""Batch orchestrator + dual-track output (D40.8).

Track 1 — ``predictions.jsonl``: the strict three-field SWE-bench
submission shape, consumed by official sb-cli cloud evaluation. Every
instance gets a row, even on failure (empty patch = unresolved) — that is
also what makes resume idempotent.

Track 2 — ``records.jsonl``: one line per instance with the adapter's
differentiated status + the harness's envelope self-report. This is the
entire raw material of the failure-taxonomy report; without it a failed
evaluation can only be re-run, not attributed.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness import __version__
from openharness.swebench.runner import (
    InstanceRunResult,
    Invoker,
    RunConfig,
    default_invoker,
    run_instance,
)
from openharness.swebench.workspace import (
    WorkspaceError,
    ensure_repo_cache,
    extract_patch,
    prepare_workspace,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from openharness.swebench.model import SWEBenchInstance
    from openharness.swebench.runner import Extractor

EnsureCache = Callable[["Path", str], "Path"]
Prepare = Callable[["Path", str, "Path"], None]
OnProgress = Callable[[str], None]


@dataclass(frozen=True)
class BatchPaths:
    """Filesystem layout for one batch (defaults live in the CLI layer)."""

    cache_root: Path
    workspaces_root: Path
    predictions_path: Path
    records_path: Path


@dataclass(frozen=True)
class BatchSummary:
    """``total`` counts instances actually attempted this call;
    ``skipped`` counts resume hits (already in predictions.jsonl)."""

    total: int
    skipped: int
    by_status: dict[str, int]


def model_name_for(model: str | None) -> str:
    """SWE-bench ``model_name_or_path`` naming: harness version + model."""
    return f"openharness-{__version__}+{model or 'default'}"


def _effective_model(config_model: str | None, base_env: Mapping[str, str] | None) -> str | None:
    """--model wins; otherwise the env-configured OPENHARNESS_MODEL — the
    records must name the model that actually ran, or the failure taxonomy
    can't be attributed per model."""
    if config_model is not None:
        return config_model
    env = os.environ if base_env is None else base_env
    return env.get("OPENHARNESS_MODEL")


def load_completed_ids(predictions_path: Path) -> set[str]:
    """Instance ids already present in predictions.jsonl (resume set)."""
    if not predictions_path.is_file():
        return set()
    ids: set[str] = set()
    with predictions_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("instance_id"), str):
                ids.add(row["instance_id"])
    return ids


def _append_jsonl(path: Path, obj: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


def run_batch(
    instances: Sequence[SWEBenchInstance],
    paths: BatchPaths,
    config: RunConfig,
    *,
    invoke: Invoker = default_invoker,
    extract: Extractor = extract_patch,
    ensure_cache: EnsureCache = ensure_repo_cache,
    prepare: Prepare = prepare_workspace,
    base_env: Mapping[str, str] | None = None,
    on_progress: OnProgress | None = None,
) -> BatchSummary:
    """Run instances serially (D40 OUT: parallelism deferred to M2).

    Per-instance failure isolation: any single instance's workspace or run
    failure becomes a differentiated record row, never a batch abort.
    """
    completed = load_completed_ids(paths.predictions_path)
    model_name = model_name_for(_effective_model(config.model, base_env))
    by_status: dict[str, int] = {}
    skipped = 0
    attempted = 0

    for instance in instances:
        if instance.instance_id in completed:
            skipped += 1
            continue
        attempted += 1
        workspace = paths.workspaces_root / instance.instance_id
        try:
            cache = ensure_cache(paths.cache_root, instance.repo)
            prepare(cache, instance.base_commit, workspace)
        except WorkspaceError as exc:
            record = InstanceRunResult(
                instance_id=instance.instance_id,
                status="workspace-failed",
                model_patch="",
                detail=str(exc),
            )
        else:
            record = run_instance(
                instance,
                workspace,
                config,
                invoke=invoke,
                extract=extract,
                base_env=base_env,
            )

        _append_jsonl(
            paths.predictions_path,
            {
                "instance_id": instance.instance_id,
                "model_name_or_path": model_name,
                "model_patch": record.model_patch,
            },
        )
        record_row: dict[str, object] = dataclasses.asdict(record)
        del record_row["model_patch"]  # predictions.jsonl owns the patch; keep records lean
        record_row["patch_lines"] = len(record.model_patch.splitlines())
        record_row["model_name_or_path"] = model_name
        _append_jsonl(paths.records_path, record_row)

        by_status[record.status] = by_status.get(record.status, 0) + 1
        if on_progress is not None:
            on_progress(f"{instance.instance_id}: {record.status}")

    return BatchSummary(total=attempted, skipped=skipped, by_status=by_status)
