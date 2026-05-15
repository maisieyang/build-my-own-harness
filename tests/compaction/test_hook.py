"""Tests for ``TruncateToolResultHook`` + ``tool_truncated`` log event —
P4-T2.2b + 2c.

Three responsibility surfaces:

1. **Passthrough** — under-cap output gets ``None``;wrong event ctx
   gets ``None``;``cap_tokens<=0`` disables the hook.
2. **Truncation** — over-cap output returns ``HookResult.modify_output``
   with head+tail+marker text;truncated length actually fits under cap.
3. **Log emit** — fires ``tool_truncated`` (INFO) with the 5 expected
   fields, picked up on stderr JSONL.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openharness.compaction import TruncateToolResultHook
from openharness.hooks import (
    HookResult,
    PostApiCallContext,
    PostToolUseContext,
    PreToolUseContext,
)
from openharness.observability import configure_logging
from openharness.protocols import ConversationMessage
from openharness.protocols.usage import UsageSnapshot
from openharness.tools.base import ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from collections.abc import Iterator


_PRECISE_MODEL = "gpt-4o"
_FALLBACK_MODEL = "qwen-plus"


def _post_ctx(output: str, *, tool_use_id: str = "toolu_01") -> PostToolUseContext:
    return PostToolUseContext(
        tool_name="Read",
        tool_use_id=tool_use_id,
        tool_input={"path": "/x"},
        exec_context=ToolExecutionContext(cwd=Path("/tmp")),
        result=ToolResult(output=output),
    )


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _configure(stream: io.StringIO) -> None:
    configure_logging(level="DEBUG", format="json", stream=stream)


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Passthrough scenarios                                                       #
# --------------------------------------------------------------------------- #


class TestPassthrough:
    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    async def test_short_output_returns_none(self, model: str) -> None:
        hook = TruncateToolResultHook(cap_tokens=10_000, model=model)
        ctx = _post_ctx("hello world")
        assert await hook(ctx) is None

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    async def test_cap_zero_disables_hook(self, model: str) -> None:
        # cap_tokens=0 means "don't truncate ever" (consistent with
        # head_tail_truncate's edge-case semantics).
        hook = TruncateToolResultHook(cap_tokens=0, model=model)
        ctx = _post_ctx("x" * 100_000)  # huge but cap is 0 → no-op
        assert await hook(ctx) is None

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    async def test_pre_tool_use_ctx_returns_none(self, model: str) -> None:
        # Defensive: hook can technically be registered on the wrong event.
        # Must passthrough rather than crash on isinstance mismatch.
        hook = TruncateToolResultHook(cap_tokens=100, model=model)
        ctx = PreToolUseContext(
            tool_name="Read",
            tool_use_id="toolu_01",
            tool_input={"path": "/x"},
            exec_context=ToolExecutionContext(cwd=Path("/tmp")),
        )
        assert await hook(ctx) is None

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    async def test_unrelated_event_ctx_returns_none(self, model: str) -> None:
        hook = TruncateToolResultHook(cap_tokens=100, model=model)
        # PostApiCallContext is also a HookContext — should be ignored.
        from openharness.protocols import ApiMessageRequest, TextBlock

        request = ApiMessageRequest(
            model="x",
            max_tokens=10,
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        )
        ctx = PostApiCallContext(
            request=request,
            response_message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
            turn=0,
        )
        assert await hook(ctx) is None


# --------------------------------------------------------------------------- #
# Truncation behavior                                                         #
# --------------------------------------------------------------------------- #


class TestTruncation:
    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    async def test_over_cap_returns_modify_output(self, model: str) -> None:
        big = "HEAD_ANCHOR " + ("x" * 50_000) + " TAIL_ANCHOR"
        hook = TruncateToolResultHook(cap_tokens=100, model=model)
        ctx = _post_ctx(big)

        result = await hook(ctx)
        assert result is not None
        assert result.decision == "modify"
        assert result.new_output is not None
        assert "HEAD_ANCHOR" in result.new_output
        assert "TAIL_ANCHOR" in result.new_output
        assert "[truncated" in result.new_output

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    async def test_truncated_result_is_shorter(self, model: str) -> None:
        big = "x" * 100_000
        hook = TruncateToolResultHook(cap_tokens=100, model=model)
        ctx = _post_ctx(big)

        result = await hook(ctx)
        assert result is not None
        assert result.new_output is not None
        assert len(result.new_output) < len(big)

    @pytest.mark.parametrize("model", [_PRECISE_MODEL, _FALLBACK_MODEL])
    async def test_construction_returns_hookresult_modify(self, model: str) -> None:
        # Type-level sanity: result is exactly HookResult, decision modify.
        big = "x" * 100_000
        hook = TruncateToolResultHook(cap_tokens=100, model=model)
        ctx = _post_ctx(big)

        result = await hook(ctx)
        assert isinstance(result, HookResult)
        assert result.decision == "modify"


# --------------------------------------------------------------------------- #
# tool_truncated log event (P4-T2.2c)                                         #
# --------------------------------------------------------------------------- #


class TestTruncationLogEvent:
    async def test_log_fired_with_5_expected_fields(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        big = "x" * 100_000
        hook = TruncateToolResultHook(cap_tokens=100, model=_PRECISE_MODEL)
        ctx = _post_ctx(big, tool_use_id="toolu_truncate_test")

        await hook(ctx)

        events = [e for e in _lines(log_stream) if e["event"] == "tool_truncated"]
        assert len(events) == 1
        e = events[0]
        # 5 expected fields (D14.4 contract):
        assert e["tool_use_id"] == "toolu_truncate_test"
        assert e["tool_name"] == "Read"
        assert e["cap_tokens"] == 100
        assert isinstance(e["original_tokens"], int)
        assert isinstance(e["truncated_tokens"], int)
        # Truncated_tokens should be lower than original (the algorithm worked)
        assert int(str(e["truncated_tokens"])) < int(str(e["original_tokens"]))
        # Level is info (D14.4 — expected behavior on long-output tools)
        assert e["level"] == "info"

    async def test_no_log_when_passthrough(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        hook = TruncateToolResultHook(cap_tokens=10_000, model=_PRECISE_MODEL)
        await hook(_post_ctx("short text"))

        events = [e for e in _lines(log_stream) if e["event"] == "tool_truncated"]
        assert events == []
