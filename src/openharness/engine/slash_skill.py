"""Synthetic LoadSkill envelope helper — Phase 18 (D38.2 / D38.3) +
Phase 19 hotfix (D38.8 — reverses D38.3's empty-args clause).

The REPL resolver in :mod:`openharness.cli` calls
:func:`synthesize_skill_envelope` when a user types ``/<skill-name> [args]``
and ``SkillStore.get(name)`` returns a hit. The function returns 3
:class:`ConversationMessage` objects that mimic the byte-shape of an
LLM-driven ``LoadSkill`` round-trip *without* actually executing
:class:`LoadSkillTool` or running through the hook / permission chain.

D38.2 envelope schema (post-D38.8):

::

    [assistant] content=[ToolUseBlock(name="LoadSkill",
                                     input={"name": skill.name},
                                     id="synth_<rand>")]
    [user]      content=[ToolResultBlock(tool_use_id="synth_<rand>",
                                         content=skill.body)]
    [user]      content=[TextBlock(text=args or _DEFAULT_PLACEHOLDER)]

D38.8 (2026-06-07 user-time hotfix): the original D38.3 spec had a
"args empty → 2-message envelope" branch — let the LLM react to the
loaded skill body on its own with no trailing user message. Phase 18
and Phase 19 dogfoods only exercised the 3-message (args-non-empty)
path; user-time testing surfaced that thinking-mode models (qwen3.7-max
observed; likely all reasoning-mode providers) reject the 2-message
envelope with::

    {"error": {"message": "The `reasoning_content` in the thinking mode
     must be passed back to the API.", "type": "invalid_request_error"}}

The 2-message shape ends on an assistant ``tool_use`` block whose
``reasoning_content`` field never existed (we synthesized the message,
no thinking turn produced it). Reasoning-mode APIs require either:
(a) the prior thinking content, or (b) a structural break that's
clearly "new turn." Empty trailing user message gives neither.

D38.8 fix: when ``args`` is empty / whitespace-only, append a
trivial placeholder user ``TextBlock`` so the envelope is always
3 messages. The placeholder signals "user is asking" without
attempting to make up content. Behavior parity with the args-present
path; reasoning-mode providers accept the structurally-identical
3-message shape.

The ``synth_`` ID prefix is the audit marker: observability /
snapshot / hook authors can identify synth envelopes without the
:mod:`engine.slash_skill` module needing to leak into their imports.

Architecture isolation (per P18-T1 acceptance):

The module imports **only** :mod:`openharness.protocols` types +
:class:`Skill`. It does **not** import
``tools.load_skill`` / ``permissions`` / ``hooks`` /
``observability`` / ``cli`` — those concerns belong to the
*caller*'s composition root (the REPL), not the helper. Keeping
imports narrow makes the function trivially unit-testable and prevents
accidental coupling that would re-introduce the very hook / permission
side-effects D38.5 deliberately bypasses.

D38.3 (un-reversed clauses): ``skill.body`` lands verbatim in the
``ToolResultBlock`` — no ``{args}`` substitution, no template engine,
no truncation. ``args`` (when non-empty) is the trailing user message
text verbatim.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from openharness.protocols import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from openharness.protocols.content import ToolResultBlock

if TYPE_CHECKING:
    from collections.abc import Callable

    from openharness.skills.model import Skill

__all__ = [
    "DEFAULT_EMPTY_ARGS_PLACEHOLDER",
    "SYNTH_ID_PREFIX",
    "synthesize_skill_envelope",
]


SYNTH_ID_PREFIX = "synth_"

DEFAULT_EMPTY_ARGS_PLACEHOLDER = (
    "What input do you need to apply this skill? "
    "Wait for me to provide it — do not explore the filesystem to find it yourself."
)
"""Trailing user-message text used when ``args`` is empty / whitespace.

Reads to the LLM as the **user asking what input the skill needs** —
interrogative + restrictive. Earlier iterations used the imperative
``"Please apply this skill now."`` which read as "go act", and LLM
agents observed (qwen3.7-max, 2026-06-07 user-time) responded by
fanning out 10+ Read/Bash/Find calls hunting for mock data instead
of asking the user. The interrogative + explicit "wait, don't
explore" framing pivots the LLM from "act" to "ask back".

Constant is exported so tests + audit code grep it without
hard-coding the literal (per D38.8)."""


def _default_synth_id() -> str:
    return f"{SYNTH_ID_PREFIX}{uuid.uuid4().hex[:12]}"


def synthesize_skill_envelope(
    skill: Skill,
    args: str,
    *,
    tool_use_id_factory: Callable[[], str] = _default_synth_id,
) -> list[ConversationMessage]:
    """Build the 3-message envelope that injects ``skill.body`` into history.

    Returns a fresh list of three :class:`ConversationMessage` objects —
    the caller is expected to ``history.extend(envelope)``. Always 3
    messages per D38.8 (reverses D38.3's "args empty → 2 messages"
    clause, which fails on thinking-mode providers).

    When ``args.strip()`` is empty the trailing user TextBlock carries
    :data:`DEFAULT_EMPTY_ARGS_PLACEHOLDER` instead of the user's literal
    text. The placeholder gives the LLM a "user is asking" structural
    signal without trying to invent task-specific content.

    ``tool_use_id_factory`` is injected for deterministic testing; in
    production the default mints ``synth_<12 hex chars>``.
    """
    synth_id = tool_use_id_factory()

    tool_use = ToolUseBlock(
        type="tool_use",
        id=synth_id,
        name="LoadSkill",
        input={"name": skill.name},
    )
    tool_result = ToolResultBlock(
        type="tool_result",
        tool_use_id=synth_id,
        content=skill.body,
    )

    trailing_text = args if args.strip() else DEFAULT_EMPTY_ARGS_PLACEHOLDER

    return [
        ConversationMessage(role="assistant", content=[tool_use]),
        ConversationMessage(role="user", content=[tool_result]),
        ConversationMessage(role="user", content=[TextBlock(text=trailing_text)]),
    ]
