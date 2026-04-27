"""Tests for UsageSnapshot."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.protocols.usage import UsageSnapshot


class TestUsageSnapshot:
    def test_construct(self) -> None:
        usage = UsageSnapshot(input_tokens=100, output_tokens=50)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_total_tokens(self) -> None:
        assert UsageSnapshot(input_tokens=100, output_tokens=50).total_tokens == 150

    def test_zero_tokens_allowed(self) -> None:
        usage = UsageSnapshot(input_tokens=0, output_tokens=0)
        assert usage.total_tokens == 0

    def test_negative_input_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UsageSnapshot(input_tokens=-1, output_tokens=0)

    def test_negative_output_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UsageSnapshot(input_tokens=0, output_tokens=-5)

    def test_roundtrip(self) -> None:
        original = UsageSnapshot(input_tokens=42, output_tokens=17)
        loaded = UsageSnapshot.model_validate_json(original.model_dump_json())
        assert loaded == original

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            UsageSnapshot.model_validate({"input_tokens": 1, "output_tokens": 1, "cache_tokens": 5})

    def test_total_tokens_not_serialized(self) -> None:
        # @property (not @computed_field) → excluded from model_dump output, matching
        # Anthropic wire format which has no ``total_tokens`` field.
        usage = UsageSnapshot(input_tokens=10, output_tokens=20)
        assert usage.model_dump() == {"input_tokens": 10, "output_tokens": 20}
