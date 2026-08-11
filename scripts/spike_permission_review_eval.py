"""Run the production permission reviewer over its ratified exact-request set."""

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
from openharness.eval.permission_review import (
    PermissionReviewCaseResult,
    run_permission_review_eval,
)
from openharness.eval.permission_review_scorers import (
    PermissionVerdictScorer,
    ReviewLifecycleScorer,
)

if TYPE_CHECKING:
    from openharness.eval.cassette import CassetteMode


def _resolve_cassette_mode() -> CassetteMode:
    return resolve_manual_cassette_mode()


def _print_case(result: PermissionReviewCaseResult) -> None:
    print(f"## {result.sample.case_id} [{result.sample.capability}]")
    print(f"   decision={result.output.decision.value} review_called={result.output.review_called}")
    for score in result.scores:
        mark = "PASS" if score.value == 1.0 else "FAIL"
        print(f"   {mark} {score.dim}: {score.reason}")
    print()


def _print_summary(results: list[PermissionReviewCaseResult]) -> None:
    per_dim: dict[str, list[float]] = defaultdict(list)
    for result in results:
        for score in result.scores:
            if isinstance(score.value, float):
                per_dim[score.dim].append(score.value)
    print("# Summary")
    for dim, values in per_dim.items():
        print(f"  {dim}: {int(sum(values))}/{len(values)}")
    all_pass = sum(1 for result in results if all(s.value == 1.0 for s in result.scores))
    print(f"  cases all-dims-pass: {all_pass}/{len(results)}")


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    mode = _resolve_cassette_mode()
    if mode == "replay":
        client = None
        model = resolve_manual_model(root)
    else:
        settings = Settings()  # type: ignore[call-arg]
        sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
        client = OpenAICompatibleApiClient(sdk=sdk, extra_body=settings.extra_body)
        model = settings.model

    dataset = root / "evals" / "permission_review" / "dataset.yaml"
    cassettes = root / "evals" / "permission_review" / "cassettes"
    scorers = [PermissionVerdictScorer(), ReviewLifecycleScorer()]

    print("# permission_review eval — G1/S3 typed exact authorization")
    print(f"# model:         {model}")
    print(f"# cassette_mode: {mode}\n")

    results = await run_permission_review_eval(
        dataset,
        scorers,
        client,
        model,
        cassette_root=cassettes,
        cassette_mode=mode,
        case_id=resolve_manual_case_id(),
    )
    for result in results:
        _print_case(result)
    _print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
