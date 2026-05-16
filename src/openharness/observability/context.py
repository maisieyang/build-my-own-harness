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

    P6-T4 (D16.7) — **nested invocation detection**. If a ``run_id`` is
    already bound when ``bind_run`` enters(typical case:sub-agent's
    ``run_query`` re-enters ``bind_run`` while parent's ``run_id`` is
    still active), the existing value is stashed as ``parent_run_id`` in
    the bound context. On exit, ``parent_run_id`` unbinds and the outer
    ``run_id`` remains for the parent. Trace stitching is a self-join
    on ``run_id ↔ parent_run_id``.
    """
    rid = run_id or new_run_id()
    # Detect nested invocation:if run_id is already bound by an outer
    # ``bind_run``,stash it as parent_run_id so sub-agent log events
    # carry the trace-stitching pointer.
    existing = structlog.contextvars.get_contextvars()
    parent_rid = existing.get("run_id")
    bind_kwargs: dict[str, object] = {"run_id": rid}
    if parent_rid is not None:
        bind_kwargs["parent_run_id"] = parent_rid
    structlog.contextvars.bind_contextvars(**bind_kwargs)
    try:
        yield rid
    finally:
        # ``structlog.contextvars`` has set/unset semantics, NOT stack —
        # ``unbind_contextvars("run_id")`` would erase the binding instead
        # of restoring the outer ``bind_run``'s value. Restore explicitly:
        if parent_rid is not None:
            structlog.contextvars.bind_contextvars(run_id=parent_rid)
            structlog.contextvars.unbind_contextvars("parent_run_id")
        else:
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


@contextmanager
def bind_agent_depth(agent_depth: int) -> Iterator[int]:
    """Bind ``agent_depth`` for the duration of a ``run_query`` invocation.

    P6-T4(D16.7):top-level ``oh ask`` runs at depth 0;each
    :class:`SpawnAgent` invocation enters a fresh ``run_query`` with
    ``context.agent_depth = parent.agent_depth + 1``. Every log event
    emitted inside that ``run_query``(``turn_start`` / ``tool_dispatch``
    / ``tool_complete`` / etc.)carries the bound depth.

    Decoupled from :func:`bind_run` per the boundary doc Tentative (a):
    keeps ``bind_run`` agnostic of ``QueryContext`` so structlog stays
    a thin layer over contextvars.

    Typical usage in ``run_query``::

        with bind_run(), bind_agent_depth(context.agent_depth):
            for turn in range(max_turns):
                with bind_turn(turn + 1):
                    logger.info("turn_start", ...)  # carries agent_depth
    """
    structlog.contextvars.bind_contextvars(agent_depth=agent_depth)
    try:
        yield agent_depth
    finally:
        structlog.contextvars.unbind_contextvars("agent_depth")
