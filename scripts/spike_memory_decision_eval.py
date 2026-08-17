"""Legacy script entry for the typed memory_decision eval.

Drives the memory_decision substrate (Sample loader, infer call,
scorers, runner) over ``evals/memory_decision/dataset.yaml`` against
the currently-configured model.

Mirrors :mod:`scripts.spike_focus_state_eval` shape so the dual
consumers stay symmetrical: the entry script picks scorers + cassette
mode, calls the runner, prints stdout, persists results.

Run::

    OPENHARNESS_EVAL_MODE=live uv run python scripts/spike_memory_decision_eval.py
    # or to re-record cassettes:
    OPENHARNESS_EVAL_MODE=record uv run python scripts/spike_memory_decision_eval.py
    # or replay from existing cassettes (no LLM cost):
    OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_memory_decision_eval.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from openai import AsyncOpenAI

from openharness.api import OpenAICompatibleApiClient
from openharness.config.settings import Settings
from openharness.eval._memory_decision_printers import print_case, print_summary
from openharness.eval.cassette import CassetteMode, CassetteStore
from openharness.eval.manual import resolve_manual_case_id, resolve_manual_cassette_mode
from openharness.eval.memory_decision import run_memory_decision_eval
from openharness.eval.memory_decision_scorers import (
    JudgmentScorer,
    MemoryTypeLLMJudgeScorer,
    PayloadValidScorer,
    PersistenceIntegrityScorer,
)


def _resolve_cassette_mode() -> CassetteMode:
    return resolve_manual_cassette_mode()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    cassette_mode = _resolve_cassette_mode()
    settings = Settings()  # type: ignore[call-arg]
    sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
    client = OpenAICompatibleApiClient(sdk=sdk)
    model = settings.model

    project_root = Path(__file__).resolve().parent.parent
    dataset_path = project_root / "evals" / "memory_decision" / "dataset.yaml"
    cassette_root = project_root / "evals" / "memory_decision" / "cassettes"
    cassette_store = CassetteStore(cassette_root)

    scorers = [
        JudgmentScorer(),
        PayloadValidScorer(),
        PersistenceIntegrityScorer(),
        MemoryTypeLLMJudgeScorer(
            api_client=client,
            model=model,
            cassette_store=cassette_store,
            cassette_mode=cassette_mode,
        ),
    ]

    print("# memory_decision eval — typed durable-memory contract")
    print(f"# model:         {model}")
    print(f"# dataset:       {dataset_path.relative_to(project_root)}")
    print(f"# cassettes:     {cassette_root.relative_to(project_root)}")
    print(f"# cassette_mode: {cassette_mode}")
    print(f"# scorers:       {[type(s).__name__ for s in scorers]}")
    print()

    results = await run_memory_decision_eval(
        dataset_path,
        scorers,
        client,
        model,
        cassette_root=cassette_root,
        cassette_mode=cassette_mode,
        case_id=resolve_manual_case_id(),
    )

    for r in results:
        print_case(r)

    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
