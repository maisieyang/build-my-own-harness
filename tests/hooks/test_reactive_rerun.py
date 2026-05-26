"""Tests for P11-T6 (D29.7) — PreApiCall reactive re-run.

Closes Phase 4 retro §6: PreApiCall hooks flagged
``re_run_on_reactive_rebuild=True`` re-fire after the engine's PTL
retry rebuilds the request, so injected content (memory etc.)
survives the truncation.

Contract surfaces:

1. Flagged hook fires exactly TWICE on a PTL-retry turn: once before
   the original request, once after rebuild.
2. Unflagged hook fires exactly ONCE (engine does NOT re-fire it).
3. Multiple flagged hooks all re-run in registration order.
4. Re-run is PreApiCall-specific — PostToolUse / PreToolUse hooks
   with the flag never re-fire from the PTL path (only PreApiCall).
5. Hook subset isolation in ``execute_hook_chain``: subset=[] returns
   None without calling registry.get.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from openharness.api.errors import PromptTooLongFailure
from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.hooks.context import PreApiCallContext, PreToolUseContext
from openharness.hooks.executor import execute_hook_chain
from openharness.hooks.registry import HookRegistry
from openharness.permissions import Decision
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.tools import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.api import SupportsStreamingMessages
    from openharness.hooks.result import HookResult
    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


class _AllowAllChecker:
    def check(self, tool_name: str, tool_input: dict[str, object], **_: object) -> object:
        del tool_name, tool_input
        return Decision.allow()


class _PtlOnceThenEnd:
    """First call raises PTL, second yields end_turn. Records calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]
            raise PromptTooLongFailure("ctx_exceeded", status_code=400)
        yield ApiMessageCompleteEvent.model_validate(
            {
                "message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "end_turn",
            }
        )


def _messages_with_pair() -> list[ConversationMessage]:
    return [
        ConversationMessage(role="user", content=[TextBlock(text="go")]),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="t1", name="Read", input={"path": "/x"})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="t1", content="ok")],
        ),
    ]


def _ctx(client: object, hook_registry: HookRegistry) -> QueryContext:
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", client),
        tool_registry=ToolRegistry(),
        permission_checker=_AllowAllChecker(),  # type: ignore[arg-type]
        hook_registry=hook_registry,
        system_prompt="t",
        cwd=Path("/tmp"),
        model="qwen-plus",
        max_tokens=64,
        compact_enabled=False,
    )


class TestFlaggedHookRerunsOnPtlRebuild:
    @pytest.mark.asyncio
    async def test_flagged_pre_api_call_fires_twice(self) -> None:
        invocations: list[int] = []

        async def memory_injector(ctx: PreApiCallContext) -> HookResult | None:
            invocations.append(len(ctx.request.messages))
            return None

        registry = HookRegistry()
        registry.register("PreApiCall", memory_injector, re_run_on_reactive_rebuild=True)
        stub = _PtlOnceThenEnd()
        ctx = _ctx(stub, registry)

        async for _ev in run_query(_messages_with_pair(), ctx):
            pass

        # Original call: 3 messages. After PTL drop-oldest-pair rebuild:
        # 1 message. So invocations should be [3, 1].
        assert invocations == [3, 1]
        assert stub.calls == 2


class TestUnflaggedHookNotRerun:
    @pytest.mark.asyncio
    async def test_unflagged_pre_api_call_fires_once(self) -> None:
        invocations: list[int] = []

        async def passive_observer(ctx: PreApiCallContext) -> HookResult | None:
            invocations.append(len(ctx.request.messages))
            return None

        registry = HookRegistry()
        registry.register("PreApiCall", passive_observer)  # no flag
        stub = _PtlOnceThenEnd()
        ctx = _ctx(stub, registry)

        async for _ev in run_query(_messages_with_pair(), ctx):
            pass

        # Fires once at top of turn; engine doesn't re-run after PTL.
        assert invocations == [3]
        assert stub.calls == 2


class TestMultipleFlaggedHooksRerunInOrder:
    @pytest.mark.asyncio
    async def test_two_flagged_hooks_both_rerun_in_registration_order(self) -> None:
        order: list[str] = []

        async def hook_a(_ctx: PreApiCallContext) -> HookResult | None:
            order.append("a")
            return None

        async def hook_b(_ctx: PreApiCallContext) -> HookResult | None:
            order.append("b")
            return None

        registry = HookRegistry()
        registry.register("PreApiCall", hook_a, re_run_on_reactive_rebuild=True)
        registry.register("PreApiCall", hook_b, re_run_on_reactive_rebuild=True)
        stub = _PtlOnceThenEnd()
        ctx = _ctx(stub, registry)

        async for _ev in run_query(_messages_with_pair(), ctx):
            pass

        # Original chain: a, b. Rerun chain: a, b. Total: ababab.
        assert order == ["a", "b", "a", "b"]

    @pytest.mark.asyncio
    async def test_mixed_flagged_and_unflagged_only_flagged_rerun(self) -> None:
        order: list[str] = []

        async def flagged(_ctx: PreApiCallContext) -> HookResult | None:
            order.append("flagged")
            return None

        async def unflagged(_ctx: PreApiCallContext) -> HookResult | None:
            order.append("unflagged")
            return None

        registry = HookRegistry()
        registry.register("PreApiCall", flagged, re_run_on_reactive_rebuild=True)
        registry.register("PreApiCall", unflagged)  # no flag
        stub = _PtlOnceThenEnd()
        ctx = _ctx(stub, registry)

        async for _ev in run_query(_messages_with_pair(), ctx):
            pass

        # Original: both fire. Rerun: only flagged.
        assert order == ["flagged", "unflagged", "flagged"]


class TestNonPreApiCallEventsNeverRerun:
    def test_post_tool_use_with_flag_not_tracked(self) -> None:
        # PostToolUse hooks should NOT be tracked even if flag set.
        registry = HookRegistry()

        async def post_hook(_ctx: object) -> HookResult | None:
            return None

        registry.register("PostToolUse", post_hook, re_run_on_reactive_rebuild=True)
        assert registry.get_reactive_rerun("PostToolUse") == []
        assert registry.get_reactive_rerun("PreApiCall") == []

    def test_pre_tool_use_with_flag_not_tracked(self) -> None:
        registry = HookRegistry()

        async def pre_tool_hook(_ctx: PreToolUseContext) -> HookResult | None:
            return None

        registry.register("PreToolUse", pre_tool_hook, re_run_on_reactive_rebuild=True)
        assert registry.get_reactive_rerun("PreToolUse") == []


class TestExecuteHookChainSubset:
    @pytest.mark.asyncio
    async def test_subset_empty_returns_none(self) -> None:
        registry = HookRegistry()

        async def hook(_ctx: PreApiCallContext) -> HookResult | None:
            return None

        registry.register("PreApiCall", hook)

        from openharness.protocols import ApiMessageRequest

        req = ApiMessageRequest(model="x", max_tokens=1, system=None, messages=[], tools=None)
        # subset=[] short-circuits before running ANY hook
        result = await execute_hook_chain(
            registry,
            "PreApiCall",
            PreApiCallContext(request=req, turn=0),
            hook_subset=[],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_subset_runs_only_provided_hooks(self) -> None:
        fired: list[str] = []

        async def hook_a(_ctx: PreApiCallContext) -> HookResult | None:
            fired.append("a")
            return None

        async def hook_b(_ctx: PreApiCallContext) -> HookResult | None:
            fired.append("b")
            return None

        registry = HookRegistry()
        registry.register("PreApiCall", hook_a)
        registry.register("PreApiCall", hook_b)

        from openharness.protocols import ApiMessageRequest

        req = ApiMessageRequest(model="x", max_tokens=1, system=None, messages=[], tools=None)
        await execute_hook_chain(
            registry,
            "PreApiCall",
            PreApiCallContext(request=req, turn=0),
            hook_subset=[hook_b],
        )
        assert fired == ["b"]


class TestReactiveRerunHookSemantics:
    """Verify the engine's reactive PTL rerun honors deny / modify
    semantics from the flagged subset."""

    @pytest.mark.asyncio
    async def test_rerun_deny_raises_loop_error(self) -> None:
        from openharness.errors import LoopError
        from openharness.hooks.result import HookResult as Result

        # Allow on first call (original chain), deny on second (rerun
        # after PTL rebuild). Without this split, the deny would fire
        # in the original chain and the rebuild code path never runs.
        call_count = {"n": 0}

        async def deny_on_rerun(_ctx: PreApiCallContext) -> Result | None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None
            return Result.deny("rerun policy violation")

        registry = HookRegistry()
        registry.register("PreApiCall", deny_on_rerun, re_run_on_reactive_rebuild=True)
        stub = _PtlOnceThenEnd()
        ctx = _ctx(stub, registry)

        with pytest.raises(LoopError, match="denied rebuilt turn"):
            async for _ev in run_query(_messages_with_pair(), ctx):
                pass

    @pytest.mark.asyncio
    async def test_rerun_modify_replaces_request(self) -> None:
        # Flagged hook on rerun modifies the request — engine should
        # use the modified request when streaming the second attempt.
        from openharness.hooks.result import HookResult as Result
        from openharness.protocols import ApiMessageRequest

        modified_seen: list[bool] = []

        async def modifier(ctx: PreApiCallContext) -> Result | None:
            # Detect the rerun by checking that messages have shrunk
            # (engine truncated before re-firing us).
            if len(ctx.request.messages) < 3:
                marker = ApiMessageRequest(
                    model=ctx.request.model,
                    max_tokens=ctx.request.max_tokens,
                    system="REWRITTEN",
                    messages=ctx.request.messages,
                    tools=ctx.request.tools,
                )
                return Result.modify_request(marker)
            return None

        # Stub captures the second-call request to confirm the modify
        # actually swung through to the API.
        class _PtlThenRecord:
            def __init__(self) -> None:
                self.calls = 0
                self.last_system: str | None = None

            async def stream_message(
                self,
                request: ApiMessageRequest,
            ) -> AsyncIterator[ApiStreamEvent]:
                self.calls += 1
                self.last_system = request.system
                if self.calls == 1:
                    if False:  # pragma: no cover
                        yield  # type: ignore[unreachable]
                    raise PromptTooLongFailure("ctx_exceeded", status_code=400)
                yield ApiMessageCompleteEvent.model_validate(
                    {
                        "message": {"role": "assistant", "content": []},
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "stop_reason": "end_turn",
                    }
                )

        registry = HookRegistry()
        registry.register("PreApiCall", modifier, re_run_on_reactive_rebuild=True)
        stub = _PtlThenRecord()
        ctx = _ctx(stub, registry)

        async for _ev in run_query(_messages_with_pair(), ctx):
            pass

        modified_seen.append(stub.last_system == "REWRITTEN")
        assert modified_seen == [True]


class TestHookRegistryRerunInitialState:
    def test_fresh_registry_has_empty_rerun_set(self) -> None:
        registry = HookRegistry()
        assert registry.get_reactive_rerun("PreApiCall") == []

    def test_register_default_does_not_flag(self) -> None:
        registry = HookRegistry()

        async def hook(_ctx: PreApiCallContext) -> HookResult | None:
            return None

        registry.register("PreApiCall", hook)
        assert registry.get_reactive_rerun("PreApiCall") == []
