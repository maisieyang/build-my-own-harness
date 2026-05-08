"""Shared test fixtures for the tool layer.

``_FakeTool`` is a minimal :class:`BaseTool` subclass used across P2-T2
sub-units (2b execute round-trip, 2c registry round-trip, 2d schema
generation) and Phase 2's later ``run_query`` tests (P2-T4) once the loop
exists. Promoting it to ``conftest.py`` from the start (per D8.9) saves a
later "extract fixture" step.
"""

from __future__ import annotations

from pydantic import BaseModel

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class FakeInput(BaseModel):
    """Input schema for :class:`_FakeTool`.

    A single ``value`` field is enough to verify Pydantic-driven
    validation flows from the LLM's dict input through to typed args.
    """

    value: str


class _FakeTool(BaseTool[FakeInput]):
    """Test double: returns ``ToolResult(output=f"value={args.value}")``."""

    name = "Fake"
    description = "Fake tool used in tests."
    input_model = FakeInput

    async def execute(
        self,
        args: FakeInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        # ``context`` unused in this fake; real tools (P2-T3) will use cwd.
        del context
        return ToolResult(output=f"value={args.value}")
