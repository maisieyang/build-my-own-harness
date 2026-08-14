"""Helpers for building/extending conversation history.

Per D7.3 (P2-T1 Three-Axis): module-level pure functions; each returns a *new*
list, the input list is never mutated. The loop in P2-T4 will chain these:

    messages = append_user_text(messages, prompt)
    # ... stream the API response, assemble the assistant message ...
    messages = append_assistant_message(messages, assembled_content)
    tool_uses = extract_tool_uses(messages[-1])
    # ... execute tools, collect ToolResultBlocks ...
    messages = append_tool_results(messages, tool_results)

Functional style makes the loop body a sequence of expression evaluations
rather than mutation orchestration; it also gives Phase 4 compaction a clean
seam (compaction can rewrite the list without worrying about aliased references).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openharness.protocols import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from openharness.protocols.content import ToolResultBlock

if TYPE_CHECKING:
    from openharness.protocols import ContentBlock


def append_user_text(
    messages: list[ConversationMessage],
    text: str,
) -> list[ConversationMessage]:
    """Append a single-text user message to a copy of ``messages``."""
    return [
        *messages,
        ConversationMessage(role="user", content=[TextBlock(text=text)]),
    ]


def append_assistant_message(
    messages: list[ConversationMessage],
    content: list[ContentBlock],
) -> list[ConversationMessage]:
    """Append an assistant turn carrying the assembled ``content`` blocks."""
    return [
        *messages,
        ConversationMessage(role="assistant", content=content),
    ]


def append_tool_results(
    messages: list[ConversationMessage],
    results: list[ToolResultBlock],
) -> list[ConversationMessage]:
    """Append a single user message bundling every ``ToolResultBlock``.

    The Anthropic Messages API requires all tool_result blocks responding to
    one assistant tool_use turn to live in a *single* following user message,
    in the same order as the originating tool_use blocks.
    """
    bundled: list[ContentBlock] = list(results)
    return [
        *messages,
        ConversationMessage(role="user", content=bundled),
    ]


def extract_tool_uses(message: ConversationMessage) -> list[ToolUseBlock]:
    """Return every :class:`ToolUseBlock` in ``message.content``.

    The loop calls this on the just-completed assistant message to drive
    per-turn tool dispatch. Empty list when the message has none.
    """
    return [block for block in message.content if isinstance(block, ToolUseBlock)]


def drop_oldest_tool_pair(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """Drop the oldest ``assistant(tool_use) + user`` message pair.

    Used by P4-T3 Layer 2 reactive truncation: when the LLM provider
    returns a "prompt too long" error, the engine calls this helper to
    shrink ``messages`` while keeping the conversation **well-formed**
    (Anthropic protocol forbids an assistant ``tool_use`` block without
    a matching ``user`` ``tool_result`` following it — so we drop the
    pair as a unit).

    Algorithm:

    - Scan from the beginning;find the first assistant message that
      contains at least one :class:`ToolUseBlock`.
    - If the immediately following message is a user message, drop both
      as a pair.
    - Otherwise (no companion user message — caller is mid-turn) leave
      messages alone.

    Returns:
        A new list. If no eligible pair is found, the input is returned
        unchanged — the caller is expected to detect this and re-raise
        the underlying ``PromptTooLongFailure`` rather than retry forever.
    """
    for i in range(len(messages) - 1):
        msg = messages[i]
        next_msg = messages[i + 1]
        if (
            msg.role == "assistant"
            and any(isinstance(b, ToolUseBlock) for b in msg.content)
            and next_msg.role == "user"
        ):
            return messages[:i] + messages[i + 2 :]
    return list(messages)


# ---------------------------------------------------------------------------
# P12-T1 (D30.6): turn_metadata producer for snapshot writers
# ---------------------------------------------------------------------------


_FILE_TOOL_NAMES = frozenset({"Read", "Write", "Edit"})
_MAX_VERIFIED_WORK_PER_TURN = 10
_MAX_RECENT_FILES = 10
_VERIFIED_WORK_SNIPPET_CHARS = 60


def collect_turn_metadata(
    messages: list[ConversationMessage],
    prior_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``tool_metadata`` dict consumed by the snapshot writer.

    Deterministic, with no LLM call. The engine stores the result in
    the per-turn snapshot.

    Schema (locked):

    - ``recent_files``: list[str] — paths from ``Read``/``Write``/
      ``Edit`` ``ToolUseBlock`` input fields (``path`` or ``file_path``).
      Deduped within turn. When ``prior_metadata`` is supplied,
      prior turn's recent_files are extended (new entries appended
      after old), then deduped + capped at ``_MAX_RECENT_FILES`` MOST
      RECENT entries — so a long conversation accumulates the last
      10 touched files across turns.
    - ``verified_work``: list[str] — summaries of successful
      tool_results from THIS TURN only (``"{tool_name}: {content[:60]}"``).
      Errors excluded. Capped at ``_MAX_VERIFIED_WORK_PER_TURN``.
      Per-turn (not accumulating) because verified work is meant
      as "what just happened" context, not a session-long audit log.
    - ``task_focus_state``: ``{"goal": None, "next_step": None}`` —
      placeholder dict; Phase 13 evaluates whether to populate via
      LLM. Phase 12 ships the schema field stably with None values
      rather than omitting it.

    ``prior_metadata`` is optional — passed at resume time so the
    rebuilt context's first turn extends the previous session's
    ``recent_files`` rather than starting from scratch. When None,
    treated as an empty starting dict.

    Pure function: input messages list is never mutated; new dict
    returned each call.
    """
    # --- recent_files: scan tool_use blocks for file tool paths ---
    recent_files: list[str] = []
    seen_files: set[str] = set()

    # Seed from prior_metadata when resuming
    if prior_metadata is not None:
        prior_files = prior_metadata.get("recent_files", [])
        if isinstance(prior_files, list):
            for p in prior_files:
                if isinstance(p, str) and p not in seen_files:
                    seen_files.add(p)
                    recent_files.append(p)

    # Build a lookup: tool_use_id → tool_name so we can correlate
    # tool_results back to the tool that produced them.
    tool_use_lookup: dict[str, ToolUseBlock] = {}
    for msg in messages:
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                tool_use_lookup[block.id] = block
                if block.name in _FILE_TOOL_NAMES:
                    path = block.input.get("path") or block.input.get("file_path")
                    if isinstance(path, str) and path not in seen_files:
                        seen_files.add(path)
                        recent_files.append(path)

    # Cap at last N (preserve newest by dropping from the front)
    if len(recent_files) > _MAX_RECENT_FILES:
        recent_files = recent_files[-_MAX_RECENT_FILES:]

    # --- verified_work: summarize successful tool_results ---
    verified_work: list[str] = []
    for msg in messages:
        for block in msg.content:
            if not isinstance(block, ToolResultBlock):
                continue
            if block.is_error:
                continue
            tool_use = tool_use_lookup.get(block.tool_use_id)
            tool_name = tool_use.name if tool_use is not None else "unknown"
            content_str = block.content if isinstance(block.content, str) else str(block.content)
            snippet = content_str[:_VERIFIED_WORK_SNIPPET_CHARS]
            # Collapse newlines so the one-liner stays one line
            snippet = snippet.replace("\n", " ").replace("\r", " ")
            verified_work.append(f"{tool_name}: {snippet}")

    if len(verified_work) > _MAX_VERIFIED_WORK_PER_TURN:
        verified_work = verified_work[-_MAX_VERIFIED_WORK_PER_TURN:]

    return {
        "recent_files": recent_files,
        "verified_work": verified_work,
        "task_focus_state": {"goal": None, "next_step": None},
    }
