"""L5 goal decomposer — turns a big goal into an ordered array of sub-goal
strings via a single ``summarize()`` call.

Same call shape as L3''s semantic gate (``verification/semantic_gate.py``):
markdown-fence stripping, ``json.loads``, fail-closed on any judge-call or
parse failure. Never falls back to silently running the original goal
undecomposed -- that would execute something different from what the user
asked for while looking like decomposition succeeded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.protocols import ConversationMessage, TextBlock
from openharness.services.structured_response import strip_markdown_fence
from openharness.services.summarize import summarize

if TYPE_CHECKING:
    from openharness.api.client import SupportsStreamingMessages

_MAX_SUB_GOALS = 8

_DECOMPOSER_SYSTEM_PROMPT = """You are a goal decomposer.

Given a goal, break it into an ordered list of smaller sub-goals that
together accomplish the original goal. Each sub-goal should be a
self-contained instruction that can be executed on its own, in order.

Output EXACTLY one JSON array of strings on a single line, no markdown
fence, no other text:
["first sub-goal", "second sub-goal", ...]"""


@dataclass(frozen=True)
class DecomposeResult:
    """Outcome of an LLM-driven goal decomposition -- fail-closed shape
    structurally similar to :class:`~openharness.verification.semantic_gate.SemanticGateResult`
    (both expose ``feedback: str``)."""

    ok: bool
    sub_goals: tuple[str, ...]
    feedback: str


async def decompose_goal(
    goal: str,
    *,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int = 512,
    timeout_seconds: float = 30.0,
) -> DecomposeResult:
    """Ask an LLM to break ``goal`` into an ordered array of sub-goal
    strings. Fail-closed on any call, parse, or schema failure."""
    payload = f"Goal: {goal}\n\nOutput the JSON array of sub-goals."

    try:
        raw = await summarize(
            messages=[ConversationMessage(role="user", content=[TextBlock(text=payload)])],
            system_prompt=_DECOMPOSER_SYSTEM_PROMPT,
            model=model,
            api_client=api_client,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            tools_disabled=True,
        )
    except Exception as exc:  # decomposer call must never crash the caller
        return DecomposeResult(
            ok=False,
            sub_goals=(),
            feedback=f"decomposer call failed: {type(exc).__name__}: {str(exc)[:120]}",
        )

    if not raw.strip():
        return DecomposeResult(
            ok=False, sub_goals=(), feedback="decomposer returned an empty response"
        )

    text = strip_markdown_fence(raw.strip())

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = raw[:120].replace("\n", " ")
        return DecomposeResult(
            ok=False,
            sub_goals=(),
            feedback=f"decomposer response could not be parsed as JSON ({exc.msg}); raw={preview!r}",
        )

    if not isinstance(data, list):
        return DecomposeResult(
            ok=False,
            sub_goals=(),
            feedback=f"decomposer response was not a JSON array (got {type(data).__name__})",
        )

    if not data:
        return DecomposeResult(
            ok=False, sub_goals=(), feedback="decomposer returned an empty array"
        )

    if len(data) > _MAX_SUB_GOALS:
        return DecomposeResult(
            ok=False,
            sub_goals=(),
            feedback=f"decomposer returned {len(data)} sub-goals, exceeding the cap of {_MAX_SUB_GOALS}",
        )

    if not all(isinstance(item, str) for item in data):
        return DecomposeResult(
            ok=False,
            sub_goals=(),
            feedback="decomposer response contained a non-string sub-goal",
        )

    if not all(item.strip() for item in data):
        return DecomposeResult(
            ok=False,
            sub_goals=(),
            feedback="decomposer response contained an empty or whitespace-only sub-goal",
        )

    return DecomposeResult(
        ok=True,
        sub_goals=tuple(data),
        feedback=f"decomposed into {len(data)} sub-goals",
    )
