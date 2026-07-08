"""``oh bench swebench`` — CLI wiring (D40 T6).

Command bodies are thin shells over the tested library layer
(``fetch_dataset`` / ``load_instances`` / ``run_batch``); everything with
logic lives there. Registration in the main app is the single-line
``app.add_typer(bench_app, name="bench")`` (D40 §六: cli.py = extension,
everything else unchanged).
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from openharness.swebench.batch import BatchPaths, run_batch
from openharness.swebench.errors import SWEBenchError
from openharness.swebench.fetch import fetch_dataset
from openharness.swebench.model import load_instances
from openharness.swebench.runner import RunConfig

bench_app = typer.Typer(
    help="Benchmark adapters — drive the shipped `oh` CLI over public benchmarks.",
    no_args_is_help=True,
)
swebench_app = typer.Typer(
    help=(
        "SWE-bench Lite adapter (decisions/40): fetch the dataset, run "
        "instances headless, emit sb-cli-ready predictions.jsonl."
    ),
    no_args_is_help=True,
)
bench_app.add_typer(swebench_app, name="swebench")

_ROOT = Path("benchmarks/swebench")
_DATASET_FILE = _ROOT / "dataset" / "swe-bench-lite-test.jsonl"


def _pin_config(cli_model: str | None) -> tuple[str | None, dict[str, str] | None]:
    """Pin the batch's model AND endpoint/key explicitly (D40.8 fidelity).

    The child ``oh ask`` runs with the workspace as cwd, where the
    project ``./.env`` is invisible — its settings would silently drift
    to the user-global ``~/.openharness/.env`` layer, and the records
    would lie about what actually ran. So: resolve the settings chain
    ONCE here at bench cwd (the layers the user sees when launching),
    then inject key/base_url/model as env vars into the child — env vars
    beat .env files, so every instance in the batch runs one pinned
    configuration.

    Returns ``(model, env_overrides)``; ``(cli_model, None)`` when the
    chain can't validate — the child then surfaces the real config error.
    """
    from pydantic import ValidationError

    from openharness.config import Settings

    user_env = Path.home() / ".openharness" / ".env"
    try:
        settings = Settings(_env_file=(str(user_env), ".env"))
    except ValidationError:
        return cli_model, None
    model = cli_model if cli_model is not None else settings.model
    return model, {
        "OPENHARNESS_API_KEY": settings.api_key,
        "OPENHARNESS_BASE_URL": settings.base_url,
        "OPENHARNESS_MODEL": model,
    }


@swebench_app.command()
def fetch(
    dest: Path = typer.Option(
        _DATASET_FILE,
        "--dest",
        help="Where to write the dataset JSONL.",
    ),
    dataset: str = typer.Option(
        "princeton-nlp/SWE-bench_Lite",
        "--dataset",
        help="HF dataset id (swap for a SWE-bench-Live slice in M2).",
    ),
    split: str = typer.Option("test", "--split", help="Dataset split."),
) -> None:
    """Download the dataset via the HF datasets-server API (network step;
    `run` itself is offline)."""
    try:
        count = fetch_dataset(dest, dataset=dataset, split=split)
    except SWEBenchError as exc:  # pragma: no cover — network-path rendering
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"wrote {count} instances to {dest}")


@swebench_app.command()
def run(
    dataset_file: Path = typer.Option(
        _DATASET_FILE,
        "--dataset-file",
        help="Local dataset JSONL written by `fetch`.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Run only the first N instances (after --instance-id filtering).",
    ),
    instance_id: list[str] | None = typer.Option(
        None,
        "--instance-id",
        help="Run only these instance ids. Repeatable.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model passthrough to `oh ask --model`; default = configured model.",
    ),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Enable the Docker sandbox AND allow Bash(*) for the run (D40.6).",
    ),
    timeout: float = typer.Option(
        1800.0,
        "--timeout",
        help="Per-instance wall-clock timeout in seconds.",
    ),
    max_turns: int = typer.Option(
        40,
        "--max-turns",
        min=1,
        help=(
            "Agent-loop turn cap passed to `oh ask --max-turns` (default 40: "
            "小批 showed real fixes at 8-19 turns against oh's 20 default)."
        ),
    ),
    root: Path = typer.Option(
        _ROOT,
        "--root",
        help="Adapter working root (cache/ workspaces/ out/ live under it).",
    ),
) -> None:
    """Run instances headless, serially, resumable — every instance gets a
    predictions.jsonl row and a records.jsonl row (D40.8 dual track)."""
    try:
        instances = load_instances(dataset_file, limit=limit, instance_ids=instance_id)
    except SWEBenchError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not instances:
        typer.echo("no instances matched the given filters", err=True)
        raise typer.Exit(code=1)

    paths = BatchPaths(
        cache_root=root / "cache",
        workspaces_root=root / "workspaces",
        predictions_path=root / "out" / "predictions.jsonl",
        records_path=root / "out" / "records.jsonl",
    )
    pinned_model, env_overrides = _pin_config(model)
    base_env = {**os.environ, **env_overrides} if env_overrides is not None else None
    config = RunConfig(model=pinned_model, sandbox=sandbox, timeout_s=timeout, max_turns=max_turns)
    summary = run_batch(instances, paths, config, base_env=base_env, on_progress=typer.echo)

    status_line = ", ".join(f"{k}={v}" for k, v in sorted(summary.by_status.items())) or "none"
    typer.echo(f"done: attempted={summary.total} skipped(resume)={summary.skipped} [{status_line}]")
    typer.echo(f"predictions: {paths.predictions_path}")
    typer.echo(f"records:     {paths.records_path}")
