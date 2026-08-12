"""Intentionally incomplete baseline tests for the pricing fixture."""

from pricing import apply_discount


def test_applies_percentage_discount() -> None:
    assert apply_discount(200, 25) == 150


def test_zero_discount_keeps_subtotal() -> None:
    assert apply_discount(80, 0) == 80
