"""Tests for HookContext per-event dataclasses + Hook callable — P3-T4.4b.

5 frozen dataclasses(one per event) + a Hook async callable type alias.
The executor (4d) uses these contexts to pass exactly the event-relevant
info to user hooks.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import get_args

import pytest

from openharness.hooks import (
    Hook,
    HookContext,
    HookResult,
    OnErrorContext,
    PostApiCallContext,
    PostToolUseContext,
    PreApiCallContext,
    PreToolUseContext,
)
from openharness.protocols import (
    ApiMessageRequest,
    ConversationMessage,
    TextBlock,
    UsageSnapshot,
)
from openharness.tools.base import ToolExecutionContext, ToolResult


def _exec_ctx() -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"))


class TestPreToolUseContext:
    def test_carries_tool_name_input_exec_context(self) -> None:
        ctx = PreToolUseContext(
            tool_name="Read",
            tool_use_id="toolu_test",
            tool_input={"path": "/tmp/foo.txt"},
            exec_context=_exec_ctx(),
        )
        assert ctx.tool_name == "Read"
        assert ctx.tool_input == {"path": "/tmp/foo.txt"}
        assert ctx.exec_context.cwd == Path("/tmp")

    def test_frozen(self) -> None:
        ctx = PreToolUseContext(
            tool_name="Read",
            tool_use_id="toolu_test",
            tool_input={},
            exec_context=_exec_ctx(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.tool_name = "Bash"  # type: ignore[misc]


class TestPostToolUseContext:
    def test_carries_result(self) -> None:
        result = ToolResult(output="content")
        ctx = PostToolUseContext(
            tool_name="Read",
            tool_use_id="toolu_test",
            tool_input={"path": "/x"},
            exec_context=_exec_ctx(),
            result=result,
        )
        assert ctx.result is result
        assert ctx.result.output == "content"

    def test_frozen(self) -> None:
        ctx = PostToolUseContext(
            tool_name="X",
            tool_use_id="toolu_test",
            tool_input={},
            exec_context=_exec_ctx(),
            result=ToolResult(output=""),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.tool_name = "Y"  # type: ignore[misc]


def _simple_request() -> ApiMessageRequest:
    return ApiMessageRequest(
        model="qwen-plus",
        max_tokens=512,
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
    )


class TestPreApiCallContext:
    def test_carries_request_and_turn(self) -> None:
        req = _simple_request()
        ctx = PreApiCallContext(request=req, turn=0)
        assert ctx.request is req
        assert ctx.turn == 0


class TestPostApiCallContext:
    def test_carries_all_response_metadata(self) -> None:
        req = _simple_request()
        msg = ConversationMessage(role="assistant", content=[TextBlock(text="hello")])
        usage = UsageSnapshot(input_tokens=3, output_tokens=1)
        ctx = PostApiCallContext(
            request=req,
            response_message=msg,
            usage=usage,
            stop_reason="end_turn",
            turn=2,
        )
        assert ctx.usage.input_tokens == 3
        assert ctx.stop_reason == "end_turn"
        assert ctx.turn == 2


class TestOnErrorContext:
    def test_default_tool_name_is_none(self) -> None:
        ctx = OnErrorContext(
            exception=RuntimeError("boom"),
            where="hook",
        )
        assert ctx.tool_name is None

    def test_carries_exception_and_origin(self) -> None:
        err = ValueError("bad args")
        ctx = OnErrorContext(exception=err, where="tool", tool_name="Bash")
        assert ctx.exception is err
        assert ctx.where == "tool"
        assert ctx.tool_name == "Bash"

    def test_where_literal_values(self) -> None:
        # 4 acceptable values per Three-Axis G — each is a valid Literal slot.
        ctx_api = OnErrorContext(exception=RuntimeError("x"), where="api")
        ctx_tool = OnErrorContext(exception=RuntimeError("x"), where="tool")
        ctx_hook = OnErrorContext(exception=RuntimeError("x"), where="hook")
        ctx_loop = OnErrorContext(exception=RuntimeError("x"), where="loop")
        assert (ctx_api.where, ctx_tool.where, ctx_hook.where, ctx_loop.where) == (
            "api",
            "tool",
            "hook",
            "loop",
        )


class TestHookContextUnion:
    def test_all_five_in_union(self) -> None:
        # HookContext is a Union of the 5 typed contexts.
        members = set(get_args(HookContext))
        expected = {
            PreToolUseContext,
            PostToolUseContext,
            PreApiCallContext,
            PostApiCallContext,
            OnErrorContext,
        }
        assert members == expected


class TestHookCallable:
    """``Hook`` is an async callable taking a HookContext and returning
    HookResult or None.

    These tests are mypy-driven (the cast / type binding itself is the
    structural check). The runtime assertion is sanity:the hook is
    callable and returns the right shape.
    """

    async def test_hook_can_be_assigned_async_function(self) -> None:
        async def my_hook(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.allow()

        # Assigning to Hook-typed binding = structural compat (mypy strict).
        hook: Hook = my_hook
        # Runtime call — verify shape.
        ctx = PreToolUseContext(
            tool_name="X",
            tool_use_id="toolu_test",
            tool_input={},
            exec_context=_exec_ctx(),
        )
        result = await hook(ctx)
        assert result is not None
        assert result.decision == "allow"

    async def test_hook_returning_none_works(self) -> None:
        async def passthrough(ctx: HookContext) -> HookResult | None:
            del ctx
            return None

        hook: Hook = passthrough
        ctx = PreToolUseContext(
            tool_name="X",
            tool_use_id="toolu_test",
            tool_input={},
            exec_context=_exec_ctx(),
        )
        result = await hook(ctx)
        assert result is None
