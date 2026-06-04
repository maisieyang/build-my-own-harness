"""Eval substrate — Stage 1 MVP.

Capability-anchored prompt eval for ``services/`` layer LLM-driven
primitives. Started from focus_state.py spike (Day 1 experiment, see
``docs/ideas/eval-experiment-day1-focus-state.md``).

Public API:

    from openharness.eval import Sample, Score, Scorer
    from openharness.eval.scorers import (
        ParseOkScorer,
        GoalKeywordMatchScorer,
        CapabilityAssertionsScorer,
    )
    from openharness.eval.runner import run_eval, load_dataset, CaseResult

Internal modules:
- ``openharness.eval.protocol`` — ``Sample`` / ``Score`` / ``Scorer``
- ``openharness.eval.scorers`` — programmatic scorer implementations
- ``openharness.eval.runner``  — async loader + iterator + aggregator

Scope (MVP, Stage 1):
- Focus_state-only (Sample messages → FocusState output)
- Programmatic scorers only (no LLM-judge — Stage 3)
- LIVE mode only (no cassette — Stage 4)
- YAML dataset (not extended — Stage 2 grows to 30+ cases)
"""

from __future__ import annotations

from openharness.eval.protocol import Sample, Score, Scorer

__all__ = [
    "Sample",
    "Score",
    "Scorer",
]
