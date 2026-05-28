"""LLM-authored ``task_focus_state`` — P13-T3 (D31.7).

Phase 12 ships ``tool_metadata.task_focus_state`` as a placeholder
``{"goal": None, "next_step": None}`` dict. Phase 13 lets users
opt in to having the LLM author it: at turn end (after extract,
before snapshot write) the harness fires a short secondary LLM
call asking for the current goal + next step in JSON.

**Design contract** (locked at D31.7):

- This is the 7th consumer of ``services/summarize.py`` — that
  primitive must NOT be modified to support focus-state. If it
  needs modification, redesign focus_state.py instead. Verified
  by P13-T4 invariant test.
- Default OFF (opt-in via ``OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE``
  + ``--llm-focus-state`` CLI). Preserves Phase 12 zero-cost
  behavior for users who don't care.
- Failure-isolated: any exception (timeout, JSON parse, LLM API
  failure, even programming errors) returns
  ``FocusState(goal=None, next_step=None)`` + WARN log. Never
  blocks the turn.
- Snapshot writer stores the result verbatim in
  ``task_focus_state`` (replacing the Phase 12 None placeholder).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.observability import get_logger
from openharness.services.summarize import summarize

if TYPE_CHECKING:
    from openharness.api import SupportsStreamingMessages
    from openharness.protocols.messages import ConversationMessage

_logger = get_logger("focus_state")


@dataclass(frozen=True)
class FocusState:
    """LLM-inferred goal + next_step for the current turn.

    Either field can be None when the LLM couldn't infer (or when
    we fell back due to a failure). Snapshot writer stores this
    as ``{"goal": ..., "next_step": ...}`` in ``tool_metadata``.
    """

    goal: str | None
    next_step: str | None

    @classmethod
    def empty(cls) -> FocusState:
        """The fallback when the LLM call fails or returns malformed JSON."""
        return cls(goal=None, next_step=None)

    def to_dict(self) -> dict[str, str | None]:
        return {"goal": self.goal, "next_step": self.next_step}


FOCUS_STATE_SYSTEM_PROMPT = """\
You watch a conversation between a user and an AI assistant. Your
job is to summarize what the assistant is currently trying to do.

Return exactly one JSON object with two fields: "goal" (one
sentence naming the user's current ask) and "next_step" (one
sentence naming what the assistant should do next). Both strings,
no markdown, no code fences, no explanation outside the JSON.

Example output:
{"goal": "fix the failing pytest in test_foo.py", "next_step": "rerun pytest after the fix and verify GREEN"}"""


def _build_user_message(
    messages: list[ConversationMessage],
    prior_focus_state: FocusState | None,
) -> ConversationMessage:
    """Construct the user-message turn fed to ``summarize()``.

    Includes a short serialization of the latest turn's messages
    plus the prior focus state (if known) for continuity. Keeps
    body brief — the LLM doesn't need full message bodies, just
    enough to infer what's being worked on.
    """
    from openharness.protocols.content import TextBlock
    from openharness.protocols.messages import ConversationMessage as _CM

    parts: list[str] = []
    if prior_focus_state is not None and (
        prior_focus_state.goal is not None or prior_focus_state.next_step is not None
    ):
        parts.append(
            f"Prior focus state: goal={prior_focus_state.goal!r}, "
            f"next_step={prior_focus_state.next_step!r}"
        )
        parts.append("")

    parts.append("Recent conversation turn (last 6 messages):")
    parts.append("")
    for msg in messages[-6:]:
        if not msg.content:
            continue
        block = msg.content[0]
        snippet: str
        if hasattr(block, "text"):
            snippet = block.text
        elif hasattr(block, "name"):  # tool_use
            snippet = f"[tool] {block.name}"
        elif hasattr(block, "content"):  # tool_result
            content = block.content
            snippet = f"[result] {str(content)[:120]}"
        else:
            snippet = "(unknown block)"
        # Cap each snippet at 240 chars + collapse newlines
        snippet = snippet.replace("\n", " ").replace("\r", " ")[:240]
        parts.append(f"[{msg.role}] {snippet}")

    parts.append("")
    parts.append("Return the JSON now.")

    return _CM(role="user", content=[TextBlock(text="\n".join(parts))])


async def infer_focus_state(
    *,
    messages: list[ConversationMessage],
    api_client: SupportsStreamingMessages,
    model: str,
    prior_focus_state: FocusState | None = None,
    timeout_seconds: float = 15.0,
    max_tokens: int = 256,
) -> FocusState:
    """Ask the LLM for the current ``FocusState`` given recent messages.

    7th consumer of :func:`openharness.services.summarize.summarize`.
    Never raises — all failures return :meth:`FocusState.empty` +
    WARN log. The engine call site (P13 T3) awaits this; failure
    fallback preserves the Phase 12 placeholder shape.

    Skip when ``messages`` has <2 entries (nothing meaningful to
    infer from a single user message).
    """
    if len(messages) < 2:
        return FocusState.empty()

    user_msg = _build_user_message(messages, prior_focus_state)

    try:
        raw = await summarize(
            messages=[user_msg],
            system_prompt=FOCUS_STATE_SYSTEM_PROMPT,
            model=model,
            api_client=api_client,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            tools_disabled=True,
        )
    except Exception as exc:
        _logger.warning(
            "focus_state_inference_failed",
            phase="llm_call",
            error=str(exc),
        )
        return FocusState.empty()

    return _parse_focus_state_response(raw)


def _parse_focus_state_response(raw: str) -> FocusState:
    """Parse the LLM's JSON output into a :class:`FocusState`.

    Tolerant: strips markdown code fences if the LLM added them
    (despite the prompt asking for raw JSON). Returns
    :meth:`FocusState.empty` on any parse / schema failure +
    WARN log so failures are observable.
    """
    text = raw.strip()
    # Strip ```json ... ``` fence if present
    if text.startswith("```"):
        # Drop first line + last line
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _logger.warning(
            "focus_state_inference_failed",
            phase="json_parse",
            error=str(exc),
        )
        return FocusState.empty()

    if not isinstance(data, dict):
        _logger.warning(
            "focus_state_inference_failed",
            phase="json_schema",
            error=f"root not a dict: {type(data).__name__}",
        )
        return FocusState.empty()

    goal_val = data.get("goal")
    next_step_val = data.get("next_step")

    # Both fields must be string or None; reject other types
    goal = goal_val if isinstance(goal_val, str) else None
    next_step = next_step_val if isinstance(next_step_val, str) else None

    return FocusState(goal=goal, next_step=next_step)
