"""Eval runner — load dataset, iterate samples, run scorers, aggregate.

The async runner pipeline:

    YAML dataset → load_dataset() → list[Sample]
                                       │
                                       ▼
                            for each Sample:
                                cassetted_infer_focus_state(...) → FocusState
                                       │
                                       ▼
                            for each scorer: scorer.score(sample, output)
                                       │
                                       ▼
                            CaseResult(sample, output, list[Score])

The runner does NOT print — it returns structured results. Callers
(scripts/spike_focus_state_eval.py) handle stdout formatting.

Cassette mode (Stage 4 / D33): runner accepts optional ``cassette_root`` +
``cassette_mode`` parameters. When provided, infer calls go through
``cassetted_infer_focus_state``. Defaults preserve old behavior (LIVE).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from openharness.eval.cassette import (
    CassetteMode,
    CassetteStore,
    cassetted_infer_focus_state,
)
from openharness.eval.protocol import Sample, Score, Scorer
from openharness.protocols.messages import ConversationMessage

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api import OpenAICompatibleApiClient
    from openharness.services.focus_state import FocusState


@dataclass(frozen=True)
class CaseResult:
    """One complete eval result for one sample.

    Bundles the input (sample), what the LLM produced (output), and
    every scorer's verdict (scores). This is the unit caller renders.
    """

    sample: Sample
    output: FocusState
    scores: list[Score]


def load_dataset(path: Path) -> list[Sample]:
    """Load samples from a YAML file.

    Schema (top level dict with ``samples`` key):

    .. code-block:: yaml

        samples:
          - case_id: str
            capability: str
            shape: str
            messages:               # list of ConversationMessage as dict
              - role: user|assistant
                content:
                  - type: text|tool_use|tool_result
                    ...fields per ContentBlock subtype...
            expected_goal_keywords: [str, ...]
            capability_assertions:
              goal_must_contain: [str, ...]
              ...
            notes: str

    Messages are reconstructed via Pydantic ``model_validate`` so the
    discriminated union on ``type`` resolves the right ContentBlock subclass.
    """
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    samples: list[Sample] = []
    for entry in data["samples"]:
        messages = [ConversationMessage.model_validate(m) for m in entry["messages"]]
        samples.append(
            Sample(
                case_id=entry["case_id"],
                capability=entry["capability"],
                shape=entry["shape"],
                messages=messages,
                expected_goal_keywords=entry["expected_goal_keywords"],
                capability_assertions=entry["capability_assertions"],
                notes=entry["notes"],
            )
        )
    return samples


async def run_eval(
    dataset_path: Path,
    scorers: list[Scorer],
    client: OpenAICompatibleApiClient,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
) -> list[CaseResult]:
    """End-to-end: load dataset → for each sample run LLM + all scorers.

    Returns ``list[CaseResult]`` in dataset order. Caller aggregates.

    Cassette mode (D33):
    - ``"live"`` (default) — bypass cassette entirely
    - ``"record"`` — call LLM and save infer cassette per sample
    - ``"replay"`` — load infer cassette per sample, never call LLM;
      missing cassette raises :class:`CassetteMissingError`

    Judge LLM calls inside :class:`CapabilityLLMJudgeScorer` are
    cassetted separately — scorer must be constructed with the same
    ``cassette_store`` + ``cassette_mode`` for full reproducibility.
    """
    samples = load_dataset(dataset_path)
    store: CassetteStore | None = (
        CassetteStore(cassette_root) if cassette_root is not None else None
    )
    results: list[CaseResult] = []
    for sample in samples:
        output = await cassetted_infer_focus_state(
            case_id=sample.case_id,
            messages=sample.messages,
            api_client=client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = []
        for scorer in scorers:
            score = await scorer.score(sample, output)
            scores.append(score)
        results.append(CaseResult(sample=sample, output=output, scores=scores))
    return results
