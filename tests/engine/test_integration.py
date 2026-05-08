"""Integration tests across the engine package — P2-T1 sub-unit 1d.

Verifies the public API contract:

1. ``QueryContext`` and ``run_query`` resolve via ``openharness.engine`` directly
   (no need for callers to know about submodule layout).
2. They compose end-to-end via the public path: build a context, call
   run_query, iterate once, get the expected NotImplementedError.

Helpers in :mod:`openharness.engine.messages` are intentionally **not**
re-exported at the package root — they are implementation detail consumed
by ``run_query`` (P2-T4). Callers needing them import from the submodule.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from openharness.api import OpenAICompatibleApiClient


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

    ctx = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", Mock(spec=OpenAICompatibleApiClient)),
        tool_registry=object(),
        permission_checker=object(),
        system_prompt="",
        cwd=Path("/tmp"),
    )

    gen = run_query([], ctx)
    with pytest.raises(NotImplementedError, match="P2-T4"):
        await anext(gen)
