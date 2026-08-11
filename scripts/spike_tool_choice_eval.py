"""tool_choice eval — D41 P0 entry script (decision surface #2, A3/A4).

Drives the tool_choice consumer (loader, single-shot infer, three
`=`-grade scorers) over ``evals/tool_choice/dataset.yaml`` against the
currently-configured model. Mirrors the memory_decision spike shape.

Reference policy (dataset_card declaration 4): qwen-max. Runs on other
models are information, not gate signals.

Run::

    OPENHARNESS_EVAL_MODE=live uv run python scripts/spike_tool_choice_eval.py
    # re-record cassettes:
    OPENHARNESS_EVAL_MODE=record uv run python scripts/spike_tool_choice_eval.py
    # replay from cassettes (no LLM cost):
    OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_tool_choice_eval.py
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from openharness.api import OpenAICompatibleApiClient
from openharness.config.settings import Settings
from openharness.eval.manual import (
    resolve_manual_case_id,
    resolve_manual_cassette_mode,
    resolve_manual_model,
)
from openharness.eval.tool_choice import ToolChoiceCaseResult, run_tool_choice_eval
from openharness.eval.tool_choice_scorers import (
    ExpectedToolScorer,
    ForbiddenToolScorer,
    InputFieldScorer,
)

if TYPE_CHECKING:
    from openharness.eval.cassette import CassetteMode


def _resolve_cassette_mode() -> CassetteMode:
    return resolve_manual_cassette_mode()


def _print_case(result: ToolChoiceCaseResult) -> None:
    calls = (
        ", ".join(f"{tu.name}({', '.join(tu.input)})" for tu in result.output.tool_uses)
        or "(no tool call)"
    )
    print(f"## {result.sample.case_id} [{result.sample.capability}]")
    print(f"   calls: {calls}")
    for score in result.scores:
        mark = "PASS" if score.value == 1.0 else "FAIL"
        print(f"   {mark} {score.dim}: {score.reason}")
    print()


def _print_summary(results: list[ToolChoiceCaseResult]) -> None:
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
    project_root = Path(__file__).resolve().parent.parent
    cassette_mode = _resolve_cassette_mode()
    if cassette_mode == "replay":
        client = None
        model = resolve_manual_model(project_root)
    else:
        settings = Settings()  # type: ignore[call-arg]
        sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
        client = OpenAICompatibleApiClient(sdk=sdk, extra_body=settings.extra_body)
        model = settings.model

    dataset_path = project_root / "evals" / "tool_choice" / "dataset.yaml"
    cassette_root = project_root / "evals" / "tool_choice" / "cassettes"

    scorers = [ExpectedToolScorer(), ForbiddenToolScorer(), InputFieldScorer()]

    print("# tool_choice eval — D41 P0 (decision surface #2)")
    print(f"# model:         {model}")
    print(f"# dataset:       {dataset_path.relative_to(project_root)}")
    print(f"# cassette_mode: {cassette_mode}")
    print()

    results = await run_tool_choice_eval(
        dataset_path,
        scorers,
        client,
        model,
        cassette_root=cassette_root,
        cassette_mode=cassette_mode,
        case_id=resolve_manual_case_id(),
    )

    for result in results:
        _print_case(result)
    _print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
