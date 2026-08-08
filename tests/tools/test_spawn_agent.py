"""Tests for ``SpawnAgent`` — P6-T3.

Five surfaces:

1. **Static contract** — name / is_read_only / input_model / description.
2. **Happy path** — stub LLM returns end_turn with text → sub-agent
   returns the text as ToolResult.
3. **Depth bound** (D16.5) — parent at max depth → refuses to spawn.
4. **LoopLimitExceeded** (D16.4) — sub-agent exhausts max_turns →
   is_error=True with descriptive message.
5. **Defensive paths** — parent_query=None → is_error;empty content →
   sentinel response.

End-to-end recursion (real sub-agent dispatching real tools) is left to
P6-T6 integration smoke;these tests stub the api_client so the sub-agent's
loop runs in <1ms.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast

import pytest

from engine.conftest import _StubApiClient
from openharness.engine.context import QueryContext
from openharness.execution import BoundaryVerification, EnforcedBoundary, ExecutionEffect
from openharness.permissions import PermissionRuntime, workspace_runtime_profile
from openharness.protocols import ApiMessageCompleteEvent
from openharness.tools import SpawnAgent, SpawnAgentInput, ToolRegistry
from openharness.tools.base import ToolExecutionContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.execution import SandboxSession
    from openharness.protocols import ApiStreamEvent, ConversationMessage


# --------------------------------------------------------------------------- #
# Event helpers                                                                #
# --------------------------------------------------------------------------- #


def _end_turn_with_text(text: str) -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
    )


def _fake_tool_use(
    *, tool_use_id: str = "toolu_x", tool_name: str = "X"
) -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": {},
                    }
                ],
            },
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "tool_use",
        }
    )


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _make_parent_context(
    events_per_turn: list[list[ApiStreamEvent]],
    *,
    agent_depth: int = 0,
    max_agent_depth: int = 3,
    tmp_path: Path,
) -> QueryContext:
    """Construct a parent QueryContext with a stub api_client.

    The same stub drives the sub-agent's `run_query` — because Sub-agent
    inherits `api_client` from the parent. Tests provide
    `events_per_turn` for the SUB-AGENT's turns.
    """
    return QueryContext(
        api_client=_StubApiClient(events_per_turn),
        tool_registry=ToolRegistry(),  # no need to register tools for these tests
        system_prompt="parent prompt",
        cwd=tmp_path,
        model="qwen-plus",
        agent_depth=agent_depth,
        max_agent_depth=max_agent_depth,
    )


# --------------------------------------------------------------------------- #
# 1. Static contract                                                          #
# --------------------------------------------------------------------------- #


class TestSpawnAgentStatic:
    def test_default_name_is_Agent(self) -> None:
        assert SpawnAgent.name == "Agent"

    def test_is_read_only_false(self) -> None:
        # Sub-agent can use mutating tools → AuthZ Tier 3 strict path.
        assert SpawnAgent.is_read_only is False

    def test_trust_source_local(self) -> None:
        # P5-T5 provenance — built-in tool.
        assert SpawnAgent.trust_source == "local"

    def test_input_model_is_SpawnAgentInput(self) -> None:
        assert SpawnAgent.input_model is SpawnAgentInput

    def test_description_mentions_delegation_and_depth(self) -> None:
        # LLM uses this to decide when to call. Must hint at the
        # "isolated context" and "bounded recursion" semantics.
        desc = SpawnAgent.description.lower()
        assert "sub-task" in desc or "delegate" in desc
        assert "context" in desc

    def test_input_model_requires_description_and_prompt(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SpawnAgentInput()  # type: ignore[call-arg]
        # Both fields required.
        ok = SpawnAgentInput(description="d", prompt="p")
        assert ok.description == "d"
        assert ok.prompt == "p"


# --------------------------------------------------------------------------- #
# 2. Happy path                                                               #
# --------------------------------------------------------------------------- #


class TestSpawnAgentHappyPath:
    async def test_returns_sub_agent_text(self, tmp_path: Path) -> None:
        # Stub sub-agent's API client: one turn, end_turn with text.
        parent_ctx = _make_parent_context(
            events_per_turn=[[_end_turn_with_text("sub-agent says: hello world")]],
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)

        result = await tool.execute(
            SpawnAgentInput(description="say hello", prompt="say hi"),
            exec_ctx,
        )
        assert result.is_error is False
        assert "hello world" in result.output

    async def test_multi_textblock_message_joined(self, tmp_path: Path) -> None:
        # Sub-agent's final message may contain multiple TextBlocks.
        event = ApiMessageCompleteEvent.model_validate(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "first part"},
                        {"type": "text", "text": "second part"},
                    ],
                },
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            }
        )
        parent_ctx = _make_parent_context(
            events_per_turn=[[event]],
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)

        result = await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )
        assert "first part" in result.output
        assert "second part" in result.output

    async def test_sub_agent_inherits_parent_fields(self, tmp_path: Path) -> None:
        # Sub-agent's QueryContext must inherit api_client / tool_registry /
        # canonical profile/boundary / cwd / model from parent. We verify the
        # captured request's model matches the parent's.
        parent_ctx = _make_parent_context(
            events_per_turn=[[_end_turn_with_text("ok")]],
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)

        await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )

        # The stub captured the sub-agent's request.
        stub = parent_ctx.api_client
        assert isinstance(stub, _StubApiClient)
        assert len(stub.captured_requests) == 1
        # Sub-agent used the same model as the parent.
        assert stub.captured_requests[0].model == "qwen-plus"

    async def test_g0_sub_agent_inherits_verified_permission_runtime(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pin the delegated-runtime coverage path before G7 tightens it."""
        profile = workspace_runtime_profile()
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="g0-recording",
            backend_version="1",
            covered_effects=(
                ExecutionEffect.COMMAND,
                ExecutionEffect.FILE_READ,
                ExecutionEffect.FILE_WRITE,
                ExecutionEffect.FILE_SEARCH,
            ),
            verification=BoundaryVerification.VERIFIED,
        )
        runtime = PermissionRuntime(profile=profile, boundary=boundary)
        sandbox_session = cast("SandboxSession", object())
        parent_ctx = dataclasses.replace(
            _make_parent_context(
                events_per_turn=[],
                tmp_path=tmp_path,
            ),
            sandbox_session=sandbox_session,
            runtime_permission_profile=profile,
            enforced_boundary=boundary,
            permission_runtime=runtime,
        )
        captured: list[QueryContext] = []

        async def _capture_context(
            messages: list[ConversationMessage],
            context: QueryContext,
        ) -> AsyncIterator[ApiStreamEvent]:
            del messages
            captured.append(context)
            yield _end_turn_with_text("ok")

        monkeypatch.setattr("openharness.tools.spawn_agent.run_query", _capture_context)

        result = await SpawnAgent().execute(
            SpawnAgentInput(description="capture", prompt="capture"),
            ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx),
        )

        assert result.is_error is False
        assert len(captured) == 1
        child = captured[0]
        assert child.tool_registry is parent_ctx.tool_registry
        assert child.sandbox_session is sandbox_session
        assert child.runtime_permission_profile is profile
        assert child.enforced_boundary is boundary
        assert child.permission_runtime is runtime

    async def test_sub_agent_system_prompt_override(self, tmp_path: Path) -> None:
        parent_ctx = _make_parent_context(
            events_per_turn=[[_end_turn_with_text("ok")]],
            tmp_path=tmp_path,
        )
        tool = SpawnAgent(system_prompt="you are a research sub-agent")
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)
        await tool.execute(SpawnAgentInput(description="x", prompt="x"), exec_ctx)

        stub = parent_ctx.api_client
        assert isinstance(stub, _StubApiClient)
        # Sub-agent's system prompt is the constructor-provided one, NOT parent's.
        assert stub.captured_requests[0].system == "you are a research sub-agent"

    async def test_sub_agent_falls_back_to_parent_system_prompt(self, tmp_path: Path) -> None:
        parent_ctx = _make_parent_context(
            events_per_turn=[[_end_turn_with_text("ok")]],
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()  # no system_prompt provided
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)
        await tool.execute(SpawnAgentInput(description="x", prompt="x"), exec_ctx)

        stub = parent_ctx.api_client
        assert isinstance(stub, _StubApiClient)
        # Falls back to parent's system_prompt.
        assert stub.captured_requests[0].system == "parent prompt"


# --------------------------------------------------------------------------- #
# 3. Depth bound (D16.5)                                                      #
# --------------------------------------------------------------------------- #


class TestSpawnAgentDepthBound:
    async def test_at_max_depth_refuses_to_spawn(self, tmp_path: Path) -> None:
        # Parent already at depth 3, max=3 → 3+1 > 3 → refuse.
        parent_ctx = _make_parent_context(
            events_per_turn=[],  # never called — refusal is before run_query
            agent_depth=3,
            max_agent_depth=3,
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)

        result = await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )
        assert result.is_error is True
        assert "max agent depth" in result.output.lower()
        assert "3" in result.output

    async def test_one_below_max_can_spawn(self, tmp_path: Path) -> None:
        # Parent at depth 2, max=3 → 2+1 = 3 ≤ 3 → allow.
        parent_ctx = _make_parent_context(
            events_per_turn=[[_end_turn_with_text("ok")]],
            agent_depth=2,
            max_agent_depth=3,
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)
        result = await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )
        assert result.is_error is False

    async def test_zero_cap_disables_spawning(self, tmp_path: Path) -> None:
        # Parent at depth 0, max=0 → 0+1 > 0 → refuse. Kill-switch behavior.
        parent_ctx = _make_parent_context(
            events_per_turn=[],
            agent_depth=0,
            max_agent_depth=0,
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)
        result = await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )
        assert result.is_error is True
        assert "max agent depth" in result.output.lower()


# --------------------------------------------------------------------------- #
# 4. LoopLimitExceeded (D16.4)                                                #
# --------------------------------------------------------------------------- #


class TestSpawnAgentLoopLimit:
    async def test_loop_limit_returns_is_error(self, tmp_path: Path) -> None:
        # Sub-agent uses max_turns=2 (instance config); stub emits tool_use
        # on every turn so the sub-agent never reaches end_turn before
        # exhausting its budget. The sub-agent's run_query will raise
        # LoopLimitExceeded, which SpawnAgent.execute catches.
        # The fake tool need not exist for this test — the loop exits
        # before tool dispatch via the max_turns check after the second
        # tool_use turn.
        from tools.conftest import _FakeTool

        registry = ToolRegistry()
        registry.register(_FakeTool())
        parent_ctx = QueryContext(
            api_client=_StubApiClient(
                [
                    [_fake_tool_use(tool_use_id="t1", tool_name="Fake")],
                    [_fake_tool_use(tool_use_id="t2", tool_name="Fake")],
                ],
            ),
            tool_registry=registry,
            system_prompt="",
            cwd=tmp_path,
            model="qwen-plus",
            agent_depth=0,
            max_agent_depth=3,
        )
        tool = SpawnAgent(max_turns=2)
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)
        result = await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )
        assert result.is_error is True
        assert "max_turns=2" in result.output


# --------------------------------------------------------------------------- #
# 5. Defensive paths                                                          #
# --------------------------------------------------------------------------- #


class TestSpawnAgentDefensive:
    async def test_parent_query_none_returns_is_error(self, tmp_path: Path) -> None:
        # Defensive — never happens via engine but a direct test caller
        # could construct ToolExecutionContext without parent_query.
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=None)
        result = await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )
        assert result.is_error is True
        assert "outside an active query context" in result.output

    async def test_no_text_blocks_returns_sentinel(self, tmp_path: Path) -> None:
        # Sub-agent stops with end_turn but no text in the final message.
        # We never hit this in practice (every LLM emits text) but defend
        # against the corner case.
        empty_event = ApiMessageCompleteEvent.model_validate(
            {
                "message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 1, "output_tokens": 0},
                "stop_reason": "end_turn",
            }
        )
        parent_ctx = _make_parent_context(
            events_per_turn=[[empty_event]],
            tmp_path=tmp_path,
        )
        tool = SpawnAgent()
        exec_ctx = ToolExecutionContext(cwd=tmp_path, parent_query=parent_ctx)
        result = await tool.execute(
            SpawnAgentInput(description="x", prompt="x"),
            exec_ctx,
        )
        # Not an error per se — just empty. Sub-agent ran to end_turn cleanly.
        assert result.is_error is False
        assert "(no text response)" in result.output
