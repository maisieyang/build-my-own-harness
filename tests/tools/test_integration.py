"""Integration tests across the tool package -- P2-T2 sub-unit 2e.

Verifies the public API contract:

1. ``BaseTool``, ``ToolRegistry``, ``ToolResult``, ``ToolExecutionContext``
   are reachable via ``openharness.tools`` directly.
2. The package-level public path composes end-to-end: a tool registers,
   the registry projects to a list of ``ToolSpec`` matching ``protocols``'
   shape, and the projected schemas can be passed straight into
   :class:`ApiMessageRequest`.
3. ``QueryContext`` (engine) accepts a real ``ToolRegistry`` -- closes the
   D7.2 hand-off from P2-T1 (was ``object`` placeholder, now tightened).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import Mock

from engine.conftest import _AllowAllChecker
from openharness.api import OpenAICompatibleApiClient
from openharness.engine import QueryContext
from openharness.protocols import ApiMessageRequest, ConversationMessage, TextBlock
from tools.conftest import _FakeTool


def test_public_api_reachable_from_package_root() -> None:
    from openharness.tools import (
        BaseTool,
        ToolExecutionContext,
        ToolRegistry,
        ToolResult,
    )

    # Sanity: each symbol resolved (no AttributeError on import).
    assert BaseTool is not None
    assert ToolExecutionContext is not None
    assert ToolRegistry is not None
    assert ToolResult is not None


def test_registry_schema_round_trips_into_api_request() -> None:
    """End-to-end: register a tool, project to schema, embed in a real request."""
    from openharness.tools import ToolRegistry

    registry = ToolRegistry()
    registry.register(_FakeTool())

    request = ApiMessageRequest(
        model="qwen-plus",
        max_tokens=256,
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        tools=registry.to_api_schema(),
    )
    assert request.tools is not None
    assert len(request.tools) == 1
    assert request.tools[0].name == "Fake"


def test_query_context_accepts_real_tool_registry() -> None:
    """P2-T1 D7.2 hand-off cashed: ``tool_registry`` field accepts ToolRegistry."""
    from openharness.tools import ToolRegistry

    ctx = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", Mock(spec=OpenAICompatibleApiClient)),
        tool_registry=ToolRegistry(),
        permission_checker=_AllowAllChecker(),
        system_prompt="",
        cwd=Path("/tmp"),
        model="qwen-plus",
    )
    assert isinstance(ctx.tool_registry, ToolRegistry)
