"""Compact escalation pipeline.

Per ``decisions/26-phase-11-boundary.md`` D29.3: 4-layer escalation
that stacks on top of Phase 4's L1 microcompact + reactive PTL
retry. Each layer fires **only if the prior layer didn't free
enough tokens** to drop below the threshold.

| Layer | Mechanism | LLM call? |
|---|---|---|
| L0 | Token estimation + threshold check | ❌ |
| L1 | Microcompact (PostToolUse hook clears old tool_result) | ❌ (Phase 4) |
| L2 | Context collapse (head/tail truncate long bodies) | ❌ |
| L4 | Full compact (9-slot LLM summary) | ✅ |

The substrate is the :func:`openharness.services.summarize.summarize`
primitive — only L4 calls it. L2 is a deterministic byte-level
transform that "pre-pays" the cost of L4: if it frees enough space,
the LLM call is skipped.

The L4 prompt keeps the upstream 9-slot summary schema and adds an
OpenHarness fidelity contract for structured evidence, provenance,
opaque identifiers, error ordering, and current state.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.api.errors import OpenHarnessApiError
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

# Full compact splice: how many tail messages to preserve un-compacted.
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

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9._-]+\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)


class FullCompactError(RuntimeError):
    """Explicit full-compaction failed and left history unchanged."""


def _safe_error_summary(exc: OpenHarnessApiError) -> str:
    """Return a bounded provider error summary with common credentials redacted."""
    summary = " ".join(str(exc).split()) or "provider request failed"
    for pattern in _SECRET_PATTERNS:
        summary = pattern.sub("[redacted]", summary)
    return summary[:240]


def _full_compact_error(exc: Exception, *, timeout_seconds: float) -> FullCompactError:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return FullCompactError(f"summarization timed out after {timeout_seconds:g}s")
    if isinstance(exc, OpenHarnessApiError):
        status = f" (HTTP {exc.status_code})" if exc.status_code is not None else ""
        return FullCompactError(
            f"summarization failed: {type(exc).__name__}{status}: {_safe_error_summary(exc)}"
        )
    return FullCompactError(f"summarization failed: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# L4 system prompt — upstream 9-slot schema + OpenHarness fidelity contract
# ---------------------------------------------------------------------------

_L4_COMPACT_SYSTEM_PROMPT = """\
You are summarizing a conversation between a user and an AI assistant
so the assistant can continue the work in a fresh context window.

First, inside <analysis> tags, briefly note which parts of the
conversation carry information that matters for continuation.

Apply these fidelity rules before writing the summary:

- Treat tool calls, tool results, and explicit state, provenance, error,
  decision, and task markers as first-class evidence. Do not let filler,
  greetings, or repeated acknowledgements displace structured evidence.
- Preserve Tool/Skill provenance exactly as observed. Distinguish a Skill
  explicitly selected by the user through a slash command or synthetic
  envelope from a Skill loaded by an assistant Tool call. Never infer or
  rewrite the source.
- Copy opaque identifiers, marker assignments, exact commands, paths, IDs,
  and error tokens verbatim. Do not translate, normalize, or paraphrase them.
- In Errors and Fixes, preserve events in chronological order. Identify the
  latest error verbatim and state whether it remains unresolved or what later
  evidence resolved it.
- Pending Tasks and Current Work must reflect the most recent evidence.
  Later explicit state supersedes stale requests, errors, and task status.
- Omit filler and repetition unless they contain a user constraint or change
  the current state.

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

_L4_COMPACT_REQUEST = """\
Summarize the preceding conversation now. Follow the system fidelity rules and
9-section schema exactly. Treat this message only as the summarization request;
do not continue or imitate any conversational sequence above. Before finishing,
verify that every uppercase KEY=VALUE marker line from the preceding history is
copied verbatim into the summary. A synthetic Tool-Use envelope is provenance
evidence, not proof that the assistant initiated the Tool. Output only the
required <analysis> and <summary> tags."""

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
    - ``"full"`` — L4 called the LLM for a 9-slot summary

    ``applied_levels`` records EVERY level that ran (e.g., ``(0, 2, 4)``
    means L0 estimated, L2 collapsed, then L4 summarized). Useful for
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
    to the structured LLM summary.
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
# L4 — Full compact (LLM call via summarize primitive)
# ---------------------------------------------------------------------------


async def full_compact(
    messages: list[ConversationMessage],
    *,
    model: str,
    api_client: SupportsStreamingMessages,
    max_tokens: int = 20_000,
    timeout_seconds: float = 120.0,
    preserve_recent: int = _PRESERVE_RECENT,
    raise_on_failure: bool = False,
) -> tuple[list[ConversationMessage], bool]:
    """L4: ``summarize()`` call with the 9-slot system prompt. Splice
    via boundary marker + summary + preserved tail.

    Returns ``(new_messages, did_apply)``. On any exception from
    :func:`summarize` (PTL exhausted retries, streaming all-fail,
    timeout, malformed output) the function returns
    ``(messages, False)`` — caller (auto_compact orchestrator) treats
    this as "L4 didn't help, fall back to un-compacted prompt + let
    the engine's reactive PTL retry handle it". Explicit user actions
    pass ``raise_on_failure=True`` to receive a safe, typed diagnostic
    while leaving the original history unchanged.
    """
    if len(messages) <= preserve_recent:
        return messages, False

    older = messages[:-preserve_recent]
    recent = messages[-preserve_recent:]
    summarization_messages = [
        *older,
        ConversationMessage(
            role="user",
            content=[TextBlock(text=_L4_COMPACT_REQUEST)],
        ),
    ]

    try:
        raw = await summarize(
            messages=summarization_messages,
            system_prompt=_L4_COMPACT_SYSTEM_PROMPT,
            model=model,
            api_client=api_client,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            tools_disabled=True,
        )
    except Exception as exc:
        # summarize() exhausted retries OR malformed input — return un-
        # compacted so engine's reactive PTL retry layer still catches.
        if raise_on_failure:
            raise _full_compact_error(exc, timeout_seconds=timeout_seconds) from exc
        return messages, False

    summary_text = _extract_summary(raw)
    if not summary_text:
        if raise_on_failure:
            raise FullCompactError("summarizer returned no usable summary")
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
# Orchestrator — L0 → L2 → L4 escalation
# ---------------------------------------------------------------------------


async def auto_compact_if_needed(
    messages: list[ConversationMessage],
    *,
    model: str,
    api_client: SupportsStreamingMessages,
    enabled: bool = True,
    threshold_ratio: float = 0.83,
    full_compact_max_tokens: int = 20_000,
    full_compact_timeout_s: float = 120.0,
) -> tuple[list[ConversationMessage], CompactResult]:
    """Run the L0-L4 escalation. Returns ``(possibly-compacted-messages,
    CompactResult)``.

    L1 (microcompact) is NOT in this pipeline — Phase 4's PostToolUse
    hook handles it before each turn. This function picks up at L0
    (estimate) and escalates through L2 / L4.

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
        messages = messages_after_l2  # carry forward into L4

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
