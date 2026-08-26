"""Intentionally broken pricing implementation for the workflow journey."""


def apply_discount(subtotal: float, discount_percent: float) -> float:
    """Return the subtotal after applying a percentage discount."""
    return subtotal - discount_percent
