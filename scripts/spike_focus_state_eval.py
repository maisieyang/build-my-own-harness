"""focus_state.py prompt eval — Stage 1 substrate thin wrapper.

Stage 1 refactor: case definitions + scorer implementations + runner are now in
``src/openharness/eval/``. Dataset is in ``evals/focus_state/dataset.yaml``.
This script is the printer + entry point — picks scorers, calls the runner,
formats stdout.

Adding a 6th case = edit dataset.yaml, no Python change. Adding a new scorer
class (e.g., Stage 3 LLM-judge) = implement the Scorer Protocol in
``src/openharness/eval/scorers.py`` and append to the ``scorers`` list below.

Run:
    OPENHARNESS_EVAL_MODE=live uv run python scripts/spike_focus_state_eval.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from openai import AsyncOpenAI

from openharness.api import OpenAICompatibleApiClient
from openharness.config.settings import Settings
from openharness.eval._printers import print_case, print_summary
from openharness.eval.cassette import CassetteMode, CassetteStore
from openharness.eval.manual import resolve_manual_case_id, resolve_manual_cassette_mode
from openharness.eval.results import (
    RunMetadata,
    build_result_filename,
    compute_file_hash,
    compute_rubric_hashes,
    compute_text_hash,
    get_git_info,
    utc_iso_now,
    write_run_results,
)
from openharness.eval.rubrics import CAPABILITY_RUBRICS
from openharness.eval.runner import run_eval
from openharness.eval.scorers import (
    CapabilityAssertionsScorer,
    CapabilityLLMJudgeScorer,
    GoalKeywordMatchScorer,
    ParseOkScorer,
)
from openharness.services.focus_state import FOCUS_STATE_SYSTEM_PROMPT

# Stdout formatting helpers extracted to openharness.eval._printers
# (single source of truth, shared with `oh eval focus_state` CLI subcommand).


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _resolve_cassette_mode() -> CassetteMode:
    """Require an explicit manual mode."""
    return resolve_manual_cassette_mode()


async def main() -> None:
    cassette_mode = _resolve_cassette_mode()
    settings = Settings()  # type: ignore[call-arg]
    sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
    client = OpenAICompatibleApiClient(sdk=sdk)
    model = settings.model

    # Dataset path: project_root / evals / focus_state / dataset.yaml
    project_root = Path(__file__).resolve().parent.parent
    dataset_path = project_root / "evals" / "focus_state" / "dataset.yaml"
    cassette_root = project_root / "evals" / "focus_state" / "cassettes"
    results_root = project_root / "evals" / "focus_state" / "results"
    cassette_store = CassetteStore(cassette_root)

    scorers = [
        ParseOkScorer(),
        GoalKeywordMatchScorer(),
        CapabilityAssertionsScorer(),
        # Stage 3 — selective LLM-judge (T4/T5/T6/T7 only per D32.1);
        # other capabilities → Score(value="NA"). Self-preference accepted
        # (judge_model = main model, D32.5).
        # Stage 4 — share cassette state with runner so judge calls replay
        # alongside infer calls (D33.4).
        CapabilityLLMJudgeScorer(
            api_client=client,
            model=model,
            cassette_store=cassette_store,
            cassette_mode=cassette_mode,
        ),
    ]

    print("# focus_state.py eval — Stage 5 (cassette + results persistence)")
    print(f"# model:         {model}")
    print(f"# dataset:       {dataset_path.relative_to(Path.cwd())}")
    print(f"# cassettes:     {cassette_root.relative_to(Path.cwd())}")
    print(f"# cassette_mode: {cassette_mode}")
    print(f"# results dir:   {results_root.relative_to(Path.cwd())}")
    print(f"# scorers:       {[type(s).__name__ for s in scorers]}")

    started_at = utc_iso_now()

    results = await run_eval(
        dataset_path,
        scorers,
        client,
        model,
        cassette_root=cassette_root,
        cassette_mode=cassette_mode,
        case_id=resolve_manual_case_id(),
    )

    for result in results:
        print_case(result)

    print_summary(results)

    # Stage 5 + 5.1 — persist results JSONL with full axes stamp
    # (D34.3 + Stage 5.1 artifact capture).
    completed_at = utc_iso_now()
    git_commit, git_dirty = get_git_info(project_root)
    metadata = RunMetadata(
        started_at=started_at,
        completed_at=completed_at,
        model=model,
        judge_model=model,  # single-model env per D32.5
        cassette_mode=cassette_mode,
        dataset_path=str(dataset_path.relative_to(project_root)),
        dataset_sha256=compute_file_hash(dataset_path),
        prompt_sha256=compute_text_hash(FOCUS_STATE_SYSTEM_PROMPT),
        prompt_text=FOCUS_STATE_SYSTEM_PROMPT,
        rubric_sha256s=compute_rubric_hashes(CAPABILITY_RUBRICS),
        rubric_texts=dict(CAPABILITY_RUBRICS),
        scorer_classes=[type(s).__name__ for s in scorers],
        n_cases=len(results),
        git_commit=git_commit,
        git_dirty=git_dirty,
    )
    result_filename = build_result_filename(metadata)
    result_path = results_root / result_filename
    write_run_results(result_path, metadata, results)
    print(f"\n# results written: {result_path.relative_to(Path.cwd())}")


if __name__ == "__main__":
    asyncio.run(main())
