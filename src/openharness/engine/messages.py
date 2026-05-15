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

from typing import TYPE_CHECKING

from openharness.protocols import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)

if TYPE_CHECKING:
    from openharness.protocols import ContentBlock, ToolResultBlock


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
