"""The spec. These assertions are the verification gate — do NOT weaken them."""

from mathy import add, is_even


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_is_even():
    assert is_even(4) is True
    assert is_even(7) is False
    assert is_even(0) is True
