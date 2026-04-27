"""Smoke tests — verify the package can be imported and basic invariants hold."""

from __future__ import annotations

import openharness


def test_package_has_version() -> None:
    assert isinstance(openharness.__version__, str)
    assert len(openharness.__version__) > 0


def test_version_is_pep440_like() -> None:
    parts = openharness.__version__.split(".")
    assert len(parts) >= 2
    for part in parts:
        assert part[0].isdigit(), f"version segment {part!r} must start with a digit"
