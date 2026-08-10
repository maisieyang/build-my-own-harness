"""memory_read eval — 决策面 #4 / C4,memory READ 自选决策。

Drives the memory_read consumer over ``evals/memory_read/dataset.yaml``:
生产 prompt(`## Memory` 规则+索引)+ Read 工具,判模型自选 Read 决策与选择
(ReadDecisionScorer + MemorySelectionScorer,tool-call 存在性硬 oracle)。
Mirrors spike_skill_trigger_eval.

Reference policy (dataset_card declaration 4): qwen-max. 他模型 run 是
information,非 gate signal。

Run::

    OPENHARNESS_EVAL_MODE=live uv run python scripts/spike_memory_read_eval.py
    OPENHARNESS_EVAL_MODE=record uv run python scripts/spike_memory_read_eval.py
    OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_memory_read_eval.py
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
from openharness.eval.memory_read import MemoryReadCaseResult, run_memory_read_eval
from openharness.eval.memory_read_scorers import MemorySelectionScorer, ReadDecisionScorer

if TYPE_CHECKING:
    from openharness.eval.cassette import CassetteMode


def _resolve_cassette_mode() -> CassetteMode:
    return resolve_manual_cassette_mode()


def _print_case(result: MemoryReadCaseResult) -> None:
    s = result.sample
    reads = [str(tu.input.get("path", "")) for tu in result.output.tool_uses if tu.name == "Read"]
    exp = s.expected_read_slug or "(none — restraint)"
    print(f"## {s.case_id} [{s.capability}]")
    print(f"   expected={exp}  reads={reads or '(none)'}")
    for score in result.scores:
        mark = "PASS" if score.value == 1.0 else "FAIL"
        print(f"   {mark} {score.dim}: {score.reason}")
    print()


def _print_summary(results: list[MemoryReadCaseResult]) -> None:
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

    dataset_path = project_root / "evals" / "memory_read" / "dataset.yaml"
    cassette_root = project_root / "evals" / "memory_read" / "cassettes"

    scorers = [ReadDecisionScorer(), MemorySelectionScorer()]

    print("# memory_read eval — 决策面 #4 / C4 (memory READ 自选决策)")
    print(f"# model:         {model}")
    print(f"# dataset:       {dataset_path.relative_to(project_root)}")
    print(f"# cassette_mode: {cassette_mode}")
    print()

    results = await run_memory_read_eval(
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
