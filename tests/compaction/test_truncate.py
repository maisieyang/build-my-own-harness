"""Tests for ``head_tail_truncate`` — P4-T2.2a.

Three responsibility surfaces:

1. **Under-cap pass-through** — no truncation when input fits.
2. **Truncation semantics** — head + marker + tail; total roughly ≤ cap;
   marker records the right dropped-token count.
3. **Edge cases** — empty input, zero cap, marker-doesn't-fit, fallback
   model (no tiktoken encoder).
"""

from __future__ import annotations

import pytest

from openharness.compaction import count_tokens
from openharness.compaction.truncate import head_tail_truncate

# Models for parametrization — gpt-4o has a tiktoken encoder (precise
# path); qwen-plus does not (fallback path). Both must satisfy the same
# contract.
_PRECISE_MODEL = "gpt-4o"
_FALLBACK_MODEL = "qwen-plus"


class TestUnderCapPassthrough:
    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_short_text_returned_unchanged(self, model: str) -> None:
        text = "hello world"
        assert head_tail_truncate(text, cap_tokens=1000, model=model) == text

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_empty_text_returned_unchanged(self, model: str) -> None:
        assert head_tail_truncate("", cap_tokens=100, model=model) == ""

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_text_exactly_at_cap_returned_unchanged(self, model: str) -> None:
        text = "hello world"
        cap = count_tokens(text, model)
        assert head_tail_truncate(text, cap_tokens=cap, model=model) == text


class TestTruncationSemantics:
    """When truncation fires, the result is ``head + marker + tail``."""

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_long_text_contains_marker(self, model: str) -> None:
        # Build a deterministic long string with distinct head + tail
        # anchors so we can verify both regions survive. ``cap=50`` is
        # tight enough to force aggressive middle elision.
        body = "x" * 8000
        text = "HEAD_ANCHOR " + body + " TAIL_ANCHOR"
        result = head_tail_truncate(text, cap_tokens=50, model=model)

        assert "[truncated" in result
        assert "tokens]" in result
        assert "HEAD_ANCHOR" in result
        assert "TAIL_ANCHOR" in result
        # Result is massively shorter than input — middle dropped.
        assert len(result) < 500

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_result_is_shorter_than_input(self, model: str) -> None:
        text = "lorem ipsum " * 5000
        result = head_tail_truncate(text, cap_tokens=200, model=model)
        assert len(result) < len(text)

    def test_result_token_count_close_to_cap_for_precise_model(self) -> None:
        # On tiktoken-supported models we expect exact-ish slicing.
        # Allow some slack for the marker text varying with N (the dropped
        # count's digit width affects marker token cost slightly).
        text = "lorem ipsum dolor sit amet " * 1000
        cap = 500
        result = head_tail_truncate(text, cap_tokens=cap, model=_PRECISE_MODEL)
        result_tokens = count_tokens(result, _PRECISE_MODEL)
        # Within +20 tokens of the cap — the marker estimate uses
        # ``total`` digits which slightly over-reserves.
        assert result_tokens <= cap + 20
        assert result_tokens >= cap - 20

    def test_marker_records_dropped_count(self) -> None:
        # On precise model we know exactly how many tokens are dropped.
        text = "abc def " * 5000
        cap = 100
        total = count_tokens(text, _PRECISE_MODEL)
        result = head_tail_truncate(text, cap_tokens=cap, model=_PRECISE_MODEL)

        # Marker's number should reflect a real drop in the right order.
        # Just check N > 0 and N < total — exact value depends on marker
        # token cost computed inline by the algorithm.
        import re

        match = re.search(r"\[truncated (\d+) tokens\]", result)
        assert match is not None, "marker not present in truncated result"
        dropped = int(match.group(1))
        assert 0 < dropped < total

    def test_head_appears_before_marker_appears_before_tail(self) -> None:
        text = "FIRSTPART" + "z" * 8000 + "LASTPART"
        result = head_tail_truncate(text, cap_tokens=100, model=_PRECISE_MODEL)
        i_head = result.index("FIRSTPART")
        i_marker = result.index("[truncated")
        i_tail = result.index("LASTPART")
        assert i_head < i_marker < i_tail


class TestEdgeCases:
    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_zero_cap_returns_input_unchanged(self, model: str) -> None:
        # Misuse:cap_tokens=0 means "don't truncate at all" by convention
        # (cap is a positive ceiling).
        text = "some content"
        assert head_tail_truncate(text, cap_tokens=0, model=model) == text

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_negative_cap_returns_input_unchanged(self, model: str) -> None:
        text = "some content"
        assert head_tail_truncate(text, cap_tokens=-5, model=model) == text

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_pathological_tiny_cap_degrades_to_head_only(self, model: str) -> None:
        # cap_tokens = 5: marker alone would cost ~10+ tokens, so the
        # algorithm degrades to head-only without a marker.
        text = "FIRSTPART " + "filler " * 5000
        result = head_tail_truncate(text, cap_tokens=5, model=model)

        # No marker — the cap was too small for one.
        assert "[truncated" not in result
        # FIRSTPART (or some part of it) appears at the start.
        assert result.startswith("F")  # head was preserved at least

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    def test_head_and_tail_anchors_preserved_input_massively_shortened(self, model: str) -> None:
        # Verify the head + tail anchors survive intact and the overall
        # result is much shorter — proxy for "head and tail don't include
        # large overlapping middle slices".
        text = "AAA " + ("middle " * 1000) + " ZZZ"
        result = head_tail_truncate(text, cap_tokens=50, model=model)

        assert "AAA" in result
        assert "ZZZ" in result
        # ≥ 5x shorter than original — middle elided.
        assert len(result) < len(text) / 5
