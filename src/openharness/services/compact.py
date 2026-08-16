"""Conversation compaction.

The runtime controls context growth in five distinct places:

1. the PostToolUse hook budgets each new Tool Result at ingress;
2. this module estimates the full draft request — system prompt, Tool schemas,
   Conversation, output reserve, and a safety margin;
3. once the threshold is crossed, old completed Tool Results are cleared by a
   tool-agnostic policy while preserving two independent recency windows: the
   recent message tail and the latest three completed Tool interactions;
4. if deterministic Tool Result cleanup is insufficient, the LLM summarizes
   older history and the original recent message tail is spliced back;
5. a provider Prompt Too Long response permits one semantic request
   recompilation; it never triggers blind Conversation-message deletion.

User and assistant text is never deterministically folded. Tool cleanup is a
standalone compact outcome, so a successful cleanup can avoid a Summary call.

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
    from openharness.protocols.requests import ApiMessageRequest


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
_PRESERVE_RECENT_MESSAGES = 12

# Tool-result cleanup has a second, independent recency window. It protects
# the newest completed ToolUse/ToolResult interactions even when later plain
# conversation messages have pushed those interactions outside the message
# tail. Anthropic Context Editing and LangChain both default to three.
_PRESERVE_RECENT_TOOL_INTERACTIONS = 3

_CLEARED_TOOL_RESULT = "[cleared]"

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

    ``compact_kind`` is the transform that actually completed:

    - ``"none"`` — estimated under threshold and no transform ran
    - ``"tool_results"`` — old Tool Result bodies were cleared
    - ``"full"`` — the LLM produced a usable structured summary

    ``applied_levels`` remains a compact observability field: ``0`` is the
    threshold estimate, ``2`` is deterministic Tool Result cleanup, and ``4``
    is semantic full compact. The retired global block-collapse level is
    deliberately absent.
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


def request_input_token_budget(
    model: str,
    *,
    max_output_tokens: int,
    threshold_ratio: float,
) -> int:
    """Return the safe input budget for one complete provider request.

    The model context window is shared by input and generated output. Reserve
    ``max_output_tokens`` first, then apply the configured safety ratio to the
    remaining input capacity. A minimum of one token keeps diagnostics and
    edge-case tests total even when a caller configures an impossible output
    limit for a small/unknown context window.
    """
    context_window = get_context_window(model)
    available_input = max(1, context_window - max_output_tokens)
    return max(1, int(available_input * threshold_ratio))


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


def estimate_request_input_tokens(request: ApiMessageRequest) -> int:
    """Estimate the entire input side of an :class:`ApiMessageRequest`.

    Conversation-only accounting misses exactly the surfaces that grow as a
    Harness gains capabilities: system/project instructions and Tool/MCP JSON
    schemas. Message blocks use their existing image-aware estimator. The
    remaining request envelope is serialized separately so images are not
    accidentally charged by base64 byte length.
    """
    envelope = {
        "model": request.model,
        "system": request.system,
        "tools": (
            [tool.model_dump(mode="json") for tool in request.tools]
            if request.tools is not None
            else None
        ),
        "stream": request.stream,
    }
    envelope_tokens = count_tokens(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        request.model,
    )
    return estimate_message_tokens(request.messages, model=request.model) + int(
        envelope_tokens * _TOKEN_PADDING
    )


def _tail_has_complete_tool_protocol(messages: list[ConversationMessage]) -> bool:
    """Return whether a suffix contains no split ToolUse/ToolResult pair."""
    pending: set[str] = set()
    for message in messages:
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                pending.add(block.id)
            elif isinstance(block, ToolResultBlock):
                if block.tool_use_id not in pending:
                    return False
                pending.remove(block.tool_use_id)
    return not pending


def _largest_recent_tail_that_fits(
    messages: list[ConversationMessage],
    *,
    model: str,
    input_token_budget: int,
    request_overhead_tokens: int,
    summary_token_reserve: int,
    max_preserve_recent_messages: int,
) -> int:
    """Choose the largest protocol-valid exact suffix inside the budget."""
    maximum = min(max_preserve_recent_messages, len(messages) - 1)
    for count in range(maximum, 0, -1):
        recent = messages[-count:]
        if not _tail_has_complete_tool_protocol(recent):
            continue
        projected_tokens = (
            request_overhead_tokens
            + summary_token_reserve
            + estimate_message_tokens(recent, model=model)
        )
        if projected_tokens <= input_token_budget:
            return count

    # Preserve at least one complete suffix when possible. The provider stays
    # authoritative if even this conservative projection remains too large.
    for count in range(1, maximum + 1):
        if _tail_has_complete_tool_protocol(messages[-count:]):
            return count
    return 1


# ---------------------------------------------------------------------------
# Tool-result cleanup
# ---------------------------------------------------------------------------


def _clear_old_tool_results(
    messages: list[ConversationMessage],
    *,
    preserve_recent_messages: int,
    preserve_recent_tool_interactions: int = _PRESERVE_RECENT_TOOL_INTERACTIONS,
) -> tuple[list[ConversationMessage], int]:
    """Replace old completed Tool Result bodies with ``[cleared]``.

    Two protection windows are computed independently over the original
    Conversation, then unioned:

    * every block in the last ``preserve_recent_messages`` messages;
    * both sides of the last ``preserve_recent_tool_interactions`` completed
      ToolUse/ToolResult interactions, paired by ``tool_use_id``.

    Cleanup is tool-agnostic and therefore applies to built-ins, plugins, MCP,
    Web, Agent, and future tools without an allowlist. ToolUse names and inputs
    remain intact. Orphan results and pending/unmatched ToolUse blocks are not
    changed. Caller-owned messages are never mutated.
    """
    recent_start = max(0, len(messages) - preserve_recent_messages)

    tool_use_message_indexes: dict[str, int] = {}
    completed_ids: list[str] = []
    completed_seen: set[str] = set()
    for message_index, message in enumerate(messages):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tool_use_message_indexes.setdefault(block.id, message_index)
            elif (
                isinstance(block, ToolResultBlock)
                and block.tool_use_id in tool_use_message_indexes
                and block.tool_use_id not in completed_seen
            ):
                completed_ids.append(block.tool_use_id)
                completed_seen.add(block.tool_use_id)

    if preserve_recent_tool_interactions <= 0:
        recent_tool_ids: set[str] = set()
    else:
        recent_tool_ids = set(completed_ids[-preserve_recent_tool_interactions:])

    new_messages: list[ConversationMessage] = []
    cleared_count = 0
    for message_index, msg in enumerate(messages):
        new_content: list[TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock] = []
        for block in msg.content:
            if not isinstance(block, ToolResultBlock):
                new_content.append(block)
                continue

            tool_use_message_index = tool_use_message_indexes.get(block.tool_use_id)
            should_clear = (
                tool_use_message_index is not None
                and tool_use_message_index < recent_start
                and message_index < recent_start
                and block.tool_use_id not in recent_tool_ids
                and block.content != _CLEARED_TOOL_RESULT
            )
            if should_clear:
                new_content.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=_CLEARED_TOOL_RESULT,
                        is_error=block.is_error,
                    )
                )
                cleared_count += 1
            else:
                new_content.append(block)
        if new_content == msg.content:
            new_messages.append(msg)
        else:
            new_messages.append(ConversationMessage(role=msg.role, content=new_content))

    return new_messages, cleared_count


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
    preserve_recent: int = _PRESERVE_RECENT_MESSAGES,
    raise_on_failure: bool = False,
) -> tuple[list[ConversationMessage], bool]:
    """Call ``summarize()`` with the 9-slot system prompt. Splice
    via boundary marker + summary + preserved tail.

    Before summarization, old completed Tool Result bodies are cleared using
    independent message-recency and tool-recency protections. The recent
    message slice is retained byte-for-byte from the original input.

    Returns ``(new_messages, did_apply)``. On any exception from
    :func:`summarize` (PTL, streaming all-fail,
    timeout, malformed output) the function returns
    ``(messages, False)`` — caller (auto_compact orchestrator) treats
    this as "L4 didn't help" and keeps the input unchanged. Explicit user actions
    pass ``raise_on_failure=True`` to receive a safe, typed diagnostic
    while leaving the original history unchanged.
    """
    if preserve_recent < 1:
        msg = "preserve_recent must be at least 1"
        raise ValueError(msg)
    if len(messages) <= preserve_recent:
        return messages, False

    prepared_messages, _cleared_count = _clear_old_tool_results(
        messages,
        preserve_recent_messages=preserve_recent,
    )
    older_for_summary = prepared_messages[:-preserve_recent]
    recent = messages[-preserve_recent:]
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
        # The caller decides whether this was a best-effort proactive compact
        # or an explicit operation that needs a typed diagnostic.
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


async def compact_for_request_budget(
    messages: list[ConversationMessage],
    *,
    model: str,
    api_client: SupportsStreamingMessages,
    input_token_budget: int,
    request_overhead_tokens: int,
    preserve_recent_messages: int = _PRESERVE_RECENT_MESSAGES,
    full_compact_max_tokens: int = 20_000,
    full_compact_timeout_s: float = 120.0,
) -> tuple[list[ConversationMessage], bool]:
    """Semantically recompile a rejected draft request exactly once.

    Unlike normal threshold compaction, this path has provider evidence that
    the full request did not fit. It therefore selects the largest exact,
    protocol-valid recent suffix that can fit alongside the non-Conversation
    request overhead and the configured maximum Summary output. Everything
    older is passed to :func:`full_compact`; no raw Conversation message is
    silently deleted.
    """
    if preserve_recent_messages < 1:
        msg = "preserve_recent_messages must be at least 1"
        raise ValueError(msg)
    if len(messages) <= 1:
        return messages, False

    preserve_recent = _largest_recent_tail_that_fits(
        messages,
        model=model,
        input_token_budget=input_token_budget,
        request_overhead_tokens=request_overhead_tokens,
        summary_token_reserve=full_compact_max_tokens,
        max_preserve_recent_messages=preserve_recent_messages,
    )
    return await full_compact(
        messages,
        model=model,
        api_client=api_client,
        max_tokens=full_compact_max_tokens,
        timeout_seconds=full_compact_timeout_s,
        preserve_recent=preserve_recent,
    )


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
    preserve_recent_messages: int = _PRESERVE_RECENT_MESSAGES,
    input_token_budget: int | None = None,
    request_overhead_tokens: int = 0,
) -> tuple[list[ConversationMessage], CompactResult]:
    """Clean old Tool Results, then summarize only if still above threshold.

    Returns ``(possibly-compacted-messages,
    CompactResult)``.

    Per-result ingress budgeting is not in this function; the PostToolUse hook
    applies it as results arrive. Above the configured full-request budget this
    function first clears eligible old Tool Result bodies. Message and Tool
    recency are independent protections. If cleanup moves the Conversation
    below threshold, no Summary call is made.

    ``enabled=False`` short-circuits everything (returns
    ``CompactResult.no_op``). Same for the under-threshold path —
    no work done when not needed.

    On summarization failure after successful cleanup, the cleaned Conversation
    remains the next Working Set. With no eligible cleanup, the original
    Conversation remains unchanged. A provider PTL can later trigger one
    semantic request recompilation at the engine boundary.
    """
    if preserve_recent_messages < 1:
        msg = "preserve_recent_messages must be at least 1"
        raise ValueError(msg)

    if request_overhead_tokens < 0:
        msg = "request_overhead_tokens must not be negative"
        raise ValueError(msg)

    original_tokens = request_overhead_tokens + estimate_message_tokens(messages, model=model)

    if not enabled:
        return messages, CompactResult.no_op(original_tokens)

    threshold = (
        input_token_budget
        if input_token_budget is not None
        else threshold_tokens(model, threshold_ratio=threshold_ratio)
    )
    if original_tokens < threshold:
        return messages, CompactResult.no_op(original_tokens)

    levels_applied: list[int] = [0]
    cleaned_messages, cleared_count = _clear_old_tool_results(
        messages,
        preserve_recent_messages=preserve_recent_messages,
    )
    cleaned_tokens = request_overhead_tokens + estimate_message_tokens(
        cleaned_messages, model=model
    )
    if cleared_count:
        levels_applied.append(2)
        if cleaned_tokens < threshold:
            return cleaned_messages, CompactResult(
                compact_kind="tool_results",
                applied_levels=tuple(levels_applied),
                original_tokens=original_tokens,
                final_tokens=cleaned_tokens,
            )

    compacted_messages, did_compact = await full_compact(
        cleaned_messages,
        model=model,
        api_client=api_client,
        max_tokens=full_compact_max_tokens,
        timeout_seconds=full_compact_timeout_s,
        preserve_recent=preserve_recent_messages,
    )
    if did_compact:
        levels_applied.append(4)
        final_tokens = request_overhead_tokens + estimate_message_tokens(
            compacted_messages, model=model
        )
        return compacted_messages, CompactResult(
            compact_kind="full",
            applied_levels=tuple(levels_applied),
            original_tokens=original_tokens,
            final_tokens=final_tokens,
        )

    # Semantic compact did not produce a usable summary. A completed
    # deterministic cleanup remains useful; otherwise preserve the original.
    return cleaned_messages, CompactResult(
        compact_kind="tool_results" if cleared_count else "none",
        applied_levels=tuple(levels_applied),
        original_tokens=original_tokens,
        final_tokens=cleaned_tokens,
    )
