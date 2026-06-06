"""End-to-end Phase 12 integration tests — P12-T6 (7a + 7b + 7c).

Three closed loops with only the LLM client stubbed:

1. **Snapshot round-trip via engine** (7a). Turn 1 writes snapshot
   through the engine's per-turn-end writer. Fresh QueryContext
   built via ``from_snapshot``; turn 2's LLM request includes the
   prior turn's messages.

2. **Compact L3 actually-hits integration** (7b). Closes Phase 11
   retro §4 gap: Phase 11 designed L3 to read the session_memory
   checkpoint but never had a producer wiring it. After P12-T1
   the checkpoint exists for real; this test forces a turn where
   L3 fires (instead of L4) by writing the checkpoint, building
   messages just over threshold, and asserting
   ``compact_kind == "session_memory"``.

3. **Chat resume e2e** (7c). REPL-level resume: snapshot exists
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
from openharness.permissions import PermissionMode
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.protocols.stream_events import ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.services.compact import auto_compact_if_needed
from openharness.services.session_memory import get_session_memory_dir
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


class _AllowAllChecker:
    def evaluate(self, *_a: object, **_kw: object) -> object:
        from openharness.permissions import DecisionResult

        return DecisionResult.allow()


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
    session_memory_path: Path | None = None,
    system_prompt: str = "t",
    model: str = "qwen-plus",
) -> QueryContext:
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", client),
        tool_registry=create_default_tool_registry(),
        permission_checker=_AllowAllChecker(),  # type: ignore[arg-type]
        hook_registry=HookRegistry(),
        system_prompt=system_prompt,
        cwd=cwd,
        model=model,
        max_tokens=64,
        max_turns=2,
        permission_mode=PermissionMode.DEFAULT,
        compact_enabled=False,
        session_memory_path=session_memory_path,
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
            permission_checker=_AllowAllChecker(),  # type: ignore[arg-type]
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
            permission_checker=_AllowAllChecker(),  # type: ignore[arg-type]
            cwd=tmp_path,
        )
        assert ctx2.system_prompt == "custom prompt that must survive resume"


# --------------------------------------------------------------------------- #
# 7b — Compact L3 actually-hits integration                                   #
#                                                                             #
# Closes Phase 11 retro §4 gap. Before P12-T1, the session_memory             #
# checkpoint had no engine-side writer — L3 always returned None.             #
# This test writes a checkpoint AND builds messages above threshold,          #
# then asserts L3 (not L4) fires.                                             #
# --------------------------------------------------------------------------- #


class TestCompactL3Hits:
    @pytest.mark.asyncio
    async def test_l3_fires_when_checkpoint_is_fresh_and_threshold_exceeded(
        self, tmp_path: Path
    ) -> None:
        # Step 1: produce a session_memory checkpoint on disk via the
        # engine, by running one turn with session_memory_path set.
        session_path = get_session_memory_dir(tmp_path) / "checkpoint.md"
        stub1 = _RecordingEndTurnStub()
        ctx1 = _ctx(
            stub1,
            cwd=tmp_path,
            snapshot_enabled=False,
            session_memory_path=session_path,
        )
        async for _ev in run_query([_user("hi")], ctx1):
            pass
        assert session_path.exists(), "Phase 12 T1 wiring should have written this"

        # Step 2: build messages just over threshold (same shape as
        # Phase 11 T7-7b L4 test — short messages so L2 doesn't help).
        small_chunk = "x " * 1000  # 2000 chars per message, < L2 threshold
        many_msgs: list[ConversationMessage] = []
        for i in range(50):
            role: str = "user" if i % 2 == 0 else "assistant"
            many_msgs.append(
                ConversationMessage(
                    role=role,  # type: ignore[arg-type]
                    content=[TextBlock(text=f"m{i}: {small_chunk}")],
                )
            )

        # Step 3: invoke auto_compact_if_needed directly with the
        # session_memory path set. L0 should fire (threshold exceeded),
        # L2 should be a no-op (no long text), L3 should HIT (fresh
        # checkpoint just written) before L4 ever runs.
        class _NeverCalledStub:
            async def stream_message(self, request: object) -> AsyncIterator[ApiStreamEvent]:
                del request
                raise AssertionError("L4 LLM should NOT have been called when L3 hits")
                yield  # pragma: no cover - never reached

        result_msgs, result = await auto_compact_if_needed(
            many_msgs,
            model="qwen-plus",
            api_client=cast("SupportsStreamingMessages", _NeverCalledStub()),
            session_memory_path=session_path,
            enabled=True,
            threshold_ratio=0.83,
        )
        assert result.compact_kind == "session_memory"
        assert 3 in result.applied_levels
        # L3 spliced — message count should drop (older messages
        # replaced by a single synthetic checkpoint message + tail)
        assert len(result_msgs) < len(many_msgs)


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
        result1 = runner.invoke(cli_module.app, ["ask", "initial question"])
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
        result1 = runner.invoke(cli_module.app, ["ask", "first question"])
        assert result1.exit_code == 0
        assert len(stub.requests) == 1

        # Turn 2: --resume should load snapshot + append "follow-up"
        stub2 = _RecordingEndTurnStub(response="second answer")
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub2)
        result2 = runner.invoke(cli_module.app, ["ask", "--resume", "follow-up"])
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
