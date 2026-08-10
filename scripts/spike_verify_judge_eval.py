"""verify_judge eval — 决策面 #1 / B3(D45.2),verify 判官元评估。

Drives the verify_judge consumer over ``evals/verify_judge/dataset.yaml``:
真调生产 run_semantic_verification,判其 verdict 与人工金标的一致率
(VerdictAgreementScorer,纯 `=`)。含抗注入对抗样本。Mirrors
spike_memory_compact_eval.

The model comes from ``OPENHARNESS_MODEL`` or the project ``.env``.

Run::

    uv run python scripts/spike_verify_judge_eval.py
    OPENHARNESS_EVAL_MODE=record uv run python scripts/spike_verify_judge_eval.py
    OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_verify_judge_eval.py
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dotenv import dotenv_values
from openai import AsyncOpenAI

from openharness.api import OpenAICompatibleApiClient
from openharness.config.settings import Settings
from openharness.eval.verify_judge import VerifyJudgeCaseResult, run_verify_judge_eval
from openharness.eval.verify_judge_scorers import VerdictAgreementScorer

if TYPE_CHECKING:
    from openharness.eval.cassette import CassetteMode

def _resolve_cassette_mode() -> CassetteMode:
    raw = os.environ.get("OPENHARNESS_EVAL_MODE", "live").lower().strip()
    if raw not in ("live", "record", "replay"):
        raise SystemExit(
            f"Invalid OPENHARNESS_EVAL_MODE={raw!r}; expected one of live / record / replay"
        )
    return cast("CassetteMode", raw)


def _resolve_replay_model(project_root: Path) -> str:
    configured = os.environ.get("OPENHARNESS_MODEL")
    if configured is None:
        configured = dotenv_values(project_root / ".env").get("OPENHARNESS_MODEL")
    model = configured.strip() if isinstance(configured, str) else ""
    if not model:
        raise SystemExit(
            "OPENHARNESS_MODEL is required for replay; configure it in the project .env"
        )
    return model


def _print_case(result: VerifyJudgeCaseResult) -> None:
    s = result.sample
    gold = "pass" if s.gold_passed else "fail"
    judge = "pass" if result.output.passed else "fail"
    tag = " [INJECTION]" if s.is_injection else ""
    print(f"## {s.case_id} [{s.capability}]{tag}")
    print(f"   gold={gold}  judge={judge}")
    for score in result.scores:
        mark = "PASS" if score.value == 1.0 else "FAIL"
        print(f"   {mark} {score.dim}: {score.reason}")
    print()


def _print_summary(results: list[VerifyJudgeCaseResult]) -> None:
    per_dim: dict[str, list[float]] = defaultdict(list)
    for result in results:
        for score in result.scores:
            if isinstance(score.value, float):
                per_dim[score.dim].append(score.value)
    print("# Summary")
    for dim, values in per_dim.items():
        print(f"  {dim}: {int(sum(values))}/{len(values)}")
    # 抗注入子集单独统计(画像用)
    inj = [r for r in results if r.sample.is_injection]
    inj_ok = sum(1 for r in inj if all(s.value == 1.0 for s in r.scores))
    if inj:
        print(f"  injection-resisted: {inj_ok}/{len(inj)}")
    all_pass = sum(1 for r in results if all(s.value == 1.0 for s in r.scores))
    print(f"  cases all-dims-pass: {all_pass}/{len(results)}")


async def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    cassette_mode = _resolve_cassette_mode()
    if cassette_mode == "replay":
        # Replay selects a cassette from project configuration without loading
        # provider credentials or calling the API.
        client = None
        model = _resolve_replay_model(project_root)
    else:
        settings = Settings()  # type: ignore[call-arg]
        sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
        client = OpenAICompatibleApiClient(sdk=sdk, extra_body=settings.extra_body)
        model = settings.model

    dataset_path = project_root / "evals" / "verify_judge" / "dataset.yaml"
    cassette_root = project_root / "evals" / "verify_judge" / "cassettes"

    scorers = [VerdictAgreementScorer()]

    print("# verify_judge eval — 决策面 #1 / B3 (verify 判官元评估)")
    print(f"# model:         {model}")
    print(f"# dataset:       {dataset_path.relative_to(project_root)}")
    print(f"# cassette_mode: {cassette_mode}")
    print()

    results = await run_verify_judge_eval(
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
