"""Tests for run_query — P2-T1 sub-unit 1c (typed stub).

The body lands in P2-T4. This file locks the public *contract*:

1. ``run_query`` is an async-generator function — callers iterate it with
   ``async for`` (not ``await``).
2. The stub raises :class:`NotImplementedError` on the first iteration with
   a marker pointing at the task that fills the body.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from openharness.api import OpenAICompatibleApiClient
from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.tools import ToolRegistry


def _make_context() -> QueryContext:
    return QueryContext(
        api_client=cast("OpenAICompatibleApiClient", Mock(spec=OpenAICompatibleApiClient)),
        tool_registry=ToolRegistry(),
        permission_checker=object(),
        system_prompt="",
        cwd=Path("/tmp"),
    )


class TestRunQueryStub:
    def test_run_query_is_async_generator_function(self) -> None:
        # Static check: the contract that callers may `async for event in run_query(...)`
        # is structural — verify it on the function itself, not an instance.
        assert inspect.isasyncgenfunction(run_query)

    async def test_first_iteration_raises_not_implemented_with_p2t4_marker(
        self,
    ) -> None:
        gen = run_query([], _make_context())
        with pytest.raises(NotImplementedError, match="P2-T4"):
            await anext(gen)
