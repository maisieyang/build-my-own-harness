"""memory_compact eval — D41 P0 entry script (decision surface #5, C1).

Drives the memory_compact consumer (loader, fixture catalog, single-shot
infer, two `=`-grade scorers) over ``evals/memory_compact/dataset.yaml``
against the currently-configured model. Mirrors spike_tool_choice_eval.

Reference policy (dataset_card declaration 4): qwen-max. Runs on other
models are information, not gate signals.

Run::

    uv run python scripts/spike_memory_compact_eval.py
    # re-record cassettes:
    OPENHARNESS_EVAL_MODE=record uv run python scripts/spike_memory_compact_eval.py
    # replay from cassettes (no LLM cost):
    OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_memory_compact_eval.py
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, cast

from openai import AsyncOpenAI

from openharness.api import OpenAICompatibleApiClient
from openharness.config.settings import Settings
from openharness.eval.memory_compact import MemoryCompactCaseResult, run_memory_compact_eval
from openharness.eval.memory_compact_scorers import FactRecallScorer, NoiseExclusionScorer

if TYPE_CHECKING:
    from openharness.eval.cassette import CassetteMode

_REFERENCE_MODEL = "qwen-max"


def _resolve_cassette_mode() -> CassetteMode:
    raw = os.environ.get("OPENHARNESS_EVAL_MODE", "live").lower().strip()
    if raw not in ("live", "record", "replay"):
        raise SystemExit(
            f"Invalid OPENHARNESS_EVAL_MODE={raw!r}; expected one of live / record / replay"
        )
    return cast("CassetteMode", raw)


def _print_case(result: MemoryCompactCaseResult) -> None:
    preview = (
        result.output.summary_text[:80].replace("\n", " ")
        if result.output.did_apply
        else "(compaction did not apply)"
    )
    print(f"## {result.sample.case_id} [{result.sample.capability}]")
    print(f"   summary[:80]: {preview}")
    for score in result.scores:
        mark = "PASS" if score.value == 1.0 else "FAIL"
        print(f"   {mark} {score.dim}: {score.reason}")
    print()


def _print_summary(results: list[MemoryCompactCaseResult]) -> None:
    per_dim: dict[str, list[float]] = defaultdict(list)
    for result in results:
        for score in result.scores:
            if isinstance(score.value, float):
                per_dim[score.dim].append(score.value)
    print("# Summary")
    for dim, values in per_dim.items():
        print(f"  {dim}: {int(sum(values))}/{len(values)}")
    all_pass = sum(1 for r in results if all(s.value == 1.0 for s in r.scores))
    print(f"  cases all-dims-pass: {all_pass}/{len(results)}")


async def main() -> None:
    cassette_mode = _resolve_cassette_mode()
    if cassette_mode == "replay":
        # F8 fix (dogfood Day 1, learnings/dogfood-day1): replay never
        # calls the API — it must not depend on Settings (credentials)
        # nor on the user's configured model (a project .env pointing at
        # another model made the documented replay command fail with
        # CassetteMissingError). Default to the reference model so the
        # committed cassettes are found; an explicit OPENHARNESS_MODEL
        # env var still overrides for info runs on other recordings.
        client = None
        model = os.environ.get("OPENHARNESS_MODEL", _REFERENCE_MODEL)
    else:
        settings = Settings()  # type: ignore[call-arg]
        sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
        client = OpenAICompatibleApiClient(sdk=sdk, extra_body=settings.extra_body)
        model = settings.model

    project_root = Path(__file__).resolve().parent.parent
    dataset_path = project_root / "evals" / "memory_compact" / "dataset.yaml"
    cassette_root = project_root / "evals" / "memory_compact" / "cassettes"

    scorers = [FactRecallScorer(), NoiseExclusionScorer()]

    print("# memory_compact eval — D41 P0 (decision surface #1 / B2)")
    print(f"# model:         {model}")
    if model != _REFERENCE_MODEL:
        print(
            f"# NOTE: reference policy is {_REFERENCE_MODEL} (D41.5) — "
            "this run is information, not a gate signal"
        )
    print(f"# dataset:       {dataset_path.relative_to(project_root)}")
    print(f"# cassette_mode: {cassette_mode}")
    print()

    results = await run_memory_compact_eval(
        dataset_path,
        scorers,
        client,
        model,
        cassette_root=cassette_root,
        cassette_mode=cassette_mode,
    )

    for result in results:
        _print_case(result)
    _print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
