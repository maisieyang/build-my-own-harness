"""Tests for engine wiring of LLM-authored task_focus_state — P13-T3.

Companion to ``tests/services/test_focus_state.py`` (which tests
``infer_focus_state`` in isolation). This file verifies the engine's
per-turn-end finally block actually calls it when opted in + threads
the result into ``tool_metadata.task_focus_state`` for both writers.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.hooks import HookRegistry
from openharness.permissions import PermissionMode
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.services.snapshot import (
    load_snapshot,
)
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.api import SupportsStreamingMessages
    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


class _AllowAllChecker:
    def evaluate(self, *_a: object, **_kw: object) -> object:
        from openharness.permissions import DecisionResult

        return DecisionResult.allow()


class _MultiResponseStub:
    """Stub returning different responses for first vs subsequent calls.

    First call = main turn ("ok"), second call = focus_state JSON.
    """

    def __init__(self, *, main_response: str = "ok", focus_response: str | None = None) -> None:
        self._main = main_response
        self._focus = focus_response
        self.calls = 0
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        from openharness.protocols.stream_events import ApiTextDeltaEvent

        self.requests.append(request)
        self.calls += 1
        # First call = main convo; second call = focus state
        if self.calls == 1:
            text = self._main
        elif self._focus is not None:
            text = self._focus
        else:
            text = "{}"
        # summarize() reads text from delta events, not from the
        # final ApiMessageCompleteEvent — yield the delta first.
        yield ApiTextDeltaEvent(text=text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _ctx(
    client: object,
    *,
    cwd: Path,
    llm_focus_state_enabled: bool = False,
    snapshot_enabled: bool = True,
) -> QueryContext:
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", client),
        tool_registry=create_default_tool_registry(),
        permission_checker=_AllowAllChecker(),  # type: ignore[arg-type]
        hook_registry=HookRegistry(),
        system_prompt="t",
        cwd=cwd,
        model="qwen-plus",
        max_tokens=64,
        max_turns=2,
        permission_mode=PermissionMode.DEFAULT,
        compact_enabled=False,
        extract_enabled=False,
        snapshot_enabled=snapshot_enabled,
        llm_focus_state_enabled=llm_focus_state_enabled,
    )


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


class TestFocusStateOptInBehavior:
    @pytest.mark.asyncio
    async def test_disabled_writes_placeholder_only(self, tmp_path: Path) -> None:
        # Default OFF — snapshot's task_focus_state should be the
        # Phase 12 None placeholder; engine should not fire a
        # second LLM call.
        stub = _MultiResponseStub(main_response="reply")
        ctx = _ctx(stub, cwd=tmp_path, llm_focus_state_enabled=False)

        async for _ev in run_query([_user("hi")], ctx):
            pass

        # Only 1 LLM call (main convo); no focus_state call
        assert stub.calls == 1

        snapshot = load_snapshot(tmp_path)
        focus = snapshot["tool_metadata"]["task_focus_state"]
        assert focus == {"goal": None, "next_step": None}

    @pytest.mark.asyncio
    async def test_enabled_writes_authored_state(self, tmp_path: Path) -> None:
        stub = _MultiResponseStub(
            main_response="reply",
            focus_response=json.dumps(
                {"goal": "answer the user", "next_step": "wait for follow-up"}
            ),
        )
        ctx = _ctx(stub, cwd=tmp_path, llm_focus_state_enabled=True)

        async for _ev in run_query([_user("hi")], ctx):
            pass

        # 2 LLM calls: main convo + focus_state inference
        assert stub.calls == 2

        snapshot = load_snapshot(tmp_path)
        focus = snapshot["tool_metadata"]["task_focus_state"]
        assert focus == {"goal": "answer the user", "next_step": "wait for follow-up"}

    @pytest.mark.asyncio
    async def test_focus_state_failure_falls_back_to_placeholder(self, tmp_path: Path) -> None:
        # Focus state LLM returns garbage → fallback to None placeholder,
        # turn still succeeds + snapshot still written.
        stub = _MultiResponseStub(
            main_response="reply",
            focus_response="not valid json",
        )
        ctx = _ctx(stub, cwd=tmp_path, llm_focus_state_enabled=True)

        async for _ev in run_query([_user("hi")], ctx):
            pass

        snapshot = load_snapshot(tmp_path)
        # Falls back to None placeholder — failure isolated
        focus = snapshot["tool_metadata"]["task_focus_state"]
        assert focus == {"goal": None, "next_step": None}

    @pytest.mark.asyncio
    async def test_focus_state_passed_to_both_writers(self, tmp_path: Path) -> None:
        # Wire session_memory + snapshot together; verify the focus state
        # appears in BOTH (session_memory's markdown checkpoint embeds
        # it under ## Current State / ## Next Step).
        from openharness.services.session_memory import (
            get_session_memory_dir,
            read_session_memory,
        )

        session_path = get_session_memory_dir(tmp_path) / "checkpoint.md"
        stub = _MultiResponseStub(
            main_response="reply",
            focus_response=json.dumps({"goal": "build the feature", "next_step": "write tests"}),
        )
        ctx = QueryContext(
            api_client=cast("SupportsStreamingMessages", stub),
            tool_registry=create_default_tool_registry(),
            permission_checker=_AllowAllChecker(),  # type: ignore[arg-type]
            hook_registry=HookRegistry(),
            system_prompt="t",
            cwd=tmp_path,
            model="qwen-plus",
            max_tokens=64,
            max_turns=2,
            permission_mode=PermissionMode.DEFAULT,
            compact_enabled=False,
            extract_enabled=False,
            session_memory_path=session_path,
            snapshot_enabled=True,
            llm_focus_state_enabled=True,
        )

        async for _ev in run_query([_user("hi")], ctx):
            pass

        # snapshot has authored state
        snapshot = load_snapshot(tmp_path)
        focus = snapshot["tool_metadata"]["task_focus_state"]
        assert focus["goal"] == "build the feature"
        assert focus["next_step"] == "write tests"

        # session_memory markdown also has it
        sm_content = read_session_memory(tmp_path)
        assert sm_content is not None
        assert "build the feature" in sm_content
        assert "write tests" in sm_content


class TestFocusStateModelOverride:
    @pytest.mark.asyncio
    async def test_model_override_threads_to_summarize(self, tmp_path: Path) -> None:
        # When llm_focus_state_model is set, the focus_state LLM call
        # uses THAT model (not context.model).
        stub = _MultiResponseStub(
            main_response="reply",
            focus_response=json.dumps({"goal": "g", "next_step": "n"}),
        )
        ctx = QueryContext(
            api_client=cast("SupportsStreamingMessages", stub),
            tool_registry=create_default_tool_registry(),
            permission_checker=_AllowAllChecker(),  # type: ignore[arg-type]
            hook_registry=HookRegistry(),
            system_prompt="t",
            cwd=tmp_path,
            model="qwen-plus",
            max_tokens=64,
            max_turns=2,
            permission_mode=PermissionMode.DEFAULT,
            compact_enabled=False,
            extract_enabled=False,
            snapshot_enabled=True,
            llm_focus_state_enabled=True,
            llm_focus_state_model="qwen-turbo",  # different from main
        )

        async for _ev in run_query([_user("hi")], ctx):
            pass

        # 2 calls: first = main (qwen-plus), second = focus (qwen-turbo)
        assert stub.calls == 2
        assert stub.requests[0].model == "qwen-plus"
        assert stub.requests[1].model == "qwen-turbo"
