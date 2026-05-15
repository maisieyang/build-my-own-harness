"""Tests for ``count_tokens`` — P4-T1.

Two paths to verify:

1. **tiktoken-supported models** — ``gpt-4o`` / ``gpt-4o-mini`` etc.
   should return exact counts (matching ``tiktoken.encoding_for_model``).
2. **Fallback models** — anything unknown (``qwen-plus`` is our Phase 1
   target!) should return ``len(text.encode("utf-8")) // 4``.

Plus the boring cases:empty string → 0; very large input completes
fast (we cache encoding lookups per-model);same model called many
times reuses cache.
"""

from __future__ import annotations

import time

import tiktoken

from openharness.compaction import count_tokens
from openharness.compaction.tokenize import _BYTE_RATIO_DIVISOR


class TestTiktokenSupportedModels:
    """For models tiktoken recognizes, we should match its own count exactly."""

    def test_gpt4o_count_matches_tiktoken_exactly(self) -> None:
        text = "Hello, world. This is a test of token counting accuracy."
        encoding = tiktoken.encoding_for_model("gpt-4o")
        expected = len(encoding.encode(text))

        assert count_tokens(text, "gpt-4o") == expected

    def test_gpt4o_mini_count_matches_tiktoken_exactly(self) -> None:
        text = "function fizzbuzz(n: int) -> str:\n    pass\n"
        encoding = tiktoken.encoding_for_model("gpt-4o-mini")
        expected = len(encoding.encode(text))

        assert count_tokens(text, "gpt-4o-mini") == expected

    def test_count_increases_with_length(self) -> None:
        short = count_tokens("hi", "gpt-4o")
        long = count_tokens("hi " * 100, "gpt-4o")
        assert long > short


class TestFallbackModels:
    """For models tiktoken doesn't know, use bytes // 4."""

    def test_qwen_falls_back_to_byte_ratio(self) -> None:
        text = "hello world"  # 11 ASCII bytes → 11 // 4 = 2
        assert count_tokens(text, "qwen-plus") == 11 // _BYTE_RATIO_DIVISOR

    def test_claude_native_name_falls_back(self) -> None:
        # If/when a Phase 5+ Anthropic-native client appears, model names
        # like "claude-opus-4.7" won't be in tiktoken's registry.
        text = "x" * 100  # 100 ASCII bytes → 25 tokens
        assert count_tokens(text, "claude-opus-4.7") == 100 // _BYTE_RATIO_DIVISOR

    def test_unknown_model_falls_back(self) -> None:
        text = "abcd" * 100  # 400 bytes → 100 tokens
        assert count_tokens(text, "some-future-model") == 400 // _BYTE_RATIO_DIVISOR

    def test_utf8_multibyte_counted_by_bytes(self) -> None:
        # CJK chars are 3 bytes each in UTF-8. The fallback over-counts
        # tokens for these (real tokenizers usually do 1 token per 1-2
        # CJK chars), but for CAP-based triggering the bias is safe-side.
        text = "你好世界"  # 4 chars x 3 bytes = 12 bytes → 3 "tokens"
        assert count_tokens(text, "qwen-plus") == 12 // _BYTE_RATIO_DIVISOR

    def test_empty_string_returns_zero_under_fallback(self) -> None:
        assert count_tokens("", "qwen-plus") == 0


class TestEdgeCases:
    def test_empty_string_returns_zero_for_known_model(self) -> None:
        # Short-circuit before invoking tiktoken — saves an unneeded
        # encoding lookup.
        assert count_tokens("", "gpt-4o") == 0

    def test_large_text_completes_quickly(self) -> None:
        # 1 MB of text — should finish well under a second even with
        # real tiktoken encoding.
        text = "lorem ipsum dolor sit amet " * 40_000  # ~1 MB
        start = time.monotonic()
        result = count_tokens(text, "gpt-4o")
        elapsed = time.monotonic() - start

        assert result > 0
        assert elapsed < 1.0, f"tokenization took {elapsed:.2f}s, expected < 1s"

    def test_repeated_calls_same_model_consistent(self) -> None:
        # Cache should make repeated calls identical (deterministic).
        text = "consistency check"
        results = {count_tokens(text, "gpt-4o") for _ in range(50)}
        assert len(results) == 1, "count varied across calls for same model"
