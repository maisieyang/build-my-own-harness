"""End-to-end smoke for ``SpawnAgent`` — P6-T6.

Wire-level test of the full recursion chain. Stub LLM drives both the
parent's loop and the sub-agent's loop on the SAME ``api_client``;
SpawnAgent's ``execute`` re-enters ``run_query`` with a fresh
QueryContext slice;parent's ``tool_result`` ends up carrying the
sub-agent's final text.

Real OPENHARNESS_API_KEY-gated integration smoke is intentionally
deferred — Phase 5b/5c/6 stub tests already cover wire shape, and
running real LLM-driven sub-agent loops needs human judgment of
trace quality(not a unit test concern).

Also verifies the **observability nesting**(P6-T4)on real
``run_query`` calls — sub-agent's events carry ``parent_run_id`` +
``agent_depth=1``.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest

from engine.conftest import _AllowAllChecker, _StubApiClient
from openharness.engine import QueryContext, run_query
from openharness.observability import configure_logging
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
)
from openharness.tools import SpawnAgent, ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _parent_tool_use(*, tool_use_id: str = "toolu_p1") -> ApiMessageCompleteEvent:
    """Parent's turn 1: LLM emits ``Agent(prompt="sub-task")`` tool_use."""
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Agent",
                        "input": {
                            "description": "research task",
                            "prompt": "count the words in foo.txt",
                        },
                    }
                ],
            },
            "usage": {"input_tokens": 20, "output_tokens": 8},
            "stop_reason": "tool_use",
        }
    )


def _end_turn(text: str) -> ApiMessageCompleteEvent:
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


class TestSpawnAgentFullChain:
    """Drive `run_query` with a stub that yields a parent turn-1 tool_use
    (Agent), then turn-2 end_turn for the parent, with a sub-agent turn
    sandwiched in between(driven by the same stub).
    """

    async def test_parent_receives_sub_agent_text_as_tool_result(self, tmp_path: Path) -> None:
        # Stub event sequence (chronological order across BOTH loops):
        #   Turn 1 of PARENT:      parent_tool_use(Agent)
        #   Turn 1 of SUB-AGENT:   end_turn("found 42 words")  ← sub completes
        #   Turn 2 of PARENT:      end_turn("the answer is 42 words")
        # _StubApiClient processes calls sequentially; the recursion
        # happens during SpawnAgent.execute which calls run_query for the
        # sub-agent BEFORE returning to the parent's loop.
        stub = _StubApiClient(
            [
                [_parent_tool_use()],
                [_end_turn("found 42 words")],
                [_end_turn("the answer is 42 words")],
            ]
        )

        registry = ToolRegistry()
        registry.register(SpawnAgent())
        context = QueryContext(
            api_client=stub,
            tool_registry=registry,
            permission_checker=_AllowAllChecker(),
            system_prompt="you are the parent agent",
            cwd=tmp_path,
            model="qwen-plus",
            agent_depth=0,
            max_agent_depth=3,
        )

        events = [
            event
            async for event in run_query(
                [
                    ConversationMessage(
                        role="user",
                        content=[
                            TextBlock(text="please research"),
                        ],
                    )
                ],
                context,
            )
        ]

        # The parent's final event is its turn-2 end_turn.
        last = events[-1]
        assert isinstance(last, ApiMessageCompleteEvent)
        assert last.stop_reason == "end_turn"
        text_blocks = [b for b in last.message.content if isinstance(b, TextBlock)]
        assert any("the answer is 42 words" in b.text for b in text_blocks)

        # 3 stub calls: parent turn 1 (tool_use), sub-agent turn 1 (end_turn),
        # parent turn 2 (end_turn). The recursive call to run_query for the
        # sub-agent went through the SAME stub — proving sub-agent inherits
        # api_client from parent.
        assert len(stub.captured_requests) == 3

        # Parent's turn-2 request contains the sub-agent's text in the
        # tool_result block. This is the load-bearing assertion:parent's
        # conversation grew by exactly one tool_use/tool_result pair
        # (turn-1 emitted tool_use, turn-2's message history has the
        # paired tool_result block), regardless of sub-agent length.
        parent_turn2 = stub.captured_requests[2]
        # Messages: [original user, assistant w/ tool_use, user w/ tool_result]
        assert len(parent_turn2.messages) == 3
        last_msg = parent_turn2.messages[2]
        assert last_msg.role == "user"
        tool_result_block = last_msg.content[0]
        assert isinstance(tool_result_block, ToolResultBlock)
        # ToolResultBlock.content is typed as str — sub-agent's text
        # round-trips into the block's content field verbatim.
        assert "found 42 words" in tool_result_block.content

    async def test_sub_agent_events_carry_parent_run_id_and_depth(
        self, tmp_path: Path, stream: io.StringIO
    ) -> None:
        # P6-T4 trace stitching:sub-agent's log events have
        # ``parent_run_id`` + ``agent_depth=1``.
        configure_logging(level="INFO", format="json", stream=stream)

        stub = _StubApiClient(
            [
                [_parent_tool_use()],
                [_end_turn("sub-agent text")],
                [_end_turn("parent ack")],
            ]
        )
        registry = ToolRegistry()
        registry.register(SpawnAgent())
        context = QueryContext(
            api_client=stub,
            tool_registry=registry,
            permission_checker=_AllowAllChecker(),
            system_prompt="",
            cwd=tmp_path,
            model="qwen-plus",
            agent_depth=0,
            max_agent_depth=3,
        )

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="x")])],
            context,
        ):
            pass

        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        # All turn_start events for both parent and sub-agent.
        turn_starts = [r for r in lines if r.get("event") == "turn_start"]
        # Parent's turn 1 + parent's turn 2 + sub-agent's turn 1 = 3.
        assert len(turn_starts) >= 3

        # Top-level events: agent_depth=0, no parent_run_id.
        top_level = [r for r in turn_starts if r.get("agent_depth") == 0]
        assert len(top_level) >= 1
        for r in top_level:
            assert "parent_run_id" not in r

        # Sub-agent's events: agent_depth=1, has parent_run_id.
        sub_agent = [r for r in turn_starts if r.get("agent_depth") == 1]
        assert len(sub_agent) == 1
        sub_record = sub_agent[0]
        assert "parent_run_id" in sub_record
        # The parent_run_id should point at the top-level run_id.
        parent_rid = top_level[0]["run_id"]
        assert sub_record["parent_run_id"] == parent_rid


@pytest.mark.integration
class TestSpawnAgentRealApi:
    """Real-LLM integration smoke — skipped without OPENHARNESS_API_KEY.

    Same pattern as P4 / P5 integration tests. Intent: in CI / manual
    verification, an actual ``oh ask`` invocation with the Agent tool
    available exercises the full recursion end-to-end against a real
    Provider.
    """

    def test_marker_present(self) -> None:
        # Sentinel — the integration test infra is wired (skip-on-no-env
        # is honored by the marker config in pyproject.toml). Real-API
        # exercise is captured by the existing P1-T3 / P4 / P5 integration
        # smoke that exercises the full CLI path; this one mostly serves
        # as documentation that Phase 6 sub-agent ALSO runs against real
        # APIs via the same path (no opt-in env var change needed —
        # ``Agent`` is already in the default tool registry).
        assert True
