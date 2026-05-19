"""Integration tests across the engine package — P2-T1 sub-unit 1d
(extended in P2-T4.4d).

Verifies the public API contract:

1. ``QueryContext`` and ``run_query`` resolve via ``openharness.engine`` directly
   (no need for callers to know about submodule layout).
2. They compose end-to-end via the public path: build a context, call
   run_query, iterate it, observe a clean exit on a no-tool turn.

Helpers in :mod:`openharness.engine.messages` are intentionally **not**
re-exported at the package root — they are implementation detail consumed
by ``run_query``. Callers needing them import from the submodule.
"""

from __future__ import annotations

from pathlib import Path

from engine.conftest import _AllowAllChecker, _StubApiClient
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.tools import ToolRegistry


def test_public_api_reachable_from_package_root() -> None:
    from openharness.engine import QueryContext, run_query

    # Sanity: each symbol resolved (no AttributeError above).
    assert QueryContext is not None
    assert run_query is not None


def test_messages_helpers_are_not_re_exported_at_package_root() -> None:
    """Helpers stay submodule-private. If a caller wants them they import
    from ``openharness.engine.messages`` explicitly."""
    import openharness.engine as engine_pkg

    for helper in (
        "append_user_text",
        "append_assistant_message",
        "append_tool_results",
        "extract_tool_uses",
    ):
        assert not hasattr(engine_pkg, helper), f"{helper!r} unexpectedly promoted to package root"


async def test_query_context_and_run_query_compose_via_public_path() -> None:
    """End-to-end shape via public imports — locks the surface in place."""
    from openharness.engine import QueryContext, run_query

    end_turn = ApiMessageCompleteEvent.model_validate(
        {
            "message": {"role": "assistant", "content": []},
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "end_turn",
        }
    )
    client = _StubApiClient(events_per_turn=[[end_turn]])

    ctx = QueryContext(
        api_client=client,
        tool_registry=ToolRegistry(),
        permission_checker=_AllowAllChecker(),
        system_prompt="",
        cwd=Path("/tmp"),
        model="qwen-plus",
    )

    from openharness.protocols import ConversationCompleteEvent

    collected = [
        event
        async for event in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            ctx,
        )
    ]
    # P6+-T1: one API event + final ConversationCompleteEvent.
    assert len(collected) == 2
    assert isinstance(collected[0], ApiMessageCompleteEvent)
    assert isinstance(collected[1], ConversationCompleteEvent)
