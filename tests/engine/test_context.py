"""Tests for :class:`QueryContext` — P2-T1 sub-unit 1a.

The dataclass has three load-bearing behaviors to verify:

1. Frozen — fields cannot be reassigned (forces ``dataclasses.replace`` for changes)
2. ``max_turns=None`` leaves the top-level interactive loop model-terminated
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
        system_prompt="you are a test harness",
        cwd=Path("/tmp"),
        model="qwen-plus",
    )


class TestQueryContext:
    def test_required_fields_round_trip(self, context: QueryContext) -> None:
        assert context.system_prompt == "you are a test harness"
        assert context.cwd == Path("/tmp")
        # The registry is a concrete runtime collaborator.
        assert isinstance(context.tool_registry, ToolRegistry)

    def test_max_turns_defaults_to_model_terminated(self, context: QueryContext) -> None:
        assert context.max_turns is None

    def test_full_compact_timeout_allows_long_context_summarization(
        self, context: QueryContext
    ) -> None:
        assert context.compact_full_timeout_s == 120.0

    def test_compact_preserves_twelve_recent_messages_by_default(
        self, context: QueryContext
    ) -> None:
        assert context.compact_preserve_recent_messages == 12

    def test_max_turns_override_accepted(self) -> None:
        ctx = QueryContext(
            api_client=_stub_client(),
            tool_registry=ToolRegistry(),
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
        assert context.max_turns is None
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
            system_prompt="",
            cwd=tmp_path,
            model="qwen-plus",
            skill_store=store,
        )
        assert ctx.skill_store is store
        assert "x" in ctx.skill_store.discover()


class TestAgentDepthFields:
    """P6-T1 (D16.5) — sub-agent recursion tracking on QueryContext."""

    def test_default_depth_is_zero(self, context: QueryContext) -> None:
        # Top-level ``oh ask`` invocations construct with depth 0.
        assert context.agent_depth == 0

    def test_default_max_agent_depth_is_three(self, context: QueryContext) -> None:
        # Matches the Settings default — engine cap is the same value as
        # the env-driven Settings field.
        assert context.max_agent_depth == 3

    def test_dataclasses_replace_bumps_depth(self, context: QueryContext) -> None:
        # The sub-agent construction pattern — `SpawnAgent.execute` will
        # call `dataclasses.replace(parent, agent_depth=parent.agent_depth + 1)`.
        sub = dataclasses.replace(context, agent_depth=context.agent_depth + 1)
        assert sub.agent_depth == 1
        assert sub.max_agent_depth == 3  # inherited

    def test_max_depth_propagates_through_replace(self, context: QueryContext) -> None:
        # `max_agent_depth` MUST stay constant across replace so every level
        # of the recursion checks against the same cap. If the cap were
        # somehow re-derived per level the depth bound would not be a hard
        # ceiling.
        sub = dataclasses.replace(context, agent_depth=2)
        assert sub.max_agent_depth == context.max_agent_depth

    def test_explicit_max_depth_override(self, tmp_path: Path) -> None:
        ctx = QueryContext(
            api_client=_stub_client(),
            tool_registry=ToolRegistry(),
            system_prompt="",
            cwd=tmp_path,
            model="qwen-plus",
            max_agent_depth=5,
        )
        assert ctx.max_agent_depth == 5
        assert ctx.agent_depth == 0  # default unchanged


class TestExecutionEnvField:
    """P7-T2 (D17.3) — `execution_env` field on QueryContext."""

    def test_default_is_HostExecution_singleton(self, context: QueryContext) -> None:
        from openharness.execution.host import _HOST_EXECUTION, HostExecution

        assert isinstance(context.execution_env, HostExecution)
        # Default factory points at the module singleton.
        assert context.execution_env is _HOST_EXECUTION

    def test_custom_execution_env_injected(self, tmp_path: Path) -> None:
        from openharness.execution import ExecutionEnvironment, ProcessResult

        class _FakeEnv:
            async def run_command(
                self, command: str, cwd: Path, timeout: float | None = None
            ) -> ProcessResult:
                return ProcessResult(output=f"fake:{command}", exit_code=0)

        fake: ExecutionEnvironment = _FakeEnv()
        ctx = QueryContext(
            api_client=_stub_client(),
            tool_registry=ToolRegistry(),
            system_prompt="",
            cwd=tmp_path,
            model="qwen-plus",
            execution_env=fake,
        )
        assert ctx.execution_env is fake

    def test_dataclasses_replace_preserves_execution_env(self, context: QueryContext) -> None:
        # Sub-agent inheritance (P6) MUST carry execution_env through
        # `dataclasses.replace`. If this breaks, sandboxed sub-agents
        # would silently revert to HostExecution.
        sub = dataclasses.replace(context, agent_depth=1)
        assert sub.execution_env is context.execution_env
