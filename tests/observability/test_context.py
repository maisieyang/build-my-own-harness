"""Tests for ``bind_run`` / ``bind_turn`` / ``new_run_id`` — P3-T5.5a.

These bindings are the "穷人版 trace 三件套" foundation: every log call
inside a ``bind_run`` block carries ``run_id``, every nested ``bind_turn``
adds ``turn_id``. Future 5c/5d log calls won't have to pass these by hand.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import TYPE_CHECKING

import pytest

from openharness.observability import (
    bind_run,
    bind_turn,
    configure_logging,
    get_logger,
    new_run_id,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    """Split + parse every JSONL line in the stream."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class TestNewRunId:
    def test_returns_12_char_hex(self) -> None:
        rid = new_run_id()
        assert len(rid) == 12
        # Hex characters only.
        int(rid, 16)

    def test_unique_per_call(self) -> None:
        ids = {new_run_id() for _ in range(100)}
        assert len(ids) == 100


class TestBindRun:
    def test_injects_run_id_into_log_within_block(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        with bind_run("abc123") as rid:
            assert rid == "abc123"
            log.info("inside")

        record = _lines(stream)[0]
        assert record["run_id"] == "abc123"

    def test_auto_mints_run_id_when_none(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        with bind_run() as rid:
            log.info("inside")

        assert len(rid) == 12
        record = _lines(stream)[0]
        assert record["run_id"] == rid

    def test_unbinds_on_exit(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        with bind_run("abc123"):
            pass
        log.info("outside")

        record = _lines(stream)[0]
        assert "run_id" not in record

    def test_unbinds_on_exception(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        with pytest.raises(RuntimeError), bind_run("abc123"):
            raise RuntimeError("boom")
        log.info("after_exception")

        # Even though the with block raised, run_id must be cleaned up.
        record = _lines(stream)[0]
        assert "run_id" not in record


class TestBindTurn:
    def test_nested_in_run_carries_both_ids(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        with bind_run("abc123"), bind_turn(2):
            log.info("nested")

        record = _lines(stream)[0]
        assert record["run_id"] == "abc123"
        assert record["turn_id"] == 2

    def test_unbinds_turn_independently_of_run(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        with bind_run("abc123"):
            with bind_turn(1):
                log.info("turn_1")
            log.info("between_turns")
            with bind_turn(2):
                log.info("turn_2")

        records = _lines(stream)
        assert records[0]["turn_id"] == 1
        assert "turn_id" not in records[1]  # turn unbound between blocks
        assert records[1]["run_id"] == "abc123"  # but run_id still bound
        assert records[2]["turn_id"] == 2


class TestContextvarIsolation:
    async def test_concurrent_tasks_see_independent_run_ids(
        self,
        stream: io.StringIO,
    ) -> None:
        """ContextVars are task-local under asyncio — concurrent tools /
        sub-agents in Phase 6 must not see each other's run_ids."""
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        async def task(rid: str) -> None:
            with bind_run(rid):
                await asyncio.sleep(0)  # yield to peer
                log.info("from_task", rid_passed=rid)

        await asyncio.gather(task("aaa111111111"), task("bbb222222222"))

        records = _lines(stream)
        # Each log line's run_id must equal the rid that the task was
        # given — proving the contextvar didn't bleed across tasks.
        for record in records:
            assert record["run_id"] == record["rid_passed"]


class TestBindRunNesting:
    """P6-T4 (D16.7) — nested ``bind_run`` invocations stash the parent's
    ``run_id`` as ``parent_run_id`` so sub-agent log events carry the
    trace-stitching pointer.
    """

    def test_top_level_has_no_parent_run_id(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        with bind_run("R1"):
            log.info("top")
        lines = _lines(stream)
        assert lines[0]["run_id"] == "R1"
        assert "parent_run_id" not in lines[0]

    def test_nested_run_attaches_parent_run_id(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        with bind_run("R1"):
            log.info("parent")
            with bind_run("R2"):
                log.info("child")
            log.info("parent_again")
        lines = _lines(stream)
        assert lines[0] == {**lines[0], "run_id": "R1"}
        assert "parent_run_id" not in lines[0]
        # Sub-agent's event has both R2 and R1.
        assert lines[1]["run_id"] == "R2"
        assert lines[1]["parent_run_id"] == "R1"
        # After child exits, parent is restored cleanly (no parent_run_id).
        assert lines[2]["run_id"] == "R1"
        assert "parent_run_id" not in lines[2]

    def test_three_level_nesting_immediate_parent_only(self, stream: io.StringIO) -> None:
        # Per the boundary doc:depth=2 sub-agent's parent_run_id is its
        # IMMEDIATE parent (R2), not the grandparent (R1). Trace stitching
        # uses self-join on run_id ↔ parent_run_id to walk the chain.
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        with bind_run("R1"), bind_run("R2"), bind_run("R3"):
            log.info("deepest")
        lines = _lines(stream)
        assert lines[0]["run_id"] == "R3"
        assert lines[0]["parent_run_id"] == "R2"


class TestBindAgentDepth:
    """P6-T4 — ``agent_depth`` binding surfaces on every log event inside
    a ``run_query`` invocation. Top-level is 0;sub-agent is 1, 2, etc.
    """

    def test_binds_depth_zero(self, stream: io.StringIO) -> None:
        from openharness.observability import bind_agent_depth

        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        with bind_agent_depth(0):
            log.info("top")
        lines = _lines(stream)
        assert lines[0]["agent_depth"] == 0

    def test_binds_depth_one(self, stream: io.StringIO) -> None:
        from openharness.observability import bind_agent_depth

        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        with bind_agent_depth(1):
            log.info("child")
        lines = _lines(stream)
        assert lines[0]["agent_depth"] == 1

    def test_unbinds_on_exit(self, stream: io.StringIO) -> None:
        from openharness.observability import bind_agent_depth

        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        with bind_agent_depth(2):
            pass
        log.info("after")
        lines = _lines(stream)
        # Outside the block, depth is gone.
        assert "agent_depth" not in lines[0]

    def test_nested_depth_via_runquery_pattern(self, stream: io.StringIO) -> None:
        # Mirrors how `run_query` will use the helper: combined with bind_run.
        from openharness.observability import bind_agent_depth

        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        with bind_run("R1"), bind_agent_depth(0):
            log.info("parent_turn")
            with bind_run("R2"), bind_agent_depth(1):
                log.info("child_turn")
        lines = _lines(stream)
        assert lines[0]["run_id"] == "R1"
        assert lines[0]["agent_depth"] == 0
        assert lines[1]["run_id"] == "R2"
        assert lines[1]["parent_run_id"] == "R1"
        assert lines[1]["agent_depth"] == 1
