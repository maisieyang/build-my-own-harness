"""error_feedback eval — D41 P0 entry script (decision surface #3, A5+A6).

Drives the error_feedback consumer (loader, fixture catalog, single-shot
infer, two `=`-grade scorers) over ``evals/error_feedback/dataset.yaml``
against the model configured by ``OPENHARNESS_MODEL`` or the project ``.env``.
Mirrors spike_tool_choice_eval.

Run::

    uv run python scripts/spike_error_feedback_eval.py
    # re-record cassettes:
    OPENHARNESS_EVAL_MODE=record uv run python scripts/spike_error_feedback_eval.py
    # replay from cassettes (no LLM cost):
    OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_error_feedback_eval.py
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
from openharness.eval.error_feedback import ErrorFeedbackCaseResult, run_error_feedback_eval
from openharness.eval.error_feedback_scorers import (
    FabricatedGuidanceScorer,
    FollowupScorer,
    VerbatimRetryScorer,
)

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
    """Resolve replay cassette identity without loading API credentials."""
    configured = os.environ.get("OPENHARNESS_MODEL")
    if configured is None:
        configured = dotenv_values(project_root / ".env").get("OPENHARNESS_MODEL")
    model = configured.strip() if isinstance(configured, str) else ""
    if not model:
        raise SystemExit(
            "OPENHARNESS_MODEL is required for replay; configure it in the project .env"
        )
    return model


def _print_case(result: ErrorFeedbackCaseResult) -> None:
    calls = (
        ", ".join(
            f"{tu.name}({', '.join(map(str, tu.input.values()))[:60]})"
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


def _print_summary(results: list[ErrorFeedbackCaseResult]) -> None:
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
        # Replay never calls the API, so it reads only the configured model
        # needed to select a cassette and does not require API credentials.
        client = None
        model = _resolve_replay_model(project_root)
    else:
        settings = Settings()  # type: ignore[call-arg]
        sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
        client = OpenAICompatibleApiClient(sdk=sdk, extra_body=settings.extra_body)
        model = settings.model

    dataset_path = project_root / "evals" / "error_feedback" / "dataset.yaml"
    cassette_root = project_root / "evals" / "error_feedback" / "cassettes"

    scorers = [VerbatimRetryScorer(), FollowupScorer(), FabricatedGuidanceScorer()]

    print("# error_feedback eval — D41 P0 (decision surface #3 A5+A6)")
    print(f"# model:         {model}")
    print(f"# dataset:       {dataset_path.relative_to(project_root)}")
    print(f"# cassette_mode: {cassette_mode}")
    print()

    results = await run_error_feedback_eval(
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
