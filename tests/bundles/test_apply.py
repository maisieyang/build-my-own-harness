"""Tests for three-layer bundle composition after authorization convergence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openharness.bundles.apply import apply_bundle_to_context
from openharness.bundles.errors import UnknownBundleError
from openharness.bundles.hook_plugins import HookSpec
from openharness.bundles.hooks import audit_log, deny_writes
from openharness.bundles.model import Bundle
from openharness.bundles.registry import WhitelistRegistry
from openharness.hooks import HookRegistry
from openharness.tools import Read, ToolRegistry, Write


def _bundle(
    *,
    system_prompt: str | None = None,
    tools_whitelist: tuple[str, ...] | None = None,
    hook_names: tuple[str, ...] = (),
) -> Bundle:
    return Bundle(
        name="test",
        description="test bundle",
        system_prompt=system_prompt,
        tools_whitelist=tools_whitelist,
        hook_names=hook_names,
        source_path=Path("/fake/test.md"),
    )


def _inputs() -> tuple[ToolRegistry, HookRegistry]:
    tools = ToolRegistry()
    tools.register(Read())
    tools.register(Write())
    return tools, HookRegistry()


def test_empty_bundle_passes_tools_and_prompt_through_but_clones_hooks() -> None:
    tools, hooks = _inputs()
    result = apply_bundle_to_context(
        bundle=_bundle(), tool_registry=tools, hook_registry=hooks, system_prompt="base"
    )
    assert result.tool_registry is tools
    assert result.system_prompt == "base"
    assert result.hook_registry is not hooks
    assert result.hook_registry.is_empty()


def test_prompt_whitelist_and_hooks_compose() -> None:
    tools, hooks = _inputs()
    result = apply_bundle_to_context(
        bundle=_bundle(
            system_prompt="review",
            tools_whitelist=("Read",),
            hook_names=("audit_log", "deny_writes"),
        ),
        tool_registry=tools,
        hook_registry=hooks,
        system_prompt="base",
    )
    assert result.system_prompt == "review"
    assert isinstance(result.tool_registry, WhitelistRegistry)
    assert [tool.name for tool in result.tool_registry.list_tools()] == ["Read"]
    assert audit_log in result.hook_registry.get("PostToolUse")
    assert deny_writes in result.hook_registry.get("PreToolUse")


def test_base_hook_order_is_preserved_without_aliasing() -> None:
    tools, hooks = _inputs()
    user_hook = AsyncMock()
    hooks.register("PreToolUse", user_hook)
    result = apply_bundle_to_context(
        bundle=_bundle(hook_names=("deny_writes",)),
        tool_registry=tools,
        hook_registry=hooks,
        system_prompt="base",
    )
    assert result.hook_registry.get("PreToolUse") == [user_hook, deny_writes]
    assert hooks.get("PreToolUse") == [user_hook]


def test_unknown_hook_fails_explicitly() -> None:
    tools, hooks = _inputs()
    with pytest.raises(UnknownBundleError):
        apply_bundle_to_context(
            bundle=_bundle(hook_names=("unknown",)),
            tool_registry=tools,
            hook_registry=hooks,
            system_prompt="base",
        )


def test_plugin_hook_catalog_is_supported() -> None:
    tools, hooks = _inputs()

    async def notify(_context: object) -> None:
        return None

    catalog = {"notify": HookSpec(event="PreToolUse", hook=notify)}
    result = apply_bundle_to_context(
        bundle=_bundle(hook_names=("notify",)),
        tool_registry=tools,
        hook_registry=hooks,
        system_prompt="base",
        plugin_hook_catalog=catalog,
    )
    assert notify in result.hook_registry.get("PreToolUse")
