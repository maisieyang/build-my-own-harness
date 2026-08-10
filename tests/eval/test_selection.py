"""Case selection must happen before an eval performs model calls."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from openharness.eval.selection import select_cases


@dataclass(frozen=True)
class _Case:
    case_id: str


def test_select_cases_returns_only_requested_case() -> None:
    cases = [_Case("one"), _Case("two")]

    assert select_cases(cases, "two") == [_Case("two")]


def test_select_cases_without_id_preserves_full_dataset() -> None:
    cases = [_Case("one"), _Case("two")]

    assert select_cases(cases, None) == cases


def test_select_cases_rejects_unknown_id_with_catalog() -> None:
    with pytest.raises(ValueError, match=r"missing.*one.*two"):
        select_cases([_Case("one"), _Case("two")], "missing")
