"""End-to-end Phase 12 integration tests — snapshot and resume.

Three closed loops with only the LLM client stubbed:

1. **Snapshot round-trip via engine** (7a). Turn 1 writes snapshot
   through the engine's per-turn-end writer. Fresh QueryContext
   built via ``from_snapshot``; turn 2's LLM request includes the
   prior turn's messages.

2. **Chat resume e2e** (7c). REPL-level resume: snapshot exists
   → ``oh chat --resume`` loads + banner + accepts next input →
   captured request includes prior history.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.hooks import HookRegistry
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.protocols.stream_events import ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.services.snapshot import (
    get_snapshot_dir,
    load_snapshot,
)
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.api import SupportsStreamingMessages
    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


class _RecordingEndTurnStub:
    """Captures every request, always returns end_turn."""

    def __init__(self, *, response: str = "ok") -> None:
        self.requests: list[ApiMessageRequest] = []
        self._response = response

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)
        yield ApiTextDeltaEvent(text=self._response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._response)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _ctx(
    client: object,
    *,
    cwd: Path,
    snapshot_enabled: bool = True,
    system_prompt: str = "t",
    model: str = "qwen-plus",
) -> QueryContext:
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", client),
        tool_registry=create_default_tool_registry(),
        hook_registry=HookRegistry(),
        system_prompt=system_prompt,
        cwd=cwd,
        model=model,
        max_tokens=64,
        max_turns=2,
        compact_enabled=False,
        snapshot_enabled=snapshot_enabled,
    )


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


# --------------------------------------------------------------------------- #
# 7a — Snapshot round trip via engine                                         #
# --------------------------------------------------------------------------- #


class TestSnapshotRoundTrip:
    @pytest.mark.asyncio
    async def test_engine_writes_snapshot_then_from_snapshot_rebuilds_history(
        self, tmp_path: Path
    ) -> None:
        # Turn 1: engine writes snapshot to tmp_path's snapshot dir.
        stub1 = _RecordingEndTurnStub(response="answer 1")
        ctx1 = _ctx(stub1, cwd=tmp_path, snapshot_enabled=True)
        async for _ev in run_query([_user("question 1")], ctx1):
            pass

        # Snapshot must exist on disk
        snapshot_path = get_snapshot_dir(tmp_path) / "current.json"
        assert snapshot_path.exists()

        # Load + rebuild context (Phase 12 from_snapshot factory)
        snapshot = load_snapshot(tmp_path)
        stub2 = _RecordingEndTurnStub(response="answer 2")
        ctx2, prior_messages = QueryContext.from_snapshot(
            snapshot,
            api_client=cast("SupportsStreamingMessages", stub2),
            tool_registry=create_default_tool_registry(),
            cwd=tmp_path,
        )

        # The snapshot captured turn 1's full final history (user + assistant)
        assert len(prior_messages) == 2
        first_block_0 = prior_messages[0].content[0]
        first_block_1 = prior_messages[1].content[0]
        assert isinstance(first_block_0, TextBlock)
        assert isinstance(first_block_1, TextBlock)
        assert first_block_0.text == "question 1"
        assert first_block_1.text == "answer 1"

        # Turn 2: append a new user message + run_query
        next_messages = [*prior_messages, _user("question 2")]
        async for _ev in run_query(next_messages, ctx2):
            pass

        # Turn 2's request includes the full prior history + the new prompt
        assert len(stub2.requests) == 1
        sent = stub2.requests[0].messages
        assert len(sent) == 3
        third_block = sent[-1].content[0]
        assert isinstance(third_block, TextBlock)
        assert third_block.text == "question 2"

    @pytest.mark.asyncio
    async def test_snapshot_round_trip_preserves_system_prompt(self, tmp_path: Path) -> None:
        # D30.2 contract: system_prompt loaded verbatim on resume,
        # NOT re-rendered from registries.
        stub1 = _RecordingEndTurnStub()
        ctx1 = _ctx(
            stub1,
            cwd=tmp_path,
            snapshot_enabled=True,
            system_prompt="custom prompt that must survive resume",
        )
        async for _ev in run_query([_user("hi")], ctx1):
            pass

        snapshot = load_snapshot(tmp_path)
        stub2 = _RecordingEndTurnStub()
        ctx2, _ = QueryContext.from_snapshot(
            snapshot,
            api_client=cast("SupportsStreamingMessages", stub2),
            tool_registry=create_default_tool_registry(),
            cwd=tmp_path,
        )
        assert ctx2.system_prompt == "custom prompt that must survive resume"


# --------------------------------------------------------------------------- #
# 7c — Chat resume e2e via CLI                                                #
# --------------------------------------------------------------------------- #


class TestChatResumeE2E:
    def test_chat_resume_loads_snapshot_into_repl_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Run a single ``oh ask`` first to produce a real snapshot
        # via the engine writer. Then run ``oh chat --resume`` and
        # capture the resulting REPL behavior.
        from typer.testing import CliRunner

        import openharness.cli as cli_module

        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__ENABLED", "true")

        stub = _RecordingEndTurnStub(response="reply")
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        # First invocation: write snapshot via engine
        runner = CliRunner()
        result1 = runner.invoke(cli_module.headless_app, ["run", "initial question"])
        assert result1.exit_code == 0, result1.stderr

        # Verify snapshot exists in conftest's cwd_dir
        import os as _os
        from pathlib import Path as _P

        snapshot_dir = get_snapshot_dir(_P(_os.getcwd()))
        assert (snapshot_dir / "current.json").exists()

        # Second invocation: oh chat --resume with one input then exit
        def _input_seq(prompt: str = "") -> str:
            del prompt
            # Iterator state via closure
            if not hasattr(_input_seq, "_called"):
                _input_seq._called = True  # type: ignore[attr-defined]
                return "/exit"
            raise EOFError

        import builtins

        monkeypatch.setattr(builtins, "input", _input_seq)

        result2 = runner.invoke(cli_module.app, ["chat", "--resume"])
        assert result2.exit_code == 0, result2.stderr
        # Banner mentions message count from the snapshot
        assert "resumed:" in result2.stdout
        # Snapshot from turn 1 had 2 messages (user + assistant)
        assert "2 messages" in result2.stdout

    def test_ask_resume_e2e_via_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from typer.testing import CliRunner

        import openharness.cli as cli_module

        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__ENABLED", "true")

        stub = _RecordingEndTurnStub(response="first answer")
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        # Turn 1: writes snapshot
        result1 = runner.invoke(cli_module.headless_app, ["run", "first question"])
        assert result1.exit_code == 0
        assert len(stub.requests) == 1

        # Turn 2: --resume should load snapshot + append "follow-up"
        stub2 = _RecordingEndTurnStub(response="second answer")
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub2)
        result2 = runner.invoke(cli_module.headless_app, ["run", "--resume", "follow-up"])
        assert result2.exit_code == 0, result2.stderr
        # Second LLM call's request includes turn 1 history + new prompt
        assert len(stub2.requests) == 1
        sent = stub2.requests[0].messages
        # 2 prior messages from snapshot + new follow-up = 3
        assert len(sent) == 3
        last_block = sent[-1].content[0]
        assert isinstance(last_block, TextBlock)
        assert last_block.text == "follow-up"


# Silence unused-import warning when asyncio not directly referenced
_ = asyncio
