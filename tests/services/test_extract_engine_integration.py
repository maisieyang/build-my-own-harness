"""Smoke test for ``run_query`` ↔ ``extract_memories_from_turn`` wiring — P11-T4.4f.

Verifies the engine calls extract at the END of run_query (when
``stop_reason != tool_use``) when ``QueryContext.memory_store`` is
set AND ``extract_enabled=True``. Verifies skip paths when either
is absent.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.hooks import HookRegistry
from openharness.memory.store import FilesystemMemoryStore
from openharness.permissions import PermissionMode
from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


class _ScriptedClient:
    """LLM stub. Returns text-only response (no tool_use) on every call.

    For tests where extract is supposed to fire: the FIRST call is the
    main agent response, the SECOND call is the extract LLM call.
    Both return our scripted text.
    """

    def __init__(self, main_response: str = "ok", extract_response: str | None = None) -> None:
        self._main = main_response
        self._extract = extract_response or json.dumps({"memories": []})
        self.requests: list[ApiMessageRequest] = []
        self.call_count = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)
        # First call = main agent, subsequent = extract
        response = self._main if self.call_count == 0 else self._extract
        self.call_count += 1
        yield ApiTextDeltaEvent(text=response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text=response)],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class _NoOpPermissionChecker:
    def check(self, tool_name: str, tool_input: dict, **_: object) -> object:
        from openharness.permissions import Decision

        return Decision.allow()


def _user_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _build_context(
    client: object,
    *,
    memory_store: object = None,
    extract_enabled: bool = True,
    tmp_path: object = None,
) -> QueryContext:
    from pathlib import Path

    return QueryContext(
        api_client=client,  # type: ignore[arg-type]
        tool_registry=create_default_tool_registry(),
        permission_checker=_NoOpPermissionChecker(),  # type: ignore[arg-type]
        hook_registry=HookRegistry(),
        system_prompt="test prompt",
        cwd=Path(tmp_path) if tmp_path is not None else Path("/tmp"),
        model="qwen-plus",
        max_tokens=128,
        max_turns=2,
        permission_mode=PermissionMode.DEFAULT,
        compact_enabled=False,  # Avoid compact LLM calls inflating call_count
        memory_store=memory_store,
        extract_enabled=extract_enabled,
    )


class TestExtractEngineIntegration:
    @pytest.mark.asyncio
    async def test_no_extract_when_memory_store_none(self) -> None:
        # memory_store=None → engine never calls extract
        client = _ScriptedClient()
        context = _build_context(client, memory_store=None)
        async for _event in run_query([_user_text("hi")], context):
            pass
        # 1 main LLM request, 0 extract calls
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_no_extract_when_disabled(self, tmp_path: Path) -> None:
        # memory_store present but extract_enabled=False
        client = _ScriptedClient()
        store = FilesystemMemoryStore(project_dir=tmp_path)
        context = _build_context(
            client, memory_store=store, extract_enabled=False, tmp_path=tmp_path
        )
        async for _event in run_query([_user_text("hi")], context):
            pass
        # 1 main LLM request only
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_extract_fires_at_turn_end(self, tmp_path: Path) -> None:
        # memory_store + extract_enabled + >=2 messages → extract runs
        # Extract returns 1 memory record → 1 file written
        extract_response = json.dumps(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "from-extract",
                        "description": "extracted from turn",
                        "body": "extracted content",
                    }
                ]
            }
        )
        client = _ScriptedClient(extract_response=extract_response)
        store = FilesystemMemoryStore(project_dir=tmp_path)
        context = _build_context(
            client, memory_store=store, extract_enabled=True, tmp_path=tmp_path
        )
        async for _event in run_query([_user_text("hello")], context):
            pass
        # 2 LLM requests: main + extract
        assert client.call_count == 2
        # Memory file actually written
        assert (tmp_path / "from-extract.md").exists()

    @pytest.mark.asyncio
    async def test_extract_failure_does_not_propagate(self, tmp_path: Path) -> None:
        # Malformed extract response → ExtractionResult has error but
        # run_query completes successfully (extraction is contained)
        client = _ScriptedClient(extract_response="not valid json {{{")
        store = FilesystemMemoryStore(project_dir=tmp_path)
        context = _build_context(
            client, memory_store=store, extract_enabled=True, tmp_path=tmp_path
        )
        # Should not raise
        events = []
        async for event in run_query([_user_text("hi")], context):
            events.append(event)
        # Engine still emitted events
        assert len(events) > 0
        # Extract called but produced no files
        assert client.call_count == 2
        assert list(tmp_path.iterdir()) == []
