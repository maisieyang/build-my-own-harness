"""``head_tail_truncate`` — the Codex-style truncation algorithm.

Per D14.1 Layer 1: when a string exceeds the token cap, keep the **head**
and the **tail** and drop the middle, replacing it with a marker that
records how many tokens were removed.

Domain rationale (from the user's observation about coding workflows):
in a tool's output, the *start* tends to carry identifying context
(file path, imports, signatures, command echo) and the *end* tends to
carry the actionable result (errors, final value, exit codes). The
middle is usually bulk:repeated boilerplate, scrollback, or detail
that the LLM rarely needs verbatim.

Algorithm:

1. Count tokens. If under cap, return unchanged.
2. Reserve room for the marker (its own token cost — we estimate by
   formatting a placeholder).
3. Split the remaining budget 50/50:half head, half tail.
4. Use tiktoken for exact slicing when the model has an encoder;
   fall back to character-ratio slicing for unknown models (Qwen
   etc., per D14.2 fallback path).

Edge case: if ``cap_tokens`` is so small that the marker alone exceeds
it, degrade to head-only truncation with no marker.
"""

from __future__ import annotations

from openharness.compaction.tokenize import _get_encoding, count_tokens

# Marker template. ``{n}`` is the number of *tokens* dropped (not bytes
# or characters) so the LLM reading the marker sees a unit consistent
# with how it's billed.
_MARKER_TEMPLATE = "\n... [truncated {n} tokens] ...\n"


def head_tail_truncate(text: str, cap_tokens: int, model: str) -> str:
    """Truncate ``text`` to roughly ``cap_tokens`` keeping head + tail.

    Args:
        text: the string to maybe truncate. Empty / under-cap text is
            returned unchanged.
        cap_tokens: target maximum tokens after truncation. Must be > 0
            for any truncation to happen (≤ 0 returns input unchanged).
        model: provider-specific model name used for token counting;
            tiktoken-supported models get exact slicing, others get
            character-ratio approximation.

    Returns:
        The original text if it fits, or a ``head + marker + tail``
        composition that approximates ``cap_tokens`` total.
    """
    if not text or cap_tokens <= 0:
        return text

    total = count_tokens(text, model)
    if total <= cap_tokens:
        return text

    # Marker's own token cost — formatted with a sample value of the
    # right magnitude (``total``) so estimate matches reality.
    marker_tokens = count_tokens(_MARKER_TEMPLATE.format(n=total), model)

    # Pathological cap: marker alone won't fit. Degrade to head-only,
    # no marker — at least the LLM still sees the beginning.
    if marker_tokens >= cap_tokens:
        return _slice_head(text, cap_tokens, model, total)

    budget = cap_tokens - marker_tokens
    head_tokens = budget // 2
    tail_tokens = budget - head_tokens  # absorbs the odd token

    head, tail = _split_head_tail(text, head_tokens, tail_tokens, model, total)
    dropped_tokens = total - head_tokens - tail_tokens
    return head + _MARKER_TEMPLATE.format(n=dropped_tokens) + tail


def _split_head_tail(
    text: str,
    head_tokens: int,
    tail_tokens: int,
    model: str,
    total: int,
) -> tuple[str, str]:
    """Slice ``text`` into ``(head, tail)`` of the requested token sizes.

    Uses tiktoken when an encoding is registered for the model
    (exact);falls back to character-ratio when not (approximate). The
    fallback assumes uniform token density — fine for monolithic output
    streams like file contents, slightly off for mixed-content streams.
    """
    encoding = _get_encoding(model)
    if encoding is not None:
        tokens = encoding.encode(text)
        head = encoding.decode(tokens[:head_tokens])
        tail = encoding.decode(tokens[-tail_tokens:])
        return head, tail

    # Fallback: character ratio. ``total`` is the byte-ratio token count;
    # ``len(text)`` is character count. Map token slice → char slice.
    chars_per_token = len(text) / total
    head_chars = int(head_tokens * chars_per_token)
    tail_chars = int(tail_tokens * chars_per_token)
    return text[:head_chars], text[-tail_chars:] if tail_chars > 0 else ""


def _slice_head(text: str, cap_tokens: int, model: str, total: int) -> str:
    """Head-only truncation used when cap is smaller than the marker.

    No marker added — the cap is too small to afford one.
    """
    encoding = _get_encoding(model)
    if encoding is not None:
        tokens = encoding.encode(text)
        return encoding.decode(tokens[:cap_tokens])
    chars_per_token = len(text) / max(total, 1)
    return text[: max(1, int(cap_tokens * chars_per_token))]
