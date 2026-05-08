"""Tests for :class:`QueryContext` — P2-T1 sub-unit 1a.

The dataclass has three load-bearing behaviors to verify:

1. Frozen — fields cannot be reassigned (forces ``dataclasses.replace`` for changes)
2. ``max_turns=20`` default matches ``decisions/06-phase-2-boundary.md`` D6.1
3. ``dataclasses.replace`` produces a new instance, leaving the original intact
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from openharness.api import OpenAICompatibleApiClient
from openharness.engine.context import QueryContext
from openharness.tools import ToolRegistry


def _stub_client() -> OpenAICompatibleApiClient:
    """A typed stand-in so QueryContext construction satisfies mypy --strict.

    QueryContext does not call any methods on the client at this layer, so a
    spec'd Mock is sufficient.
    """
    return cast("OpenAICompatibleApiClient", Mock(spec=OpenAICompatibleApiClient))


@pytest.fixture
def context() -> QueryContext:
    """A baseline QueryContext most tests can use as-is or via ``replace``."""
    return QueryContext(
        api_client=_stub_client(),
        tool_registry=ToolRegistry(),
        permission_checker=object(),
        system_prompt="you are a test harness",
        cwd=Path("/tmp"),
    )


class TestQueryContext:
    def test_required_fields_round_trip(self, context: QueryContext) -> None:
        assert context.system_prompt == "you are a test harness"
        assert context.cwd == Path("/tmp")
        # tool_registry now tightened to ToolRegistry (P2-T2 hand-off);
        # permission_checker stays object until P2-T6.
        assert isinstance(context.tool_registry, ToolRegistry)
        assert context.permission_checker is not None

    def test_max_turns_default_matches_boundary_contract(self, context: QueryContext) -> None:
        # decisions/06-phase-2-boundary.md D6.1: hybrid loop exit, 20 default.
        assert context.max_turns == 20

    def test_max_turns_override_accepted(self) -> None:
        ctx = QueryContext(
            api_client=_stub_client(),
            tool_registry=ToolRegistry(),
            permission_checker=object(),
            system_prompt="",
            cwd=Path("/tmp"),
            max_turns=5,
        )
        assert ctx.max_turns == 5

    def test_frozen_field_assignment_raises(self, context: QueryContext) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            context.max_turns = 99  # type: ignore[misc]

    def test_replace_creates_new_instance_without_mutating_original(
        self, context: QueryContext
    ) -> None:
        replaced = dataclasses.replace(
            context,
            max_turns=42,
            system_prompt="updated",
        )
        # original unchanged
        assert context.max_turns == 20
        assert context.system_prompt == "you are a test harness"
        # new instance carries overrides
        assert replaced.max_turns == 42
        assert replaced.system_prompt == "updated"
        assert replaced is not context
