"""Compact L0-L4 escalation pipeline — P11-T3.

Per ``decisions/26-phase-11-boundary.md`` D29.3: 4-layer escalation
that stacks on top of Phase 4's L1 microcompact + reactive PTL
retry. Each layer fires **only if the prior layer didn't free
enough tokens** to drop below the threshold.

| Layer | Mechanism | LLM call? |
|---|---|---|
| L0 | Token estimation + threshold check | ❌ |
| L1 | Microcompact (PostToolUse hook clears old tool_result) | ❌ (Phase 4) |
| L2 | Context collapse (head/tail truncate long bodies) | ❌ |
| L3 | Session memory reuse (read 5-slot checkpoint, splice) | ❌ |
| L4 | Full compact (9-slot LLM summary) | ✅ |

The substrate is the :func:`openharness.services.summarize.summarize`
primitive — only L4 calls it. L2 + L3 are deterministic byte-level
transforms that "pre-pay" the cost of L4: if either succeeds, we
skip the LLM call entirely.

**Trade-off note** (D29.3): the 9-slot summary schema (in
``_L4_COMPACT_SYSTEM_PROMPT``) is copied **verbatim from HKUDS
upstream** per the boundary doc sub-decision. Production-validated;
revisit in Phase 11 retro if slot choices cause friction.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.compaction.tokenize import count_tokens
from openharness.protocols.content import (
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.protocols.messages import ConversationMessage
from openharness.services.summarize import summarize

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api.client import SupportsStreamingMessages


# ---------------------------------------------------------------------------
# Tuning constants — match HKUDS upstream
# ---------------------------------------------------------------------------

# Per HKUDS: token estimate * 4/3 padding for conservative over-estimate.
# Triggering compact slightly early is safer than PTL surprise.
_TOKEN_PADDING = 4 / 3

# Per HKUDS: image budget 3072 tokens by default. The
# :func:`estimate_message_tokens` ``image_token_estimate`` kwarg overrides.
_IMAGE_TOKEN_ESTIMATE = 3_072

# L2 context-collapse thresholds (HKUDS):
# - Text bodies >= 2400 chars get head/tail truncated
# - Head 900 + tail 500 chars + ``[collapsed N chars]`` marker
_L2_LONG_TEXT_THRESHOLD = 2_400
_L2_HEAD_CHARS = 900
_L2_TAIL_CHARS = 500

# L3 session_memory freshness: 1 hour. Older checkpoints are stale
# enough that L4 LLM summary is preferred over splicing in outdated
# state. Tunable in case real usage shows different cadences.
_L3_FRESHNESS_SECONDS = 60 * 60

# L3 / L4 splice: how many tail messages to preserve un-compacted.
# Recent messages carry the most signal for the LLM's next move;
# they survive both layers' splicing.
_PRESERVE_RECENT = 12

# Default context window when model unknown. 32k is a safe lower
# bound — any modern Provider's flagship model exceeds this.
_DEFAULT_CONTEXT_WINDOW = 32_000

# Known model context windows. Used by :func:`get_context_window`.
# Includes prefix matching ("qwen-plus-latest" → uses "qwen-plus").
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "qwen-plus": 32_000,
    "qwen-max": 32_000,
    "qwen3.7-max": 262_144,
    "qwen-turbo": 8_000,
    "claude-3-opus": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-4-sonnet": 200_000,
}


# ---------------------------------------------------------------------------
# L4 system prompt — VERBATIM from HKUDS upstream per D29.3 sub-decision
# ---------------------------------------------------------------------------

_L4_COMPACT_SYSTEM_PROMPT = """\
You are summarizing a conversation between a user and an AI assistant
so the assistant can continue the work in a fresh context window.

First, inside <analysis> tags, briefly note which parts of the
conversation carry information that matters for continuation.

Then, inside <summary> tags, produce a structured summary with
exactly these 9 sections in order:

1. **Primary Request and Intent**: what the user originally asked for
2. **Key Technical Concepts**: technologies / APIs / patterns discussed
3. **Files and Code Sections**: which files / functions touched, with
   one-line purpose each
4. **Errors and Fixes**: what broke + how it was resolved
5. **Problem Solving**: the reasoning chain behind decisions
6. **All User Messages**: each turn the user typed, summarized
7. **Pending Tasks**: what's known to still need doing
8. **Current Work**: what was happening when this summary was taken
9. **Optional Next Step**: the most likely next thing to do, if clear

Output ONLY the <analysis>...</analysis> and <summary>...</summary>
tags. No greeting, no closing, no markdown outside the tags."""

# Boundary marker placed between summary and preserved-tail messages
# so the LLM understands where compaction cut occurred. Renders as
# a user-role message so the model's attention shape isn't disturbed.
_COMPACT_BOUNDARY_MARKER = "[Conversation history summarized below — older messages elided]"

_SUMMARY_TAG_PATTERN = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactResult:
    """Outcome of an :func:`auto_compact_if_needed` call.

    ``compact_kind`` is the **highest level that actually fired**:

    - ``"none"`` — L0 estimated under threshold; no transform applied
    - ``"context_collapse"`` — L2 alone freed enough tokens
    - ``"session_memory"`` — L3 spliced the checkpoint (no LLM call)
    - ``"full"`` — L4 called the LLM for a 9-slot summary

    ``applied_levels`` records EVERY level that ran (e.g., ``(0, 2, 3)``
    means L0 estimated, L2 collapsed, L3 spliced). Useful for
    observability and debugging escalation behavior.
    """

    compact_kind: str
    applied_levels: tuple[int, ...]
    original_tokens: int
    final_tokens: int

    @classmethod
    def no_op(cls, tokens: int) -> CompactResult:
        """Convenience: result for the "L0 says we're under threshold" path."""
        return cls(
            compact_kind="none",
            applied_levels=(0,),
            original_tokens=tokens,
            final_tokens=tokens,
        )


# ---------------------------------------------------------------------------
# L0 — Token estimation + threshold computation
# ---------------------------------------------------------------------------


def get_context_window(model: str) -> int:
    """Return the known context window for ``model`` or a safe default.

    Tries exact match first; falls back to prefix match (so
    ``qwen-plus-latest`` resolves to ``qwen-plus``'s 32k window).
    """
    if model in _MODEL_CONTEXT_WINDOWS:
        return _MODEL_CONTEXT_WINDOWS[model]
    for known, window in _MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(known):
            return window
    return _DEFAULT_CONTEXT_WINDOW


def threshold_tokens(model: str, *, threshold_ratio: float) -> int:
    """Compute the auto-compact threshold for the given model.

    E.g., ``threshold_ratio=0.83`` (D29.8 default) for qwen-plus (32k)
    gives ``26,560`` tokens — auto compact fires when estimated input
    crosses that.
    """
    return int(get_context_window(model) * threshold_ratio)


def estimate_message_tokens(
    messages: list[ConversationMessage],
    *,
    model: str,
    image_token_estimate: int = _IMAGE_TOKEN_ESTIMATE,
) -> int:
    """Estimate total tokens across all messages, with safety padding.

    Per-block costs:
    - :class:`TextBlock` → exact tiktoken count (or byte-ratio fallback
      for unknown models — see Phase 4 ``count_tokens`` for details)
    - :class:`ToolUseBlock` → ``name + json(input)``
    - :class:`ToolResultBlock` → ``content``
    - :class:`ImageBlock` → ``image_token_estimate`` (3072 default)

    Result multiplied by ``4/3`` padding factor (HKUDS convention) so
    we err on the high side. Over-estimating triggers compact slightly
    early — preferable to PTL surprise.
    """
    total = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextBlock):
                total += count_tokens(block.text, model)
            elif isinstance(block, ToolUseBlock):
                total += count_tokens(block.name, model)
                total += count_tokens(json.dumps(block.input), model)
            elif isinstance(block, ToolResultBlock):
                total += count_tokens(block.content, model)
            elif isinstance(block, ImageBlock):
                total += image_token_estimate
    return int(total * _TOKEN_PADDING)


# ---------------------------------------------------------------------------
# L2 — Context collapse (deterministic byte-level)
# ---------------------------------------------------------------------------


def try_context_collapse(
    messages: list[ConversationMessage],
) -> tuple[list[ConversationMessage], bool]:
    """For each :class:`TextBlock` or :class:`ToolResultBlock` whose
    body exceeds ``_L2_LONG_TEXT_THRESHOLD`` chars, replace with
    head + ``[collapsed N chars]`` marker + tail.

    Pure transformation — returns a new list, doesn't mutate caller's.

    Returns ``(new_messages, any_changed)``. ``any_changed=False`` means
    no body was long enough to collapse — L2 didn't help, escalate
    to L3.
    """
    any_changed = False
    new_messages: list[ConversationMessage] = []
    for msg in messages:
        new_content: list[TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock] = []
        for block in msg.content:
            if isinstance(block, TextBlock) and len(block.text) > _L2_LONG_TEXT_THRESHOLD:
                new_content.append(TextBlock(text=_collapse_string(block.text)))
                any_changed = True
            elif (
                isinstance(block, ToolResultBlock) and len(block.content) > _L2_LONG_TEXT_THRESHOLD
            ):
                new_content.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=_collapse_string(block.content),
                        is_error=block.is_error,
                    )
                )
                any_changed = True
            else:
                new_content.append(block)
        new_messages.append(ConversationMessage(role=msg.role, content=new_content))
    return new_messages, any_changed


def _collapse_string(text: str) -> str:
    """Head + ``[collapsed N chars]`` marker + tail. Symmetric with
    :func:`openharness.compaction.truncate.head_tail_truncate` but
    char-based (not token-based) — L2 is a faster cheap pass; precise
    token slicing isn't needed when we'll re-estimate after."""
    omitted = len(text) - _L2_HEAD_CHARS - _L2_TAIL_CHARS
    head = text[:_L2_HEAD_CHARS]
    tail = text[-_L2_TAIL_CHARS:]
    return f"{head}\n...[collapsed {omitted} chars]...\n{tail}"


# ---------------------------------------------------------------------------
# L3 — Session memory checkpoint reuse (file read, no LLM)
# ---------------------------------------------------------------------------


def try_session_memory_compaction(
    messages: list[ConversationMessage],
    session_memory_path: Path | None,
    *,
    preserve_recent: int = _PRESERVE_RECENT,
    fresh_window_seconds: float = _L3_FRESHNESS_SECONDS,
) -> tuple[list[ConversationMessage], bool]:
    """Splice older messages with the 5-slot checkpoint markdown file.

    Skip conditions (return ``(messages, False)`` — let L4 try):
    - ``session_memory_path`` is None or doesn't exist (no checkpoint
      yet — first turn of project)
    - Checkpoint older than ``fresh_window_seconds`` (default 1h —
      stale state, prefer L4's fresh LLM summary)
    - ``len(messages) <= preserve_recent`` (not enough older messages
      to splice into checkpoint)
    - File read fails (permission / decode error)

    When successful, replaces ``messages[:-preserve_recent]`` with a
    single synthetic user message containing the checkpoint. Tail
    messages preserved.
    """
    if session_memory_path is None or not session_memory_path.exists():
        return messages, False

    try:
        age = time.time() - session_memory_path.stat().st_mtime
    except OSError:
        return messages, False
    if age > fresh_window_seconds:
        return messages, False

    if len(messages) <= preserve_recent:
        return messages, False

    try:
        checkpoint = session_memory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return messages, False

    older = messages[:-preserve_recent]
    recent = messages[-preserve_recent:]
    if not older:
        return messages, False

    synthetic = ConversationMessage(
        role="user",
        content=[
            TextBlock(
                text=(
                    f"Session memory checkpoint from earlier in this conversation:\n\n{checkpoint}"
                )
            )
        ],
    )
    return [synthetic, *recent], True


# ---------------------------------------------------------------------------
# L4 — Full compact (LLM call via summarize primitive)
# ---------------------------------------------------------------------------


async def full_compact(
    messages: list[ConversationMessage],
    *,
    model: str,
    api_client: SupportsStreamingMessages,
    max_tokens: int = 20_000,
    timeout_seconds: float = 25.0,
    preserve_recent: int = _PRESERVE_RECENT,
) -> tuple[list[ConversationMessage], bool]:
    """L4: ``summarize()`` call with the 9-slot system prompt. Splice
    via boundary marker + summary + preserved tail.

    Returns ``(new_messages, did_apply)``. On any exception from
    :func:`summarize` (PTL exhausted retries, streaming all-fail,
    timeout, malformed output) the function returns
    ``(messages, False)`` — caller (auto_compact orchestrator) treats
    this as "L4 didn't help, fall back to un-compacted prompt + let
    the engine's reactive PTL retry handle it".
    """
    if len(messages) <= preserve_recent:
        return messages, False

    older = messages[:-preserve_recent]
    recent = messages[-preserve_recent:]

    try:
        raw = await summarize(
            messages=older,
            system_prompt=_L4_COMPACT_SYSTEM_PROMPT,
            model=model,
            api_client=api_client,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            tools_disabled=True,
        )
    except Exception:
        # summarize() exhausted retries OR malformed input — return un-
        # compacted so engine's reactive PTL retry layer still catches.
        return messages, False

    summary_text = _extract_summary(raw)
    if not summary_text:
        return messages, False

    boundary = ConversationMessage(
        role="user",
        content=[TextBlock(text=_COMPACT_BOUNDARY_MARKER)],
    )
    summary = ConversationMessage(
        role="user",
        content=[TextBlock(text=f"Summary of prior conversation:\n\n{summary_text}")],
    )
    return [boundary, summary, *recent], True


def _extract_summary(raw: str) -> str:
    """Extract the ``<summary>...</summary>`` content from the LLM
    response. ``<analysis>`` tags are discarded.

    If no ``<summary>`` tags found, fall back to the stripped response
    text — the LLM may have ignored the schema; salvage what we have
    rather than discarding everything.
    """
    match = _SUMMARY_TAG_PATTERN.search(raw)
    if match is None:
        return raw.strip()
    return match.group(1).strip()


# ---------------------------------------------------------------------------
# Orchestrator — L0 → L2 → L3 → L4 escalation
# ---------------------------------------------------------------------------


async def auto_compact_if_needed(
    messages: list[ConversationMessage],
    *,
    model: str,
    api_client: SupportsStreamingMessages,
    session_memory_path: Path | None = None,
    enabled: bool = True,
    threshold_ratio: float = 0.83,
    full_compact_max_tokens: int = 20_000,
    full_compact_timeout_s: float = 25.0,
) -> tuple[list[ConversationMessage], CompactResult]:
    """Run the L0-L4 escalation. Returns ``(possibly-compacted-messages,
    CompactResult)``.

    L1 (microcompact) is NOT in this pipeline — Phase 4's PostToolUse
    hook handles it before each turn. This function picks up at L0
    (estimate) and escalates through L2 / L3 / L4.

    ``enabled=False`` short-circuits everything (returns
    ``CompactResult.no_op``). Same for the under-threshold path —
    no work done when not needed.

    On L4 failure (LLM exhausted retries), returns the messages
    unchanged with ``compact_kind="none"``. The engine's reactive
    PTL retry remains as last-resort safety net.
    """
    original_tokens = estimate_message_tokens(messages, model=model)

    if not enabled:
        return messages, CompactResult.no_op(original_tokens)

    threshold = threshold_tokens(model, threshold_ratio=threshold_ratio)
    if original_tokens < threshold:
        return messages, CompactResult.no_op(original_tokens)

    levels_applied: list[int] = [0]  # L0 always runs

    # --- L2 ---
    messages_after_l2, l2_changed = try_context_collapse(messages)
    if l2_changed:
        levels_applied.append(2)
        tokens_after_l2 = estimate_message_tokens(messages_after_l2, model=model)
        if tokens_after_l2 < threshold:
            return messages_after_l2, CompactResult(
                compact_kind="context_collapse",
                applied_levels=tuple(levels_applied),
                original_tokens=original_tokens,
                final_tokens=tokens_after_l2,
            )
        messages = messages_after_l2  # carry forward into L3/L4

    # --- L3 ---
    messages_after_l3, l3_changed = try_session_memory_compaction(messages, session_memory_path)
    if l3_changed:
        levels_applied.append(3)
        tokens_after_l3 = estimate_message_tokens(messages_after_l3, model=model)
        return messages_after_l3, CompactResult(
            compact_kind="session_memory",
            applied_levels=tuple(levels_applied),
            original_tokens=original_tokens,
            final_tokens=tokens_after_l3,
        )

    # --- L4 (LLM) ---
    messages_after_l4, l4_changed = await full_compact(
        messages,
        model=model,
        api_client=api_client,
        max_tokens=full_compact_max_tokens,
        timeout_seconds=full_compact_timeout_s,
    )
    if l4_changed:
        levels_applied.append(4)
        tokens_after_l4 = estimate_message_tokens(messages_after_l4, model=model)
        return messages_after_l4, CompactResult(
            compact_kind="full",
            applied_levels=tuple(levels_applied),
            original_tokens=original_tokens,
            final_tokens=tokens_after_l4,
        )

    # All applicable layers tried, nothing freed enough — surrender
    # gracefully. The engine's reactive PTL retry will catch the
    # eventual provider error if any.
    return messages, CompactResult(
        compact_kind="none",
        applied_levels=tuple(levels_applied),
        original_tokens=original_tokens,
        final_tokens=original_tokens,
    )
