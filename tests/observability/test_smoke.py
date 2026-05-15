"""End-to-end observability smoke — P3-T5.5f.

The full pipeline: ``oh ask`` → CLI flags → ``_run_ask`` → ``run_query``
→ ``_dispatch_one`` → logging.StreamHandler(stderr) → JSONL on stderr.

This is the only test that exercises the *entire* path with real engine
+ real ``_dispatch_one`` + real ``configure_logging`` from the CLI seam.
Everything below is real except for two seams:

- ``_build_client`` is monkeypatched to a stub yielding canned events
- ``create_default_tool_registry`` is monkeypatched to a registry holding
  a ``_FakeTool`` (so AuthZ + dispatch see a real tool path without
  touching the filesystem)

The smoke asserts the **observable contract** of Phase 3 observability:

1. Default WARNING level suppresses the 4 lifecycle log points;
   ``--log-level INFO`` reveals them.
2. ``--log-format json`` puts one JSON record per line on stderr.
3. The 3-ID trace tree is reconstructable from stderr alone:
   - ``run_id`` is stable across every log line (12-hex)
   - ``turn_id`` increments per LLM turn (1-indexed)
   - ``tool_use_id`` ties each ``tool_dispatch`` to its ``tool_complete``
4. stdout still carries the LLM text response (轴 3 — log goes stderr,
   user output goes stdout).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.tools import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# --------------------------------------------------------------------------- #
# Multi-turn stub — extends test_cli's _RecordingStubClient to support N turns #
# --------------------------------------------------------------------------- #


class _MultiTurnStubClient:
    """Yields a pre-canned event sequence per turn.

    Constructed with ``[turn1_events, turn2_events, ...]``. Each
    ``stream_message`` call advances to the next inner list.
    """

    def __init__(self, events_per_turn: list[list[ApiStreamEvent]]) -> None:
        self._events_per_turn = events_per_turn
        self._turn = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        del request
        events = self._events_per_turn[self._turn]
        self._turn += 1
        for event in events:
            yield event


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _fake_tool_use_complete(
    tool_use_id: str = "toolu_smoke_01",
) -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Fake",
                        "input": {"value": "smoke"},
                    }
                ],
            },
            "usage": {"input_tokens": 5, "output_tokens": 5},
            "stop_reason": "tool_use",
        }
    )


def _end_turn_complete(text: str = "done") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent(
        message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
        usage=UsageSnapshot(input_tokens=5, output_tokens=2),
        stop_reason="end_turn",
    )


def _fake_tool_registry() -> ToolRegistry:
    from tools.conftest import _FakeTool

    registry = ToolRegistry()
    registry.register(_FakeTool())
    return registry


# --------------------------------------------------------------------------- #
# The smokes                                                                  #
# --------------------------------------------------------------------------- #


class TestObservabilityE2E:
    """Phase 3 observability — full path through ``oh ask`` CLI."""

    def test_jsonl_trace_reconstructable_from_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The headline smoke. ``oh ask`` with ``--log-format json
        --log-level INFO`` emits a JSONL stream on stderr that can be
        parsed and reconstructed into a 3-ID trace tree."""
        _set_minimum_env(monkeypatch)

        # 2-turn flow:turn 1 calls Fake tool;turn 2 ends.
        stub = _MultiTurnStubClient(
            [
                [_fake_tool_use_complete()],
                [_end_turn_complete("done")],
            ]
        )
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
        monkeypatch.setattr(cli_module, "create_default_tool_registry", _fake_tool_registry)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "smoke", "--log-level", "INFO", "--log-format", "json"],
        )

        assert result.exit_code == 0, result.stderr

        # stdout carries user-facing rendering (tool dispatch lines from
        # ``_stream_render``);log lines (JSON) must NOT appear there.
        assert "[Fake]" in result.stdout  # _stream_render prints tool name
        assert "turn_start" not in result.stdout
        assert "{" not in result.stdout  # no JSON record leaked to stdout

        # Parse stderr as JSONL.
        lines = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
        assert lines, "expected at least one JSON log line on stderr"

        # ── 1. 4 expected lifecycle events present (no retry / no limit) ── #
        events = {e["event"] for e in lines if "event" in e}
        assert "turn_start" in events
        assert "tool_dispatch" in events
        assert "tool_complete" in events
        # No retry (stub never raises) and no loop_limit_exceeded (end_turn hit).
        assert "loop_limit_exceeded" not in events

        # ── 2. run_id stable across every line, 12-hex ─────────────────── #
        run_ids = {e["run_id"] for e in lines if "run_id" in e}
        assert len(run_ids) == 1
        rid = next(iter(run_ids))
        assert len(rid) == 12
        int(rid, 16)  # hex characters only — raises if not

        # ── 3. turn_id 1-indexed, two turns ────────────────────────────── #
        turn_ids = sorted({e["turn_id"] for e in lines if "turn_id" in e})
        assert turn_ids == [1, 2]

        # ── 4. tool_use_id ties dispatch ↔ complete pair ───────────────── #
        dispatch = next(e for e in lines if e["event"] == "tool_dispatch")
        complete = next(e for e in lines if e["event"] == "tool_complete")
        assert dispatch["tool_use_id"] == complete["tool_use_id"]
        assert dispatch["tool_use_id"] == "toolu_smoke_01"
        # tool_dispatch + tool_complete both inside turn 1 (the tool_use turn).
        assert dispatch["turn_id"] == 1
        assert complete["turn_id"] == 1
        # Real dispatch — duration_ms is a number, not None.
        assert isinstance(complete["duration_ms"], (int, float))
        assert complete["duration_ms"] >= 0
        assert complete["is_error"] is False
        # _FakeTool returns f"value={args.value}" → len("value=smoke") == 11.
        assert complete["output_len"] == len("value=smoke")

    def test_default_warning_level_suppresses_lifecycle_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ``--log-level INFO``, the 4 lifecycle logs are at INFO
        and get filtered. stderr is empty (or only carries non-INFO output
        like Click usage)."""
        _set_minimum_env(monkeypatch)
        stub = _MultiTurnStubClient([[_end_turn_complete("hi")]])
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
        monkeypatch.setattr(cli_module, "create_default_tool_registry", _fake_tool_registry)

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["ask", "smoke", "--log-format", "json"],
        )

        assert result.exit_code == 0
        # No INFO-level lifecycle events in stderr at default WARNING.
        # (Empty or whitespace-only; cannot assert exactly "" because
        # rendering / pytest infra may add trailing newlines.)
        json_lines = [line for line in result.stderr.splitlines() if line.strip().startswith("{")]
        assert json_lines == []

    def test_run_id_unique_across_two_separate_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each ``oh ask`` invocation mints a fresh run_id — two runs in
        the same process must not share trace IDs."""
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "create_default_tool_registry", _fake_tool_registry)

        def _run_once() -> str:
            stub = _MultiTurnStubClient([[_end_turn_complete("hi")]])
            monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
            runner = CliRunner()
            result = runner.invoke(
                cli_module.app,
                ["ask", "smoke", "--log-level", "INFO", "--log-format", "json"],
            )
            assert result.exit_code == 0
            lines = [
                json.loads(line)
                for line in result.stderr.splitlines()
                if line.strip().startswith("{")
            ]
            # Grab the run_id from any line that carries it.
            # ``json.loads`` returns Any, so cast to str for the return type.
            return str(next(e["run_id"] for e in lines if "run_id" in e))

        rid_1 = _run_once()
        rid_2 = _run_once()
        assert rid_1 != rid_2
