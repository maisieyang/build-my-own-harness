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

from engine.conftest import _AllowAllChecker
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
        permission_checker=_AllowAllChecker(),
        system_prompt="you are a test harness",
        cwd=Path("/tmp"),
        model="qwen-plus",
    )


class TestQueryContext:
    def test_required_fields_round_trip(self, context: QueryContext) -> None:
        assert context.system_prompt == "you are a test harness"
        assert context.cwd == Path("/tmp")
        # Both D7.2 hand-offs now cashed:
        # - tool_registry tightened to ToolRegistry in P2-T2.2e
        # - permission_checker tightened to PermissionChecker Protocol in P2-T4.4c
        assert isinstance(context.tool_registry, ToolRegistry)
        # Protocol structural type — no runtime isinstance check, but the
        # binding itself satisfies the type contract.
        assert hasattr(context.permission_checker, "evaluate")

    def test_max_turns_default_matches_boundary_contract(self, context: QueryContext) -> None:
        # decisions/06-phase-2-boundary.md D6.1: hybrid loop exit, 20 default.
        assert context.max_turns == 20

    def test_max_turns_override_accepted(self) -> None:
        ctx = QueryContext(
            api_client=_stub_client(),
            tool_registry=ToolRegistry(),
            permission_checker=_AllowAllChecker(),
            system_prompt="",
            cwd=Path("/tmp"),
            model="qwen-plus",
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


class TestSkillStoreField:
    """P5c-T2.2a — ``skill_store`` field default + injection.

    Default is an :class:`EmptySkillStore` so callers who don't care
    about Phase 5c don't pay any cost; setting it requires no API
    changes beyond passing the kwarg.
    """

    def test_default_is_empty_store(self, context: QueryContext) -> None:
        from openharness.skills.store import EmptySkillStore

        assert isinstance(context.skill_store, EmptySkillStore)
        # Empty by construction — discover() yields nothing.
        assert context.skill_store.discover() == {}

    def test_custom_store_injected(self, tmp_path: Path) -> None:
        from openharness.skills.store import FilesystemSkillStore

        (tmp_path / "x.md").write_text(
            "---\nname: x\ndescription: y\n---\nbody\n",
            encoding="utf-8",
        )
        store = FilesystemSkillStore(global_dir=tmp_path)
        ctx = QueryContext(
            api_client=_stub_client(),
            tool_registry=ToolRegistry(),
            permission_checker=_AllowAllChecker(),
            system_prompt="",
            cwd=tmp_path,
            model="qwen-plus",
            skill_store=store,
        )
        assert ctx.skill_store is store
        assert "x" in ctx.skill_store.discover()
