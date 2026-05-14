"""End-to-end log shape — engine/query.py x observability — P3-T5.5c.

These tests run ``run_query`` against the same stub fixtures that
``tests/engine/test_query.py`` uses, and assert on the **log stream**
shape instead of the event stream:

- The 4 log points (``turn_start`` / ``tool_dispatch`` / ``tool_complete`` /
  ``loop_limit_exceeded``) fire at the right moments.
- Every log carries ``run_id``; turn-scoped logs carry ``turn_id``;
  dispatch logs carry ``tool`` + ``tool_use_id``.
- ``tool_complete`` carries ``duration_ms`` ≥ 0 and ``output_len`` matching
  the real output, but **does not carry the output content itself** (D13.6
  privacy contract).
- ``tool_dispatch.input.path`` is sanitized to cwd-relative when present;
  ``tool_dispatch.input.command`` is reduced to ``first_token (len=N)``.
- ``loop_limit_exceeded`` is emitted at WARNING level so it surfaces under
  the default log level.

This file lives in ``tests/observability/`` because the unit under test
is the observability contract: the dispatch logic was already validated
by ``test_query.py``;here we only verify what shows up in the JSONL.
"""

from __future__ import annotations

import dataclasses
import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from engine.conftest import _AllowAllChecker, _StubApiClient
from openharness.engine.context import QueryContext
from openharness.engine.errors import LoopLimitExceeded
from openharness.engine.query import run_query
from openharness.observability import configure_logging
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.tools import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from openharness.api import SupportsStreamingMessages


# --------------------------------------------------------------------------- #
# Fixtures + helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """Provide a fresh StringIO + teardown. The test body must call
    ``configure_logging(stream=log_stream)`` itself — doing it in this
    fixture would set up logging before pytest-asyncio re-initializes the
    test's event loop, which can reset root.handlers and silently swallow
    every log record.
    """
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _configure(stream: io.StringIO) -> None:
    configure_logging(level="DEBUG", format="json", stream=stream)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    """Parse every JSONL line in the stream. ``Any`` typing is intentional —
    these tests assert on arbitrary JSON shape; ``object`` would force every
    access to be ``cast``."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def _end_turn_event(text: str = "ok") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "end_turn",
        }
    )


def _tool_use_event(
    *,
    tool_use_id: str = "toolu_01",
    tool_input: dict[str, object] | None = None,
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
                        "input": tool_input if tool_input is not None else {"value": "hi"},
                    }
                ],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "tool_use",
        }
    )


def _make_context(
    *,
    api_client: object,
    tool_registry: ToolRegistry | None = None,
    cwd: Path | None = None,
) -> QueryContext:
    from tools.conftest import _FakeTool

    if tool_registry is None:
        tool_registry = ToolRegistry()
        tool_registry.register(_FakeTool())
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", api_client),
        tool_registry=tool_registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test harness",
        cwd=cwd if cwd is not None else Path("/tmp"),
        model="qwen-plus",
        max_tokens=512,
    )


# --------------------------------------------------------------------------- #
# turn_start + loop_limit_exceeded                                            #
# --------------------------------------------------------------------------- #


class TestTurnStartAndLoopLimit:
    async def test_turn_start_fires_per_turn_with_model_and_max_tokens(
        self, log_stream: io.StringIO
    ) -> None:
        _configure(log_stream)
        client = _StubApiClient(events_per_turn=[[_end_turn_event()]])
        context = _make_context(api_client=client)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        events = [e for e in _lines(log_stream) if e["event"] == "turn_start"]
        assert len(events) == 1
        assert events[0]["model"] == "qwen-plus"
        assert events[0]["max_tokens"] == 512
        assert events[0]["turn_id"] == 1  # 1-indexed for humans
        assert "run_id" in events[0]
        assert len(str(events[0]["run_id"])) == 12  # 12-hex from new_run_id

    async def test_run_id_is_stable_across_turns(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        # Two turns, both carry the same run_id but distinct turn_ids.
        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "x"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        turn_starts = [e for e in _lines(log_stream) if e["event"] == "turn_start"]
        assert len(turn_starts) == 2
        assert turn_starts[0]["run_id"] == turn_starts[1]["run_id"]
        assert turn_starts[0]["turn_id"] == 1
        assert turn_starts[1]["turn_id"] == 2

    async def test_loop_limit_exceeded_logs_at_warning_then_raises(
        self, log_stream: io.StringIO
    ) -> None:
        _configure(log_stream)
        # Two tool_use turns, max_turns=2 — exhausts the loop without end_turn.
        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_use_id="t1", tool_input={"value": "1"})],
                [_tool_use_event(tool_use_id="t2", tool_input={"value": "2"})],
            ]
        )
        context = _make_context(api_client=client)
        context = dataclasses.replace(context, max_turns=2)

        with pytest.raises(LoopLimitExceeded):
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                context,
            ):
                pass

        events = _lines(log_stream)
        loop_limit = next(e for e in events if e["event"] == "loop_limit_exceeded")
        assert loop_limit["max_turns"] == 2
        assert loop_limit["level"] == "warning"
        # turn_id is NOT bound at the moment loop_limit fires (it's after
        # the per-turn ``with bind_turn`` block) — but run_id is.
        assert "run_id" in loop_limit
        assert "turn_id" not in loop_limit


# --------------------------------------------------------------------------- #
# tool_dispatch + tool_complete                                               #
# --------------------------------------------------------------------------- #


class TestToolDispatchAndComplete:
    async def test_dispatch_complete_pair_per_tool_use(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "hello"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        events = _lines(log_stream)
        dispatches = [e for e in events if e["event"] == "tool_dispatch"]
        completes = [e for e in events if e["event"] == "tool_complete"]
        assert len(dispatches) == 1
        assert len(completes) == 1

        d = dispatches[0]
        c = completes[0]
        assert d["tool"] == "Fake"
        assert d["tool_use_id"] == "toolu_01"
        assert d["input"] == {"value": "hello"}  # passes through; no path/cmd key
        assert d["turn_id"] == 1

        assert c["tool"] == "Fake"
        assert c["tool_use_id"] == "toolu_01"
        assert c["is_error"] is False
        assert c["duration_ms"] >= 0
        # _FakeTool returns f"value=hello" → output_len = 11
        assert c["output_len"] == len("value=hello")

    async def test_complete_does_not_log_output_content(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        # Privacy contract D13.6 — tool output never appears in the log
        # record verbatim; only its length.
        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "TOPSECRET"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        complete = next(e for e in _lines(log_stream) if e["event"] == "tool_complete")
        # Whole serialized record must not contain the secret.
        record_json = json.dumps(complete)
        assert "TOPSECRET" not in record_json

    async def test_dispatch_pair_ordering_relative_to_messages_helpers(
        self, log_stream: io.StringIO
    ) -> None:
        _configure(log_stream)
        client = _StubApiClient(
            events_per_turn=[
                [_tool_use_event(tool_input={"value": "x"})],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        # Sequence must be: turn_start(1) → tool_dispatch → tool_complete →
        # turn_start(2) → (end_turn, no tool dispatch on turn 2)
        events = [
            e["event"]
            for e in _lines(log_stream)
            if e["event"] in {"turn_start", "tool_dispatch", "tool_complete"}
        ]
        assert events == ["turn_start", "tool_dispatch", "tool_complete", "turn_start"]


# --------------------------------------------------------------------------- #
# Semantic sanitization at log call sites                                     #
# --------------------------------------------------------------------------- #


class TestToolInputSanitization:
    async def test_path_field_becomes_cwd_relative(
        self,
        log_stream: io.StringIO,
        tmp_path: Path,
    ) -> None:
        _configure(log_stream)
        # Build a tool whose input has a 'path' field, dispatched with cwd=tmp_path
        # — the log should show cwd-relative.
        from pydantic import BaseModel

        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

        class PathInput(BaseModel):
            path: str

        class _PathTool(BaseTool[PathInput]):
            name = "PathTool"
            description = "Stub"
            input_model = PathInput
            is_read_only = True

            async def execute(
                self,
                args: PathInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                return ToolResult(output="ok")

        registry = ToolRegistry()
        registry.register(_PathTool())

        nested = tmp_path / "sub" / "file.txt"
        nested.parent.mkdir(parents=True)
        nested.touch()

        client = _StubApiClient(
            events_per_turn=[
                [
                    ApiMessageCompleteEvent.model_validate(
                        {
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_01",
                                        "name": "PathTool",
                                        "input": {"path": str(nested)},
                                    }
                                ],
                            },
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                            "stop_reason": "tool_use",
                        }
                    )
                ],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, tool_registry=registry, cwd=tmp_path)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        dispatch = next(e for e in _lines(log_stream) if e["event"] == "tool_dispatch")
        # cwd-relative: "sub/file.txt", NOT the absolute /private/var/... path.
        assert dispatch["input"]["path"] == "sub/file.txt"
        # The absolute path must NOT appear anywhere in the dispatch record.
        assert str(nested) not in json.dumps(dispatch)

    async def test_command_field_becomes_first_token_plus_length(
        self,
        log_stream: io.StringIO,
    ) -> None:
        _configure(log_stream)
        from pydantic import BaseModel

        from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

        class CmdInput(BaseModel):
            command: str

        class _CmdTool(BaseTool[CmdInput]):
            name = "CmdTool"
            description = "Stub"
            input_model = CmdInput
            is_read_only = False

            async def execute(
                self,
                args: CmdInput,
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                return ToolResult(output="ok")

        registry = ToolRegistry()
        registry.register(_CmdTool())

        secret_cmd = 'curl -H "Authorization: Bearer sk-1234567890abcdefghij" https://x.com'
        client = _StubApiClient(
            events_per_turn=[
                [
                    ApiMessageCompleteEvent.model_validate(
                        {
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_01",
                                        "name": "CmdTool",
                                        "input": {"command": secret_cmd},
                                    }
                                ],
                            },
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                            "stop_reason": "tool_use",
                        }
                    )
                ],
                [_end_turn_event()],
            ]
        )
        context = _make_context(api_client=client, tool_registry=registry)

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            context,
        ):
            pass

        dispatch = next(e for e in _lines(log_stream) if e["event"] == "tool_dispatch")
        # command field shows first token + length; no secret leakage.
        assert dispatch["input"]["command"].startswith("curl (len=")
        assert "sk-1234567890" not in json.dumps(dispatch)
        assert "Bearer" not in json.dumps(dispatch)
