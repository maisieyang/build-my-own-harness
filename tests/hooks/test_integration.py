"""End-to-end smoke tests for the hook system — P3-T4.4h.

Unit tests in :mod:`tests.hooks.test_executor` cover the chain-resolve
algorithm in isolation. These tests cover the **wiring** — that hooks are
actually invoked from ``run_query`` / ``_dispatch_one`` at the right
points, that modify-results flow through to downstream consumers
(tool input → execute; tool output → LLM's next-turn message; request →
api_client), and that the dispatch order honors the framework safety
baseline (AuthZ runs BEFORE PreToolUse hooks).

Each test attaches real hooks to a real ``run_query`` loop driven by the
shared ``_StubApiClient`` + ``_FakeTool`` fixtures used elsewhere in
``tests/engine``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from engine.conftest import _AllowAllChecker, _DenyChecker, _StubApiClient
from openharness.engine import QueryContext, run_query
from openharness.errors import HookError, LoopError, ToolError
from openharness.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    OnErrorContext,
    PostApiCallContext,
    PostToolUseContext,
    PreApiCallContext,
    PreToolUseContext,
)
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ConversationMessage,
    TextBlock,
    ToolExecutionCompletedEvent,
    ToolResultBlock,
)
from openharness.tools import ExecutionDomain, ToolRegistry

if TYPE_CHECKING:
    from openharness.api import SupportsStreamingMessages
    from openharness.permissions import PermissionChecker


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _end_turn_event(text: str = "ok") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "end_turn",
        }
    )


def _tool_use_event(
    *,
    tool_use_id: str = "toolu_01",
    tool_input: dict[str, object] | None = None,
) -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Fake",
                        "input": tool_input if tool_input is not None else {"value": "hi"},
                    }
                ],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "tool_use",
        }
    )


def _make_context(
    *,
    api_client: object,
    hook_registry: HookRegistry,
    tool_registry: ToolRegistry | None = None,
    permission_checker: PermissionChecker | None = None,
) -> QueryContext:
    from tools.conftest import _FakeTool

    if tool_registry is None:
        tool_registry = ToolRegistry()
        tool_registry.register(_FakeTool())
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", api_client),
        tool_registry=tool_registry,
        permission_checker=permission_checker
        if permission_checker is not None
        else _AllowAllChecker(),
        system_prompt="test harness",
        cwd=Path("/tmp"),
        model="qwen-plus",
        max_tokens=512,
        hook_registry=hook_registry,
    )


# --------------------------------------------------------------------------- #
# Observe-only — log hook sees every lifecycle event                          #
# --------------------------------------------------------------------------- #


class TestObserveHooks:
    """Log-style hooks that return ``None`` see events but don't influence."""

    async def test_full_lifecycle_fires_in_order(self) -> None:
        log: list[tuple[str, str]] = []

        async def log_pre_api(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PreApiCallContext)
            log.append(("PreApiCall", ctx.request.model))
            return None

        async def log_post_api(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PostApiCallContext)
            log.append(("PostApiCall", ctx.stop_reason))
            return None

        async def log_pre_tool(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PreToolUseContext)
            log.append(("PreToolUse", ctx.tool_name))
            return None

        async def log_post_tool(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PostToolUseContext)
            log.append(("PostToolUse", ctx.tool_name))
            return None

        registry = HookRegistry()
        registry.register("PreApiCall", log_pre_api)
        registry.register("PostApiCall", log_post_api)
        registry.register("PreToolUse", log_pre_tool)
        registry.register("PostToolUse", log_post_tool)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "hi"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, hook_registry=registry)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        # Two API turns -> 2x (PreApiCall, PostApiCall); one tool dispatch
        # in turn 1 -> 1x (PreToolUse, PostToolUse). Order matches lifecycle.
        assert log == [
            ("PreApiCall", "qwen-plus"),
            ("PostApiCall", "tool_use"),
            ("PreToolUse", "Fake"),
            ("PostToolUse", "Fake"),
            ("PreApiCall", "qwen-plus"),
            ("PostApiCall", "end_turn"),
        ]


# --------------------------------------------------------------------------- #
# Modify hooks — rewrites flow through to downstream consumers                #
# --------------------------------------------------------------------------- #


class TestModifyHooksReachDownstream:
    async def test_pre_tool_use_modify_input_reaches_execute(self) -> None:
        """``modify_input`` is re-validated and passed to ``tool.execute``."""

        async def rewrite(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_input({"value": "REWRITTEN"})

        registry = HookRegistry()
        registry.register("PreToolUse", rewrite)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "original"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, hook_registry=registry)

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(e for e in events if isinstance(e, ToolExecutionCompletedEvent))
        # _FakeTool returns f"value={args.value}" — proves rewrite reached execute.
        assert completed.output == "value=REWRITTEN"
        assert completed.is_error is False

    async def test_pre_tool_use_modified_input_is_reauthorized(self) -> None:
        """The final hook-modified arguments, not only the model-authored
        arguments, must pass the permission checker before execution."""
        from openharness.permissions import DecisionResult
        from tools.conftest import FakeInput

        class _AllowOriginalDenyRewrite:
            def __init__(self) -> None:
                self.values: list[str] = []

            def evaluate(self, tool_name: str, args: object, context: object) -> DecisionResult:
                del tool_name, context
                assert isinstance(args, FakeInput)
                value = args.value
                self.values.append(value)
                if value == "REWRITTEN":
                    return DecisionResult.deny("rewritten input is outside the grant")
                return DecisionResult.allow()

        async def rewrite(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_input({"value": "REWRITTEN"})

        hooks = HookRegistry()
        hooks.register("PreToolUse", rewrite)
        checker = _AllowOriginalDenyRewrite()
        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "original"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(
            api_client=client,
            hook_registry=hooks,
            permission_checker=checker,  # type: ignore[arg-type]
        )

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]

        completed = next(e for e in events if isinstance(e, ToolExecutionCompletedEvent))
        assert completed.is_error is True
        assert "rewritten input is outside the grant" in completed.output
        assert checker.values == ["original", "REWRITTEN"]

    async def test_post_tool_use_modify_output_reaches_next_turn_message(self) -> None:
        """Sanitize hook redacts output; LLM's next turn sees the redacted text."""

        async def sanitize(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_output(new_output="[REDACTED]")

        registry = HookRegistry()
        registry.register("PostToolUse", sanitize)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "secret"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, hook_registry=registry)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        # Turn-2 request: [user, assistant(tool_use), user(tool_result)].
        turn2 = client.captured_requests[1]
        tool_result = turn2.messages[2].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.content == "[REDACTED]"

    async def test_pre_api_call_modify_request_reaches_client(self) -> None:
        """``modify_request`` replaces the ApiMessageRequest before stream_message."""

        async def inject_system(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PreApiCallContext)
            new_req = ctx.request.model_copy(update={"system": "INJECTED SYSTEM PROMPT"})
            return HookResult.modify_request(new_req)

        registry = HookRegistry()
        registry.register("PreApiCall", inject_system)

        client = _StubApiClient(events_per_turn=[[_end_turn_event()]])
        context = _make_context(api_client=client, hook_registry=registry)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        # Client captured the modified request, not the original.
        assert client.captured_requests[0].system == "INJECTED SYSTEM PROMPT"


# --------------------------------------------------------------------------- #
# Deny hooks — short-circuit with recovery semantics                          #
# --------------------------------------------------------------------------- #


class TestDenyHooks:
    async def test_pre_tool_use_deny_yields_is_error_result(self) -> None:
        """PreToolUse deny short-circuits the tool dispatch; LLM sees an
        is_error=True ToolResult (recovery path, not abort)."""

        async def deny(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.deny("policy blocked")

        registry = HookRegistry()
        registry.register("PreToolUse", deny)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "x"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, hook_registry=registry)

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(e for e in events if isinstance(e, ToolExecutionCompletedEvent))
        assert completed.is_error is True
        assert "hook denied" in completed.output
        assert "policy blocked" in completed.output

        # Turn 2 still happened — deny is recoverable, not fatal.
        turn2 = client.captured_requests[1]
        tool_result = turn2.messages[2].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.is_error is True

    async def test_post_tool_use_deny_converts_to_is_error_modify(self) -> None:
        """Three-Axis F:PostToolUse deny soft-converts to modify-as-error."""

        async def post_deny(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.deny("contains PII")

        registry = HookRegistry()
        registry.register("PostToolUse", post_deny)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "x"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, hook_registry=registry)

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(e for e in events if isinstance(e, ToolExecutionCompletedEvent))
        assert completed.is_error is True
        assert "post-hook deny" in completed.output
        assert "contains PII" in completed.output

    async def test_pre_api_call_deny_raises_loop_error(self) -> None:
        """PreApiCall deny is fatal — the loop can't proceed without an API call."""

        async def deny_turn(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.deny("budget exceeded")

        registry = HookRegistry()
        registry.register("PreApiCall", deny_turn)

        client = _StubApiClient(events_per_turn=[[_end_turn_event()]])
        context = _make_context(api_client=client, hook_registry=registry)

        with pytest.raises(LoopError, match="budget exceeded"):
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                pass

        # Client never called — the deny short-circuited before stream_message.
        assert client._turn == 0


# --------------------------------------------------------------------------- #
# Order — AuthZ runs BEFORE PreToolUse hooks (framework safety baseline)      #
# --------------------------------------------------------------------------- #


class TestDispatchOrder:
    async def test_authz_runs_before_pre_tool_use_hooks(self) -> None:
        """Hooks can't bypass the AuthZ baseline — when AuthZ denies, the
        PreToolUse hook is never invoked."""
        observed: list[str] = []

        async def pre_tool(ctx: HookContext) -> HookResult | None:
            del ctx
            observed.append("PreToolUse fired")
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", pre_tool)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "x"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(
            api_client=client,
            hook_registry=registry,
            permission_checker=_DenyChecker(),
        )

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(e for e in events if isinstance(e, ToolExecutionCompletedEvent))
        assert completed.is_error is True
        assert "permission denied" in completed.output
        # PreToolUse hook never fired — AuthZ short-circuited first.
        assert observed == []


# --------------------------------------------------------------------------- #
# OnError — hook crash and tool crash both fire OnError                       #
# --------------------------------------------------------------------------- #


class TestOnErrorChain:
    async def test_crashing_hook_raises_hook_error_and_fires_on_error(self) -> None:
        """Three-Axis G:hook exception → fire OnError (one level) → raise HookError."""
        on_error_seen: list[OnErrorContext] = []

        async def crash(ctx: HookContext) -> HookResult | None:
            del ctx
            raise ValueError("hook boom")

        async def watch_on_error(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, OnErrorContext)
            on_error_seen.append(ctx)
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", crash)
        registry.register("OnError", watch_on_error)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "x"})],
            ]
        )
        context = _make_context(api_client=client, hook_registry=registry)

        with pytest.raises(HookError, match="hook boom"):
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                pass

        assert len(on_error_seen) == 1
        ctx = on_error_seen[0]
        assert ctx.where == "hook"
        assert ctx.tool_name == "Fake"
        assert isinstance(ctx.exception, ValueError)

    async def test_tool_crash_fires_on_error_and_wraps_as_tool_error(self) -> None:
        """Tool's ``execute`` raises → OnError fires (where='tool') → ToolError."""
        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
        from tools.conftest import FakeInput

        class _CrashingFake(BaseTool[FakeInput]):
            execution_domain = ExecutionDomain.LOCAL_DATA
            name = "Fake"
            description = "Crashes mid-execute."
            input_model = FakeInput

            async def execute(
                self,
                args: FakeInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                raise RuntimeError("tool boom")

        on_error_seen: list[OnErrorContext] = []

        async def watch_on_error(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, OnErrorContext)
            on_error_seen.append(ctx)
            return None

        registry = HookRegistry()
        registry.register("OnError", watch_on_error)

        tool_registry = ToolRegistry()
        tool_registry.register(_CrashingFake())

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "x"})],
            ]
        )
        context = _make_context(
            api_client=client,
            hook_registry=registry,
            tool_registry=tool_registry,
        )

        with pytest.raises(ToolError, match="tool boom"):
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                pass

        assert len(on_error_seen) == 1
        ctx = on_error_seen[0]
        assert ctx.where == "tool"
        assert ctx.tool_name == "Fake"
        assert isinstance(ctx.exception, RuntimeError)


# --------------------------------------------------------------------------- #
# Realistic composition — log + modify + sanitize layered on the same flow   #
# --------------------------------------------------------------------------- #


class TestRealisticComposition:
    async def test_log_and_sanitize_and_modify_compose(self) -> None:
        """Layered hooks compose:log observes, PreToolUse rewrites input,
        PostToolUse sanitizes output — all in one flow."""
        log: list[str] = []

        async def log_pre(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PreToolUseContext)
            # The log hook sees the (possibly already-modified) input that
            # this hook receives — registered first, so still original here.
            log.append(f"pre:{ctx.tool_input.get('value')}")
            return None

        async def rewrite(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_input({"value": "ROUTED"})

        async def log_post(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PostToolUseContext)
            log.append(f"post:{ctx.result.output}")
            return None

        async def sanitize(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_output(new_output="[REDACTED]")

        registry = HookRegistry()
        registry.register("PreToolUse", log_pre)
        registry.register("PreToolUse", rewrite)
        registry.register("PostToolUse", log_post)
        registry.register("PostToolUse", sanitize)

        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "ORIGINAL"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, hook_registry=registry)

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            )
        ]
        completed = next(e for e in events if isinstance(e, ToolExecutionCompletedEvent))
        # PostToolUse sanitize replaced "value=ROUTED" → "[REDACTED]".
        assert completed.output == "[REDACTED]"

        # log_pre saw the original input (registered before rewrite).
        # log_post saw the *raw* tool output before sanitize accumulated.
        assert log == ["pre:ORIGINAL", "post:value=ROUTED"]

        # Turn 2 carries the sanitized result to the LLM.
        turn2 = client.captured_requests[1]
        tool_result = turn2.messages[2].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.content == "[REDACTED]"


# --------------------------------------------------------------------------- #
# Sanity: ApiMessageRequest is the type used through-out                      #
# --------------------------------------------------------------------------- #


def test_api_message_request_is_imported_for_modify_request_tests() -> None:
    """Lock the import so refactors that move ApiMessageRequest break here
    rather than silently in the modify_request test path."""
    assert ApiMessageRequest is not None
