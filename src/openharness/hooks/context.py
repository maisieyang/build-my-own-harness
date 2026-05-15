"""HookContext per-event dataclasses + ``Hook`` callable alias — P3-T4.4b.

Each of the 5 ``HookEvent`` values has a typed context dataclass carrying
the event-specific payload. Per P3-T4 Three-Axis A:5 typed dataclasses
(not a union with discriminator) — lets hook authors write precise
signatures and lets the executor (4d) pass exactly the right info per event.

The ``Hook`` callable alias unions all 5 contexts so :class:`HookRegistry`
stores them homogeneously;the executor narrows by event when dispatching.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from openharness.hooks.result import HookResult
    from openharness.protocols import ApiMessageRequest, ConversationMessage, UsageSnapshot
    from openharness.tools.base import ToolExecutionContext, ToolResult


@dataclass(frozen=True)
class PreToolUseContext:
    """Payload for hooks running **before** ``tool.execute()``.

    ``tool_input`` is the raw dict from the LLM (already Pydantic-validated
    by the dispatcher);hooks may return ``HookResult.modify_input(...)``
    with a replacement dict — the dispatcher re-validates after the chain
    finishes.

    P4-T2:``tool_use_id`` lets observability hooks correlate Pre/Post
    pairs and tie them back to the originating ``ToolUseBlock``;cross-
    event log filtering depends on it.
    """

    tool_name: str
    tool_use_id: str
    tool_input: dict[str, Any]
    exec_context: ToolExecutionContext


@dataclass(frozen=True)
class PostToolUseContext:
    """Payload for hooks running **after** ``tool.execute()``.

    ``tool_input`` reflects any PreToolUse modifications;``result`` is
    the actual :class:`ToolResult`. Hooks may return
    ``HookResult.modify_output(...)`` to replace what the LLM sees.

    P4-T2:``tool_use_id`` mirrors :class:`PreToolUseContext`'s field so
    observability hooks can pair Pre/Post observations and so the
    P4 ``TruncateToolResultHook`` can include it in the ``tool_truncated``
    log event.
    """

    tool_name: str
    tool_use_id: str
    tool_input: dict[str, Any]
    exec_context: ToolExecutionContext
    result: ToolResult


@dataclass(frozen=True)
class PreApiCallContext:
    """Payload for hooks running **before** ``client.stream_message()``.

    Hooks may return ``HookResult.modify_request(...)`` to inject memory
    excerpts (Phase 4) / change the model / etc.
    """

    request: ApiMessageRequest
    turn: int


@dataclass(frozen=True)
class PostApiCallContext:
    """Payload for hooks running **after** a complete LLM turn streams.

    Phase 3 PostApiCall is observe-only (cost / usage tracking, logging).
    Modify is reserved for Phase 4+ (e.g., Compaction trimming history).
    """

    request: ApiMessageRequest
    response_message: ConversationMessage
    usage: UsageSnapshot
    stop_reason: str
    turn: int


@dataclass(frozen=True)
class OnErrorContext:
    """Payload for hooks running when a dispatch-pipeline exception fires.

    ``where`` narrows the origin:

    - ``"hook"``  — another hook raised an exception
    - ``"tool"``  — ``tool.execute()`` raised an exception
    - ``"api"``   — ``client.stream_message()`` raised
    - ``"loop"``  — loop-control error (rare)

    Phase 3 OnError is observe-only — the exception propagates regardless
    of what hooks do. Phase 4+ may add suppress semantics.
    """

    exception: Exception
    where: Literal["api", "tool", "hook", "loop"]
    tool_name: str | None = None


# Union of all 5 context types — Hook signature accepts any one.
HookContext = (
    PreToolUseContext | PostToolUseContext | PreApiCallContext | PostApiCallContext | OnErrorContext
)


# Hooks are async callables;sync hooks are NOT supported (per Three-Axis D)
# because the dispatch loop is async and sync hooks would block the event loop.
# Users with sync work should wrap with ``asyncio.to_thread`` inside their hook.
Hook = Callable[[HookContext], Awaitable["HookResult | None"]]
