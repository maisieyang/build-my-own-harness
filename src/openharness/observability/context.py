"""``run_id`` / ``turn_id`` contextvar binders — the "穷人版 trace" 三件套.

Per the P3-T5 第一性原理 discussion: every log point should carry three
IDs so an event stream can be reconstructed into a tree:

::

    trace_id  (= run_id)    — one per ``oh ask`` invocation
    span_id   (= turn_id)   — one per LLM turn (1, 2, 3...)
    nested    (= tool_use_id) — one per tool dispatch (lives on ``ToolUseBlock``,
                                passed at the log call site, not bound here)

This module provides binders for the two **ambient** IDs that should be
visible to every log call in scope without being threaded through every
function signature. ``tool_use_id`` is local to dispatch and travels with
the ``ToolUseBlock`` already — log call sites pass it directly.

structlog's ``contextvars.bind_contextvars`` rides on Python's
``contextvars.ContextVar`` so bindings are **task-local** under asyncio
(a child task inherits the parent's bindings at creation time and won't
leak back). This makes the binders safe to use in concurrent tools / runs.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator


def new_run_id() -> str:
    """Mint a fresh 12-char hex run_id (truncated UUID4).

    Short enough to scan in console output, long enough to be effectively
    unique across a session. Full UUID is overkill for trace correlation
    in a single-user CLI.
    """
    return uuid.uuid4().hex[:12]


@contextmanager
def bind_run(run_id: str | None = None) -> Iterator[str]:
    """Bind ``run_id`` to the current task context for the duration of
    the ``with`` block.

    Yields the bound id so callers can include it in user-facing output
    (e.g., echo the run_id at start so users can grep logs for it).

    ::

        with bind_run() as rid:
            logger.info("turn_start", turn=1)  # auto-injects run_id=rid
    """
    rid = run_id or new_run_id()
    structlog.contextvars.bind_contextvars(run_id=rid)
    try:
        yield rid
    finally:
        structlog.contextvars.unbind_contextvars("run_id")


@contextmanager
def bind_turn(turn_id: int) -> Iterator[int]:
    """Bind ``turn_id`` for the duration of one LLM turn within a run.

    Typical usage in ``run_query``::

        for turn in range(max_turns):
            with bind_turn(turn):
                # every log inside this block carries turn_id=turn
                ...
    """
    structlog.contextvars.bind_contextvars(turn_id=turn_id)
    try:
        yield turn_id
    finally:
        structlog.contextvars.unbind_contextvars("turn_id")
