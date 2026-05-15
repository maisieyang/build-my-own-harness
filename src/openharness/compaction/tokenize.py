"""Token counting for compaction — P4-T1.

Single public function :func:`count_tokens` — given a string + a model
name, return an integer estimate of how many tokens the LLM provider
will charge for that string.

Per D14.2:

1. **tiktoken when supported** — OpenAI maintains encodings for
   ``gpt-4o``, ``gpt-4o-mini``, ``gpt-3.5-turbo``, and a handful of
   older models. When ``tiktoken.encoding_for_model(model)`` succeeds,
   that's the truth.
2. **Byte-ratio fallback** — when no encoding is registered (Qwen
   models, Anthropic-native, any unrecognized name), use Codex's
   published approximation::

       tokens ≈ len(text.encode("utf-8")) // 4

   Quirky but documented (`OpenAI Codex compaction guide`); errs on the
   high side for ASCII, low for CJK / emoji-dense text. Good enough for
   the cap-based triggering in Layer 1 — if we over-estimate slightly,
   we truncate slightly more conservatively (safe direction).

The choice is intentionally a single function (not a class):callers
are stateless and pure-call; encoding lookup is cheap (tiktoken caches
internally). Future Phase 5+ may swap to a Protocol if a third
"token counter" implementation appears (e.g., Anthropic-native API
that returns ``input_tokens`` in response).
"""

from __future__ import annotations

from functools import lru_cache

import tiktoken


# Result of an encoding lookup — either a real Encoding or the sentinel
# ``None`` meaning "fall back to byte ratio". Cached so repeated calls
# for the same model don't re-resolve.
@lru_cache(maxsize=32)
def _get_encoding(model: str) -> tiktoken.Encoding | None:
    """Return a tiktoken Encoding for ``model`` if registered;else None.

    ``encoding_for_model`` raises ``KeyError`` on unknown models — we
    catch and return None so the caller falls back to byte ratio.
    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return None


# Byte-to-token ratio for the fallback path. Sourced from OpenAI's Codex
# documentation (``num_bytes / 4`` recommendation). Wraps a literal so
# the magic constant has a name in the codebase.
_BYTE_RATIO_DIVISOR = 4


def count_tokens(text: str, model: str) -> int:
    """Estimate the number of tokens in ``text`` for the given ``model``.

    Args:
        text: the string to count. Empty string returns 0 without
            invoking tiktoken.
        model: provider-specific model name. ``"gpt-4o"`` / ``"gpt-4o-mini"``
            etc. get exact tiktoken counts; ``"qwen-plus"`` / unknown
            names get byte-ratio fallback.

    Returns:
        Non-negative integer token estimate. Exact for tiktoken-supported
        models, approximate (``len_bytes // 4``) for unsupported ones.
    """
    if not text:
        return 0
    encoding = _get_encoding(model)
    if encoding is not None:
        return len(encoding.encode(text))
    return len(text.encode("utf-8")) // _BYTE_RATIO_DIVISOR
