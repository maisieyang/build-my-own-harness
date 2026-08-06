"""Tests for ``HookRegistry`` — P3-T4.4c.

Tiny in-memory registry. Per Three-Axis J:registration order = execution
order (FIFO). Per Three-Axis E:injection via QueryContext (4e), no plugin
discovery in Phase 3.
"""

from __future__ import annotations

from pathlib import Path

from openharness.hooks import (
    Hook,
    HookContext,
    HookRegistry,
    HookResult,
    PreToolUseContext,
)
from openharness.tools.base import ExecutionDomain, ToolExecutionContext, TrustedControlSurface


async def _noop_hook(ctx: HookContext) -> HookResult | None:
    del ctx
    return None


def _another_noop() -> Hook:
    async def _h(ctx: HookContext) -> HookResult | None:
        del ctx
        return None

    return _h


class TestEmptyRegistry:
    def test_hooks_explicitly_declare_trusted_control_plane(self) -> None:
        assert HookRegistry.execution_domain is ExecutionDomain.TRUSTED_CONTROL
        assert HookRegistry.trusted_control_surface is TrustedControlSurface.HOOKS

    def test_empty_registry_returns_empty_list_for_any_event(self) -> None:
        registry = HookRegistry()
        assert registry.get("PreToolUse") == []
        assert registry.get("PostToolUse") == []
        assert registry.get("OnError") == []

    def test_is_empty_true_initially(self) -> None:
        assert HookRegistry().is_empty() is True


class TestRegister:
    def test_register_one_hook_retrievable(self) -> None:
        registry = HookRegistry()
        registry.register("PreToolUse", _noop_hook)
        hooks = registry.get("PreToolUse")
        assert len(hooks) == 1
        assert hooks[0] is _noop_hook

    def test_is_empty_false_after_register(self) -> None:
        registry = HookRegistry()
        registry.register("PreToolUse", _noop_hook)
        assert registry.is_empty() is False

    def test_register_preserves_fifo_order(self) -> None:
        registry = HookRegistry()
        hook_a = _another_noop()
        hook_b = _another_noop()
        hook_c = _another_noop()
        registry.register("PreToolUse", hook_a)
        registry.register("PreToolUse", hook_b)
        registry.register("PreToolUse", hook_c)
        # Three-Axis J:execution order = registration order.
        assert registry.get("PreToolUse") == [hook_a, hook_b, hook_c]

    def test_different_events_independent(self) -> None:
        registry = HookRegistry()
        pre_hook = _another_noop()
        post_hook = _another_noop()
        registry.register("PreToolUse", pre_hook)
        registry.register("PostToolUse", post_hook)
        assert registry.get("PreToolUse") == [pre_hook]
        assert registry.get("PostToolUse") == [post_hook]
        assert registry.get("PreApiCall") == []


class TestGetReturnsCopy:
    def test_caller_can_mutate_returned_list_without_affecting_registry(self) -> None:
        registry = HookRegistry()
        registry.register("PreToolUse", _noop_hook)
        listed = registry.get("PreToolUse")
        listed.clear()  # caller mutates the copy
        # Registry still has the hook.
        assert len(registry.get("PreToolUse")) == 1


class TestRegistryEndToEnd:
    async def test_registered_hook_is_callable_via_registry(self) -> None:
        """Sanity:retrieve a hook, call it with a real context, get a result."""

        async def my_hook(ctx: HookContext) -> HookResult | None:
            del ctx
            return HookResult.deny("test deny")

        registry = HookRegistry()
        registry.register("PreToolUse", my_hook)

        hooks = registry.get("PreToolUse")
        ctx = PreToolUseContext(
            tool_name="X",
            tool_use_id="toolu_test",
            tool_input={},
            exec_context=ToolExecutionContext(cwd=Path("/tmp")),
        )
        result = await hooks[0](ctx)
        assert result is not None
        assert result.decision == "deny"
        assert result.message == "test deny"
