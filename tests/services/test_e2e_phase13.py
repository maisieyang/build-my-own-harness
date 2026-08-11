"""End-to-end Phase 13 integration tests — P13-T4.

Three closed loops with only the LLM client stubbed (real engine +
writers + rotation + CLI subcommands + focus-state inference):

1. **Rotation E2E** (4a): 5 sequential snapshot writes through the
   engine in the same cwd → history/ accumulates 4 entries +
   current.json points to the most recent.

2. **CLI subcommand E2E** (4b): real ``oh ask`` writes a snapshot
   via engine, then ``oh snapshot list / show / gc --dry-run``
   exercise the introspection surface end-to-end.

3. **LLM focus_state E2E** (4c): real engine + stubbed LLM
   returning valid focus-state JSON → snapshot has the authored
   focus state → ``oh snapshot show`` renders it in the output.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from typer.testing import CliRunner

import openharness.cli as cli_module
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


class _SequencedStub:
    """Returns one canned response per call from ``responses``."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls = 0
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)
        idx = self.calls
        self.calls += 1
        text = "ok" if idx >= len(self._responses) else self._responses[idx]
        yield ApiTextDeltaEvent(text=text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _ctx(
    client: object,
    *,
    cwd: Path,
    llm_focus_state_enabled: bool = False,
) -> QueryContext:
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", client),
        tool_registry=create_default_tool_registry(),
        hook_registry=HookRegistry(),
        system_prompt="t",
        cwd=cwd,
        model="qwen-plus",
        max_tokens=64,
        max_turns=2,
        compact_enabled=False,
        snapshot_enabled=True,
        llm_focus_state_enabled=llm_focus_state_enabled,
    )


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


# --------------------------------------------------------------------------- #
# 4a: Rotation E2E                                                            #
# --------------------------------------------------------------------------- #


class TestRotationE2E:
    @pytest.mark.asyncio
    async def test_five_sequential_writes_history_accumulates(self, tmp_path: Path) -> None:
        # Run 5 separate turns through the engine in the same cwd.
        # After: current.json is the 5th, history/ has 4 entries
        # (1st through 4th rotated in newest→oldest order).
        for i in range(5):
            stub = _SequencedStub([f"reply {i}"])
            ctx = _ctx(stub, cwd=tmp_path)
            async for _ev in run_query([_user(f"turn {i}")], ctx):
                pass

        snapshot_dir = get_snapshot_dir(tmp_path)
        assert (snapshot_dir / "current.json").exists()
        history_dir = snapshot_dir / "history"
        history_entries = sorted(history_dir.glob("*.json"))
        assert len(history_entries) == 4

        # current.json holds the most recent turn
        current = load_snapshot(tmp_path)
        assert current["messages"][0]["content"][0]["text"] == "turn 4"


# --------------------------------------------------------------------------- #
# 4b: CLI subcommand E2E (real oh ask → oh snapshot list/show/gc)             #
# --------------------------------------------------------------------------- #


class TestCliSubcommandE2E:
    def test_ask_then_list_show_gc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__ENABLED", "true")

        # Stub builder for oh ask
        stub = _SequencedStub(["ack"])
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()

        # 1. oh ask writes the snapshot via the real engine
        result = runner.invoke(cli_module.headless_app, ["run", "initial question"])
        assert result.exit_code == 0, result.stderr

        # 2. oh snapshot list shows current
        result = runner.invoke(cli_module.app, ["state", "snapshots", "list"])
        assert result.exit_code == 0
        assert "current" in result.stdout

        # 3. oh snapshot show current renders the metadata
        result = runner.invoke(cli_module.app, ["state", "snapshots", "show", "current"])
        assert result.exit_code == 0
        assert "id:" in result.stdout
        assert "model:" in result.stdout
        assert "messages:" in result.stdout

        # 4. oh snapshot gc --dry-run reports nothing under threshold
        result = runner.invoke(cli_module.app, ["state", "snapshots", "gc", "--dry-run"])
        assert result.exit_code == 0
        assert (
            "no snapshots to gc" in result.stdout.lower()
            or "nothing to drop" in result.stdout.lower()
        )


# --------------------------------------------------------------------------- #
# 4c: LLM focus-state E2E                                                     #
# --------------------------------------------------------------------------- #


class TestLlmFocusStateE2E:
    @pytest.mark.asyncio
    async def test_focus_state_round_trips_to_snapshot(self, tmp_path: Path) -> None:
        # Engine fires main convo + focus-state inference; snapshot
        # writer stores authored focus state in tool_metadata.
        stub = _SequencedStub(
            responses=[
                "main convo reply",
                json.dumps({"goal": "ship Phase 13", "next_step": "write retro doc"}),
            ]
        )
        ctx = _ctx(stub, cwd=tmp_path, llm_focus_state_enabled=True)
        async for _ev in run_query([_user("what should I do next?")], ctx):
            pass

        # 2 LLM calls happened
        assert stub.calls == 2

        # Snapshot has authored focus state
        snapshot = load_snapshot(tmp_path)
        focus = snapshot["tool_metadata"]["task_focus_state"]
        assert focus == {"goal": "ship Phase 13", "next_step": "write retro doc"}

    def test_show_renders_snapshot_with_authored_focus_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Hand-write a snapshot with authored focus state → oh snapshot
        # show renders it as JSON via --format json
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
        import os as _os
        from pathlib import Path as _P

        cwd = _P(_os.getcwd())
        snapshot_dir = get_snapshot_dir(cwd)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "schema": "openharness.snapshot.v1",
            "created_at": "2026-05-28T14:30:12+00:00",
            "git_head": "abc1234",
            "cwd": str(cwd.resolve()),
            "model": "qwen-plus",
            "permission_mode": "default",
            "system_prompt": "test",
            "max_tokens": 1024,
            "messages": [],
            "tool_metadata": {
                "recent_files": [],
                "verified_work": [],
                "task_focus_state": {"goal": "active goal", "next_step": "next thing"},
            },
            "extra": {},
        }
        (snapshot_dir / "current.json").write_text(json.dumps(payload))

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["state", "snapshots", "show", "current", "--format", "json"],
        )
        assert result.exit_code == 0
        # JSON output round-trips the focus state
        data = json.loads(result.stdout)
        assert data["tool_metadata"]["task_focus_state"]["goal"] == "active goal"
        assert data["tool_metadata"]["task_focus_state"]["next_step"] == "next thing"
