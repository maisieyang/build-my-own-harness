"""Tests for ``execute_hook_chain`` — the chain resolve algorithm — P3-T4.4d.

This is the algorithmic core of the hook system. Per Three-Axis B:
modify accumulates, first-deny-wins. Per Three-Axis F:Post-event "deny"
auto-converts to modify-as-error. Per Three-Axis G:hook exceptions fire
OnError(one level) then raise HookError.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.errors import HookError
from openharness.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    OnErrorContext,
    PostToolUseContext,
    PreToolUseContext,
    execute_hook_chain,
)
from openharness.tools.base import ToolExecutionContext, ToolResult


def _ctx_pre(tool_input: dict[str, object] | None = None) -> PreToolUseContext:
    return PreToolUseContext(
        tool_name="X",
        tool_input=dict(tool_input or {}),
        exec_context=ToolExecutionContext(cwd=Path("/tmp")),
    )


def _ctx_post(result: ToolResult | None = None) -> PostToolUseContext:
    return PostToolUseContext(
        tool_name="X",
        tool_input={},
        exec_context=ToolExecutionContext(cwd=Path("/tmp")),
        result=result or ToolResult(output="raw output"),
    )


class TestEmptyChain:
    async def test_no_hooks_returns_none(self) -> None:
        result = await execute_hook_chain(HookRegistry(), "PreToolUse", _ctx_pre())
        assert result is None


class TestPassThroughHooks:
    async def test_all_hooks_return_none_yields_none(self) -> None:
        async def noop(ctx: HookContext) -> HookResult | None:
            del ctx
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", noop)
        registry.register("PreToolUse", noop)
        result = await execute_hook_chain(registry, "PreToolUse", _ctx_pre())
        assert result is None

    async def test_explicit_allow_treated_as_passthrough(self) -> None:
        async def allow_hook(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.allow()

        registry = HookRegistry()
        registry.register("PreToolUse", allow_hook)
        result = await execute_hook_chain(registry, "PreToolUse", _ctx_pre())
        assert result is None  # explicit allow == none, no modifications


class TestFirstDenyWins:
    async def test_first_deny_short_circuits(self) -> None:
        observed: list[str] = []

        async def hook_a(ctx: HookContext) -> HookResult | None:
            del ctx
            observed.append("A")
            return None

        async def hook_b(ctx: HookContext) -> HookResult | None:
            del ctx
            observed.append("B")
            return HookResult.deny("B rejected")

        async def hook_c(ctx: HookContext) -> HookResult | None:
            del ctx
            observed.append("C")  # should not run
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", hook_a)
        registry.register("PreToolUse", hook_b)
        registry.register("PreToolUse", hook_c)

        result = await execute_hook_chain(registry, "PreToolUse", _ctx_pre())
        assert result is not None
        assert result.decision == "deny"
        assert result.message == "B rejected"
        # B short-circuited the chain; C did not run.
        assert observed == ["A", "B"]


class TestModifyAccumulates:
    async def test_chained_modify_each_sees_previous(self) -> None:
        seen_inputs: list[dict[str, object]] = []

        async def hook_a(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PreToolUseContext)
            seen_inputs.append(dict(ctx.tool_input))
            return HookResult.modify_input({"path": "/safer/version"})

        async def hook_b(ctx: HookContext) -> HookResult | None:
            # Should see hook_a's modified input.
            assert isinstance(ctx, PreToolUseContext)
            seen_inputs.append(dict(ctx.tool_input))
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", hook_a)
        registry.register("PreToolUse", hook_b)

        ctx = _ctx_pre({"path": "/etc/passwd"})
        result = await execute_hook_chain(registry, "PreToolUse", ctx)
        # hook_b saw modified version.
        assert seen_inputs == [{"path": "/etc/passwd"}, {"path": "/safer/version"}]
        # Final result reflects accumulated modify.
        assert result is not None
        assert result.decision == "modify"
        assert result.new_input == {"path": "/safer/version"}

    async def test_modify_then_deny_uses_modified_input(self) -> None:
        """The marquee scenario from Three-Axis B:hook_A 'saves' a deny
        by modifying input before hook_B inspects it."""

        async def rewrite_to_sandbox(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PreToolUseContext)
            path = ctx.tool_input.get("path", "")
            return HookResult.modify_input({"path": f"/sandbox{path}"})

        async def deny_if_outside_sandbox(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, PreToolUseContext)
            path = ctx.tool_input.get("path", "")
            if not isinstance(path, str) or not path.startswith("/sandbox"):
                return HookResult.deny("outside sandbox")
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", rewrite_to_sandbox)
        registry.register("PreToolUse", deny_if_outside_sandbox)

        ctx = _ctx_pre({"path": "/etc/passwd"})
        result = await execute_hook_chain(registry, "PreToolUse", ctx)
        # rewrite saved it — deny didn't fire.
        assert result is not None
        assert result.decision == "modify"
        assert result.new_input == {"path": "/sandbox/etc/passwd"}

    async def test_last_modify_wins_per_field(self) -> None:
        async def hook_a(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_input({"path": "from_A"})

        async def hook_b(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_input({"path": "from_B"})

        registry = HookRegistry()
        registry.register("PreToolUse", hook_a)
        registry.register("PreToolUse", hook_b)

        result = await execute_hook_chain(registry, "PreToolUse", _ctx_pre())
        assert result is not None
        assert result.new_input == {"path": "from_B"}  # last wins


class TestPostHookSoftDeny:
    """PostToolUse / PostApiCall ``decision="deny"`` auto-converts to
    modify-as-error (Three-Axis F:soft post-deny)."""

    async def test_post_tool_use_deny_becomes_modify_is_error(self) -> None:
        async def post_deny(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.deny("contains secret")

        registry = HookRegistry()
        registry.register("PostToolUse", post_deny)

        result = await execute_hook_chain(registry, "PostToolUse", _ctx_post())
        assert result is not None
        # NOT a deny — converted to modify-as-error.
        assert result.decision == "modify"
        assert result.new_is_error is True
        assert "post-hook deny" in (result.new_output or "")
        assert "contains secret" in (result.new_output or "")


class TestHookExceptionFiresOnError:
    async def test_hook_crash_raises_hook_error(self) -> None:
        async def crasher(ctx: HookContext) -> HookResult | None:
            del ctx
            raise ValueError("boom")

        registry = HookRegistry()
        registry.register("PreToolUse", crasher)

        with pytest.raises(HookError) as exc_info:
            await execute_hook_chain(registry, "PreToolUse", _ctx_pre())
        # The original exception chains via __cause__.
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert "boom" in str(exc_info.value.__cause__)

    async def test_hook_crash_fires_on_error_chain_before_raising(self) -> None:
        on_error_observed: list[OnErrorContext] = []

        async def crasher(ctx: HookContext) -> HookResult | None:
            del ctx
            raise RuntimeError("hook A crashed")

        async def on_error_observer(ctx: HookContext) -> HookResult | None:
            assert isinstance(ctx, OnErrorContext)
            on_error_observed.append(ctx)
            return None

        registry = HookRegistry()
        registry.register("PreToolUse", crasher)
        registry.register("OnError", on_error_observer)

        with pytest.raises(HookError):
            await execute_hook_chain(registry, "PreToolUse", _ctx_pre())

        assert len(on_error_observed) == 1
        assert on_error_observed[0].where == "hook"
        assert on_error_observed[0].tool_name == "X"
        assert isinstance(on_error_observed[0].exception, RuntimeError)

    async def test_on_error_hook_itself_crashing_is_suppressed(self) -> None:
        """OnError hook crashing must not mask the original error."""

        async def crasher(ctx: HookContext) -> HookResult | None:
            del ctx
            raise RuntimeError("original error")

        async def on_error_that_crashes(ctx: HookContext) -> HookResult | None:
            del ctx
            raise RuntimeError("on_error also crashed")

        registry = HookRegistry()
        registry.register("PreToolUse", crasher)
        registry.register("OnError", on_error_that_crashes)

        with pytest.raises(HookError) as exc_info:
            await execute_hook_chain(registry, "PreToolUse", _ctx_pre())
        # Original error is preserved; OnError's crash silently dropped.
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "original error" in str(exc_info.value.__cause__)

    async def test_on_error_event_itself_does_not_fire_recursive_on_error(
        self,
    ) -> None:
        """An OnError hook crashing must not trigger another OnError chain
        (would infinite recurse)."""
        recursion_check: list[int] = []

        async def on_error_crasher(ctx: HookContext) -> HookResult | None:
            del ctx
            recursion_check.append(1)
            raise RuntimeError("OnError hook crashed")

        registry = HookRegistry()
        registry.register("OnError", on_error_crasher)

        # Calling execute_hook_chain with event=OnError directly:
        on_error_ctx = OnErrorContext(exception=ValueError("original"), where="tool", tool_name="X")
        # The crash here is silently dropped by the suppression in event="OnError"
        # path — we just want to verify no infinite recursion / HookError.
        # Per executor logic: when event == "OnError", crashing hooks raise HookError
        # WITHOUT firing OnError again (the `if event != "OnError"` guard).
        with pytest.raises(HookError):
            await execute_hook_chain(registry, "OnError", on_error_ctx)
        # Hook ran once — no recursion.
        assert recursion_check == [1]


class TestModifyOutputAccumulation:
    async def test_post_hook_modify_output_passes_through(self) -> None:
        async def sanitize(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.modify_output("[REDACTED]")

        registry = HookRegistry()
        registry.register("PostToolUse", sanitize)
        result = await execute_hook_chain(registry, "PostToolUse", _ctx_post())
        assert result is not None
        assert result.decision == "modify"
        assert result.new_output == "[REDACTED]"
