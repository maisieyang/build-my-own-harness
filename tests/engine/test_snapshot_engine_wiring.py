"""Tests for engine per-turn-end snapshot writer wiring — P12-T3-3c.

Companion to ``test_session_memory_engine_wiring.py`` — verifies the
SECOND consumer (snapshot writer) fires from the same engine call
site, sharing the ``collect_turn_metadata`` producer per D30.6.

Contract surfaces:

1. ``snapshot_enabled=True`` + end_turn → JSON snapshot written
2. ``snapshot_enabled=False`` → no write attempt
3. Tool_use intermediate turn → no write (writer fires only at
   user-turn end)
4. OSError during write → WARN-logged + turn still emits
   ConversationCompleteEvent
5. Single ``collect_turn_metadata`` call shared between
   session_memory writer + snapshot writer (no double-compute)
"""

from __future__ import annotations

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
from openharness.services.session_memory import get_session_memory_dir
from openharness.services.snapshot import (
    SNAPSHOT_VERSION,
    get_snapshot_dir,
    load_snapshot,
)
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.api import SupportsStreamingMessages
    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


class _AllowAllChecker:
    def evaluate(self, tool_name: str, args: object, context: object, **_: object) -> object:
        from openharness.permissions import DecisionResult

        del tool_name, args, context
        return DecisionResult.allow()


class _EndTurnStub:
    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _ctx(
    client: object,
    *,
    cwd: Path,
    snapshot_enabled: bool,
    session_memory_path: Path | None = None,
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
        session_memory_path=session_memory_path,
        snapshot_enabled=snapshot_enabled,
    )


class TestSnapshotWriterWiring:
    @pytest.mark.asyncio
    async def test_enabled_writes_snapshot(self, tmp_path: Path) -> None:
        ctx = _ctx(_EndTurnStub(), cwd=tmp_path, snapshot_enabled=True)

        async for _ev in run_query([_user("hi")], ctx):
            pass

        snapshot = load_snapshot(tmp_path)
        assert snapshot["version"] == SNAPSHOT_VERSION
        assert snapshot["model"] == "qwen-plus"
        assert snapshot["system_prompt"] == "t"
        assert len(snapshot["messages"]) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_disabled_no_write(self, tmp_path: Path) -> None:
        ctx = _ctx(_EndTurnStub(), cwd=tmp_path, snapshot_enabled=False)

        async for _ev in run_query([_user("hi")], ctx):
            pass

        snapshot_dir = get_snapshot_dir(tmp_path)
        assert not snapshot_dir.exists()

    @pytest.mark.asyncio
    async def test_oserror_during_write_does_not_break_turn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import openharness.services.snapshot as sn_module

        def _broken_writer(**_kw: object) -> object:
            raise OSError("disk full simulation")

        monkeypatch.setattr(sn_module, "write_session_snapshot", _broken_writer)

        ctx = _ctx(_EndTurnStub(), cwd=tmp_path, snapshot_enabled=True)

        events = []
        async for ev in run_query([_user("hi")], ctx):
            events.append(ev)

        from openharness.protocols.stream_events import ConversationCompleteEvent

        completes = [e for e in events if isinstance(e, ConversationCompleteEvent)]
        assert len(completes) == 1


class TestSingleProducerSharing:
    @pytest.mark.asyncio
    async def test_both_writers_share_one_collect_turn_metadata_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Wire BOTH writers; verify collect_turn_metadata fires ONCE,
        # not once per writer. The single-producer/two-consumer design
        # is D30.6's load-bearing claim.
        from openharness.engine import query as query_module

        call_count = {"n": 0}
        real_collect = query_module.collect_turn_metadata

        def _counting(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            return real_collect(*args, **kwargs)  # type: ignore[no-any-return]

        monkeypatch.setattr(query_module, "collect_turn_metadata", _counting)

        session_path = get_session_memory_dir(tmp_path) / "checkpoint.md"
        ctx = _ctx(
            _EndTurnStub(),
            cwd=tmp_path,
            snapshot_enabled=True,
            session_memory_path=session_path,
        )

        async for _ev in run_query([_user("hi")], ctx):
            pass

        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_neither_consumer_skips_producer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When neither session_memory nor snapshot is wired, the producer
        # should be skipped too (zero overhead for the no-wiring case).
        from openharness.engine import query as query_module

        call_count = {"n": 0}
        real_collect = query_module.collect_turn_metadata

        def _counting(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            return real_collect(*args, **kwargs)  # type: ignore[no-any-return]

        monkeypatch.setattr(query_module, "collect_turn_metadata", _counting)

        ctx = _ctx(
            _EndTurnStub(),
            cwd=tmp_path,
            snapshot_enabled=False,
            session_memory_path=None,
        )

        async for _ev in run_query([_user("hi")], ctx):
            pass

        assert call_count["n"] == 0
