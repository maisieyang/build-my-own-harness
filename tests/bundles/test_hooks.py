"""Tests for ``BUILTIN_HOOKS`` + ``resolve_hook`` — P5d-T2.

Two surfaces:

1. **Hook behavior** — ``audit_log`` emits the right log; ``deny_writes``
   denies non-read-only tools and allows read-only ones.
2. **Registry lookup** — ``resolve_hook`` returns the right
   ``(event, callable)`` pair;unknown name raises ``UnknownBundleError``.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from openharness.bundles.errors import UnknownBundleError
from openharness.bundles.hooks import (
    BUILTIN_HOOKS,
    audit_log,
    deny_writes,
    resolve_hook,
)
from openharness.engine.context import QueryContext
from openharness.hooks.context import PostToolUseContext, PreToolUseContext
from openharness.hooks.result import HookResult
from openharness.observability import configure_logging, get_logger
from openharness.tools import ToolRegistry
from openharness.tools.base import ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


# --------------------------------------------------------------------------- #
# audit_log                                                                   #
# --------------------------------------------------------------------------- #


class TestAuditLog:
    async def test_emits_audit_event(self, stream: io.StringIO, tmp_path: Path) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        log = get_logger("bundles")
        del log  # silence unused-var warning; we use the module logger

        ctx = PostToolUseContext(
            tool_name="Read",
            tool_use_id="toolu_x",
            tool_input={"path": "/foo"},
            exec_context=ToolExecutionContext(cwd=tmp_path),
            result=ToolResult(output="contents", is_error=False),
        )
        result = await audit_log(ctx)
        # Passthrough — doesn't modify the ToolResult.
        assert result is None

        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        # Find the audit event we just emitted.
        audit_events = [r for r in lines if r.get("event") == "audit_tool_complete"]
        assert len(audit_events) == 1
        record = audit_events[0]
        assert record["tool_name"] == "Read"
        assert record["tool_use_id"] == "toolu_x"
        assert record["is_error"] is False
        assert record["output_len"] == len("contents")

    async def test_passthrough_on_wrong_context_type(self, tmp_path: Path) -> None:
        # If audit_log is somehow called with PreToolUseContext (shouldn't
        # happen since it registers on PostToolUse only), it should
        # passthrough gracefully rather than crash.
        ctx = PreToolUseContext(
            tool_name="x",
            tool_use_id="t",
            tool_input={},
            exec_context=ToolExecutionContext(cwd=tmp_path),
        )
        assert await audit_log(ctx) is None


# --------------------------------------------------------------------------- #
# deny_writes                                                                 #
# --------------------------------------------------------------------------- #


def _make_exec_context_with_parent(
    *, cwd: Path, tool_is_read_only: bool, tool_name: str = "X"
) -> ToolExecutionContext:
    """Build a ToolExecutionContext whose parent_query.tool_registry has
    one fake tool with the specified ``is_read_only`` value.
    """
    registry = ToolRegistry()
    fake_tool = MagicMock()
    fake_tool.is_read_only = tool_is_read_only
    fake_tool.name = tool_name
    # ToolRegistry.register uses .name attribute — fake_tool has it.
    registry._tools[tool_name] = fake_tool

    parent = MagicMock(spec=QueryContext)
    parent.tool_registry = registry
    return ToolExecutionContext(cwd=cwd, parent_query=parent)


class TestDenyWrites:
    async def test_allows_read_only_tool(self, tmp_path: Path) -> None:
        exec_ctx = _make_exec_context_with_parent(
            cwd=tmp_path, tool_is_read_only=True, tool_name="Read"
        )
        pre = PreToolUseContext(
            tool_name="Read",
            tool_use_id="t1",
            tool_input={},
            exec_context=exec_ctx,
        )
        result = await deny_writes(pre)
        # Read is read-only — passthrough.
        assert result is None

    async def test_denies_mutating_tool(self, tmp_path: Path) -> None:
        exec_ctx = _make_exec_context_with_parent(
            cwd=tmp_path, tool_is_read_only=False, tool_name="Write"
        )
        pre = PreToolUseContext(
            tool_name="Write",
            tool_use_id="t1",
            tool_input={},
            exec_context=exec_ctx,
        )
        result = await deny_writes(pre)
        assert isinstance(result, HookResult)
        assert result.decision == "deny"
        assert result.message is not None
        assert "deny_writes" in result.message
        assert "Write" in result.message

    async def test_passthrough_when_tool_not_in_registry(self, tmp_path: Path) -> None:
        # If the tool the LLM called doesn't exist, the engine surfaces its
        # own "not found" error — our hook shouldn't deny in that case
        # (it'd mask the real error).
        exec_ctx = _make_exec_context_with_parent(
            cwd=tmp_path, tool_is_read_only=True, tool_name="OtherTool"
        )
        pre = PreToolUseContext(
            tool_name="NonexistentTool",
            tool_use_id="t1",
            tool_input={},
            exec_context=exec_ctx,
        )
        assert await deny_writes(pre) is None

    async def test_passthrough_when_no_parent_query(self, tmp_path: Path) -> None:
        # If exec_context lacks parent_query, hook can't look up the
        # tool; passthrough rather than crash.
        pre = PreToolUseContext(
            tool_name="Write",
            tool_use_id="t1",
            tool_input={},
            exec_context=ToolExecutionContext(cwd=tmp_path),  # parent_query=None
        )
        assert await deny_writes(pre) is None

    async def test_passthrough_on_wrong_context_type(self, tmp_path: Path) -> None:
        ctx = PostToolUseContext(
            tool_name="x",
            tool_use_id="t",
            tool_input={},
            exec_context=ToolExecutionContext(cwd=tmp_path),
            result=ToolResult(output="", is_error=False),
        )
        assert await deny_writes(ctx) is None


# --------------------------------------------------------------------------- #
# resolve_hook                                                                #
# --------------------------------------------------------------------------- #


class TestResolveHook:
    def test_audit_log_registered(self) -> None:
        event, hook = resolve_hook("audit_log")
        assert event == "PostToolUse"
        assert hook is audit_log

    def test_deny_writes_registered(self) -> None:
        event, hook = resolve_hook("deny_writes")
        assert event == "PreToolUse"
        assert hook is deny_writes

    def test_unknown_hook_raises(self) -> None:
        with pytest.raises(UnknownBundleError) as exc_info:
            resolve_hook("nonexistent")
        assert exc_info.value.name == "nonexistent"
        assert exc_info.value.kind == "hook"
        # Catalog includes both built-ins.
        assert "audit_log" in exc_info.value.available
        assert "deny_writes" in exc_info.value.available

    def test_builtin_hooks_has_both(self) -> None:
        # Sanity: the dict's keys are at minimum these two.
        assert "audit_log" in BUILTIN_HOOKS
        assert "deny_writes" in BUILTIN_HOOKS
