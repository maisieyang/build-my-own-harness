"""Eval substrate protocol — Sample / Score / Scorer.

Three data shapes:

- :class:`Sample` — one eval case (input messages + expected behavior).
  Capability-anchored design: each Sample declares the prompt capability
  it probes + pre-registered pass/fail assertions.
- :class:`Score` — one dimension of one Sample's evaluation result.
  Multi-dim aggregation = list of Score, never collapsed to single number
  (per playbook §四 4.4).
- :class:`Scorer` — Protocol an evaluator implements. ``async def score``
  takes (sample, output) → Score. Sync scorers wrap themselves in async
  trivially; LLM-judge scorers (Stage 3) need the async signature.

Capability IDs (T1-T8 from Day 1 experiment, see eval-experiment-day1-focus-state.md):
  T1 = Tool name 抽象成 user-side 语言
  T2 = Tool input → 推 user 意图
  T5 = Tool chain → 抓 high-level
  T6 = Tool-only no text → 仍能推 goal
  T7 = Edit + user 字面 done → 识别 follow-up

``capability_assertions`` schema (each key optional, all substring matches
are case-insensitive):
  goal_must_contain               — at least one substring must appear in goal
  goal_must_NOT_contain           — none of these substrings can be in goal
  next_step_must_contain_any_of   — at least one substring in next_step
  next_step_must_NOT_contain      — none of these in next_step

The semantic ceiling of substring assertions (Day 1 Stage 0f finding):
substring rules cannot distinguish "symbol-as-task-subject" (correct fail)
from "symbol-as-contextual-reference" (false-positive fail). LLM-judge
scorer (Stage 3) takes over this dimension.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openharness.protocols.messages import ConversationMessage
    from openharness.services.focus_state import FocusState


@dataclass(frozen=True)
class Sample:
    """One hand-picked eval case probing a specific prompt capability.

    Capability-anchored case (not shape-anchored) — designer pre-registers
    pass/fail criteria via ``capability_assertions`` before running, making
    each Sample a falsification-criteria-bearing hypothesis about prompt
    behavior.
    """

    case_id: str
    capability: str
    shape: str
    messages: list[ConversationMessage]
    expected_goal_keywords: list[str]
    capability_assertions: dict[str, list[str]]
    notes: str


@dataclass(frozen=True)
class Score:
    """One dimension of one Sample's evaluation.

    ``value`` is float (0.0 / 1.0 for binary) OR str (state enum for
    multi-state dims like parse_quality). Caller aggregates per-dim;
    never collapse multi-dim into single number (Goodhart's law).

    ``reason`` is human-readable diagnostic — always populated even on
    pass, so the eval result is debugger-friendly without re-running.
    """

    dim: str
    value: float | str
    reason: str
    case_id: str


@runtime_checkable
class Scorer(Protocol):
    """A scorer takes a Sample and the LLM output and produces one Score.

    All scorers are async — sync scorers wrap themselves (trivial) and
    LLM-judge scorers (Stage 3) need real async for the judge call.

    ``dim`` is a stable identifier for the score dimension. Per-sample
    dim variation (e.g., capability_T1 / capability_T2) is encoded in
    the returned ``Score.dim``, not the scorer's ``dim`` attribute —
    the latter is the scorer's *category*.
    """

    @property
    def dim(self) -> str: ...

    async def score(self, sample: Sample, output: FocusState) -> Score: ...
