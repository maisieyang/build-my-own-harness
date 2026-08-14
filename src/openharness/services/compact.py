"""Conversation compaction.

The runtime controls context growth in four distinct places:

1. the PostToolUse hook budgets each new Tool Result at ingress;
2. this module estimates the whole Conversation before each model request;
3. once the threshold is crossed, older successful ``Read`` and ``Grep``
   results may be replaced only when the private summarizer input gets smaller;
   then the LLM summarizes older history and the original recent tail is
   spliced back;
4. the engine's Prompt Too Long retry remains the reactive fallback.

User and assistant text is never deterministically folded. Cleanup is not a
standalone compact outcome: if summarization fails, the caller gets the exact
original Conversation back.

The summary prompt uses a 9-slot schema plus a fidelity contract for structured
evidence, provenance, opaque identifiers, error ordering, and current state.
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

# Full compact splice: how many tail messages to preserve un-compacted.
# Recent messages carry the most signal for the LLM's next move;
# they survive the semantic splice byte-for-byte.
_PRESERVE_RECENT = 12

# Default context window when model unknown. 32k is a safe lower
# bound — any modern Provider's flagship model exceeds this.
_DEFAULT_CONTEXT_WINDOW = 32_000

# These tools are read-only and can query the current source again from the
# original ToolUse arguments. That does not make their exact historical output
# recoverable after the source changes. Dynamic Web/MCP results, LoadSkill
# provenance, Bash/Agent evidence, errors, and unknown tools are intentionally
# excluded.
_RERUNNABLE_TOOL_RESULTS = frozenset({"Read", "Grep"})

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

    ``compact_kind`` is the transform that actually completed:

    - ``"none"`` — estimated under threshold or summarization failed
    - ``"full"`` — the LLM produced a usable structured summary

    ``applied_levels`` remains a compact observability field: ``0`` is the
    threshold estimate and ``4`` is semantic full compact. The retired global
    block-collapse level is deliberately absent.
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
# Summary-input preparation
# ---------------------------------------------------------------------------


def _omit_old_rerunnable_tool_results(
    older: list[ConversationMessage],
    *,
    model: str,
) -> list[ConversationMessage]:
    """Clear only old successful results whose marker is token-smaller.

    The returned messages are used solely as input to the summarizer. ToolUse
    blocks and their arguments stay intact, so a later agent can query the
    current source again. Exact historical output is not promised. Short
    results stay verbatim when replacing them would fail to reclaim tokens.
    Caller-owned messages are never mutated.
    """
    tool_names: dict[str, str] = {}
    for message in older:
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tool_names[block.id] = block.name

    new_messages: list[ConversationMessage] = []
    for msg in older:
        new_content: list[TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock] = []
        for block in msg.content:
            tool_name = (
                tool_names.get(block.tool_use_id) if isinstance(block, ToolResultBlock) else None
            )
            if (
                isinstance(block, ToolResultBlock)
                and not block.is_error
                and tool_name in _RERUNNABLE_TOOL_RESULTS
            ):
                assert tool_name is not None
                original_tokens = count_tokens(block.content, model)
                marker = (
                    f"[older successful {tool_name} tool result omitted from summary input; "
                    f"original output used {original_tokens} tokens; preserved ToolUse "
                    "arguments can query the current source again but cannot guarantee the "
                    "exact historical output]"
                )
                if count_tokens(marker, model) < original_tokens:
                    new_content.append(
                        ToolResultBlock(
                            tool_use_id=block.tool_use_id,
                            content=marker,
                            is_error=False,
                        )
                    )
                else:
                    new_content.append(block)
            else:
                new_content.append(block)
        new_messages.append(ConversationMessage(role=msg.role, content=new_content))
    return new_messages


# ---------------------------------------------------------------------------
# Full compact (LLM call via summarize primitive)
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
    """Call ``summarize()`` with the 9-slot system prompt. Splice
    via boundary marker + summary + preserved tail.

    Only the older slice is prepared for summarization: successful ``Read``
    and ``Grep`` result bodies may be omitted when the marker is token-smaller.
    Their ToolUse arguments can query the current source again but cannot
    recreate exact historical output. The recent slice is retained byte-for-
    byte from the original input and is never sent through deterministic
    cleanup.

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
    older_for_summary = _omit_old_rerunnable_tool_results(older, model=model)
    summarization_messages = [
        *older_for_summary,
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
# Orchestrator — threshold → full compact
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
    """Estimate the Conversation and run semantic compact above threshold.

    Returns ``(possibly-compacted-messages,
    CompactResult)``.

    Per-result ingress budgeting is not in this function; the PostToolUse hook
    applies it as results arrive. Above the Conversation threshold this
    function always attempts full semantic compact. It never returns a global
    deterministic block-collapse as the next model Working Set.

    ``enabled=False`` short-circuits everything (returns
    ``CompactResult.no_op``). Same for the under-threshold path —
    no work done when not needed.

    On summarization failure (LLM exhausted retries), returns the messages
    unchanged with ``compact_kind="none"``. The engine's reactive
    PTL retry remains as last-resort safety net.
    """
    original_tokens = estimate_message_tokens(messages, model=model)

    if not enabled:
        return messages, CompactResult.no_op(original_tokens)

    threshold = threshold_tokens(model, threshold_ratio=threshold_ratio)
    if original_tokens < threshold:
        return messages, CompactResult.no_op(original_tokens)

    levels_applied: list[int] = [0]
    compacted_messages, did_compact = await full_compact(
        messages,
        model=model,
        api_client=api_client,
        max_tokens=full_compact_max_tokens,
        timeout_seconds=full_compact_timeout_s,
    )
    if did_compact:
        levels_applied.append(4)
        final_tokens = estimate_message_tokens(compacted_messages, model=model)
        return compacted_messages, CompactResult(
            compact_kind="full",
            applied_levels=tuple(levels_applied),
            original_tokens=original_tokens,
            final_tokens=final_tokens,
        )

    # Semantic compact did not produce a usable summary. Preserve the original
    # Conversation; the engine's reactive PTL retry remains the final fallback.
    return messages, CompactResult(
        compact_kind="none",
        applied_levels=tuple(levels_applied),
        original_tokens=original_tokens,
        final_tokens=original_tokens,
    )
