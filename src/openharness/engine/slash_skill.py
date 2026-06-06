"""Synthetic LoadSkill envelope helper — Phase 18 (D38.2 / D38.3).

The REPL resolver in :mod:`openharness.cli` calls
:func:`synthesize_skill_envelope` when a user types ``/<skill-name> [args]``
and ``SkillStore.get(name)`` returns a hit. The function returns 2 or 3
:class:`ConversationMessage` objects that mimic the byte-shape of an
LLM-driven ``LoadSkill`` round-trip *without* actually executing
:class:`LoadSkillTool` or running through the hook / permission chain.

D38.2 envelope schema (locked):

::

    [assistant] content=[ToolUseBlock(name="LoadSkill",
                                     input={"name": skill.name},
                                     id="synth_<rand>")]
    [user]      content=[ToolResultBlock(tool_use_id="synth_<rand>",
                                         content=skill.body)]
    [user]      content=[TextBlock(text=args)]   # 仅 args.strip() 非空

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

D38.3: ``skill.body`` lands verbatim in the ``ToolResultBlock`` — no
``{args}`` substitution, no template engine, no truncation. ``args`` is
the trailing user message text *only*, mirroring "LLM auto-called
LoadSkill, then user typed a follow-up" verbatim.
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

__all__ = ["SYNTH_ID_PREFIX", "synthesize_skill_envelope"]


SYNTH_ID_PREFIX = "synth_"


def _default_synth_id() -> str:
    return f"{SYNTH_ID_PREFIX}{uuid.uuid4().hex[:12]}"


def synthesize_skill_envelope(
    skill: Skill,
    args: str,
    *,
    tool_use_id_factory: Callable[[], str] = _default_synth_id,
) -> list[ConversationMessage]:
    """Build the 2/3-message envelope that injects ``skill.body`` into history.

    Returns a fresh list of :class:`ConversationMessage` — the caller is
    expected to ``history.extend(envelope)``. Number of messages:

    - ``args.strip() == ""`` → 2 messages (assistant tool_use + user tool_result)
    - otherwise              → 3 messages (… + user TextBlock(args))

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

    envelope: list[ConversationMessage] = [
        ConversationMessage(role="assistant", content=[tool_use]),
        ConversationMessage(role="user", content=[tool_result]),
    ]

    if args.strip():
        envelope.append(ConversationMessage(role="user", content=[TextBlock(text=args)]))

    return envelope
