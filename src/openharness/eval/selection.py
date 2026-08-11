"""Shared pre-inference case selection for manual eval runs."""

from __future__ import annotations

from typing import Protocol, TypeVar


class HasCaseId(Protocol):
    """Structural contract shared by every eval sample type."""

    @property
    def case_id(self) -> str:
        """Stable dataset identifier."""
        ...


CaseT = TypeVar("CaseT", bound=HasCaseId)


def select_cases(cases: list[CaseT], case_id: str | None) -> list[CaseT]:
    """Return one requested case before inference, or the complete dataset."""
    if case_id is None:
        return cases
    selected = [case for case in cases if case.case_id == case_id]
    if selected:
        return selected
    available = ", ".join(case.case_id for case in cases)
    raise ValueError(f"Unknown eval case {case_id!r}; available cases: {available}")
