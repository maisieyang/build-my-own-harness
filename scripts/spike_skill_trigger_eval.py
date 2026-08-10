"""skill_trigger eval — D41 P0 entry script (decision surface #5, C1).

Drives the skill_trigger consumer (loader, fixture catalog, single-shot
infer, two `=`-grade scorers) over ``evals/skill_trigger/dataset.yaml``
against the currently-configured model. Mirrors spike_tool_choice_eval.

Reference policy (dataset_card declaration 4): qwen-max. Runs on other
models are information, not gate signals.

Run::

    OPENHARNESS_EVAL_MODE=live uv run python scripts/spike_skill_trigger_eval.py
    # re-record cassettes:
    OPENHARNESS_EVAL_MODE=record uv run python scripts/spike_skill_trigger_eval.py
    # replay from cassettes (no LLM cost):
    OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_skill_trigger_eval.py
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
from openharness.eval.skill_trigger import SkillTriggerCaseResult, run_skill_trigger_eval
from openharness.eval.skill_trigger_scorers import SlugSelectionScorer, TriggerDecisionScorer

if TYPE_CHECKING:
    from openharness.eval.cassette import CassetteMode


def _resolve_cassette_mode() -> CassetteMode:
    return resolve_manual_cassette_mode()


def _print_case(result: SkillTriggerCaseResult) -> None:
    calls = (
        ", ".join(
            f"{tu.name}({tu.input.get('name', '')})" if tu.name == "LoadSkill" else tu.name
            for tu in result.output.tool_uses
        )
        or "(no tool call)"
    )
    print(f"## {result.sample.case_id} [{result.sample.capability}]")
    print(f"   calls: {calls}")
    for score in result.scores:
        mark = "PASS" if score.value == 1.0 else "FAIL"
        print(f"   {mark} {score.dim}: {score.reason}")
    print()


def _print_summary(results: list[SkillTriggerCaseResult]) -> None:
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

    dataset_path = project_root / "evals" / "skill_trigger" / "dataset.yaml"
    cassette_root = project_root / "evals" / "skill_trigger" / "cassettes"

    scorers = [TriggerDecisionScorer(), SlugSelectionScorer()]

    print("# skill_trigger eval — D41 P0 (decision surface #5 / C1)")
    print(f"# model:         {model}")
    print(f"# dataset:       {dataset_path.relative_to(project_root)}")
    print(f"# cassette_mode: {cassette_mode}")
    print()

    results = await run_skill_trigger_eval(
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
