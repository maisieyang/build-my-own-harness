"""Tests for the L5 goal decomposer — ``services/decompose.py``.

One-shot LLM call (reusing ``summarize()``, same shape as the L3'
semantic judge) that turns a big goal into an ordered array of sub-goal
strings. Fail-closed: any judge-call/parse/schema failure is
``ok=False`` -- never falls back to silently running the original goal
undecomposed, since that would be executing something different from
what the user asked for while looking like decomposition succeeded.
"""

from __future__ import annotations

import dataclasses as _dc
from typing import TYPE_CHECKING

import pytest

from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.services.decompose import DecomposeResult, decompose_goal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


class _DecomposerStubClient:
    """Yields a single canned decomposer response. Records the last request sent."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.last_request: ApiMessageRequest | None = None

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        yield ApiTextDeltaEvent(text=self._response_text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant", content=[TextBlock(text=self._response_text)]
            ),
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )


class _RaisingStubClient:
    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        raise RuntimeError("decomposer call blew up")
        yield  # pragma: no cover - unreachable, satisfies generator typing


class TestDecomposeGoalHappyPath:
    async def test_valid_json_array_succeeds(self) -> None:
        stub = _DecomposerStubClient('["set up the data model", "add the API endpoint"]')
        result = await decompose_goal("build the feature", api_client=stub, model="fake-model")
        assert isinstance(result, DecomposeResult)
        assert result.ok is True
        assert result.sub_goals == ("set up the data model", "add the API endpoint")

    async def test_markdown_fence_is_stripped(self) -> None:
        stub = _DecomposerStubClient('```json\n["step one", "step two"]\n```')
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is True
        assert result.sub_goals == ("step one", "step two")

    async def test_unclosed_markdown_fence_still_parses(self) -> None:
        """Review fix: a truncated response (opening fence, no closing fence)
        must not have its last content line silently discarded."""
        stub = _DecomposerStubClient('```json\n["step one", "step two"]')
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is True
        assert result.sub_goals == ("step one", "step two")

    async def test_goal_reaches_the_decomposer_prompt(self) -> None:
        stub = _DecomposerStubClient('["a"]')
        await decompose_goal("UNIQUE_GOAL_MARKER", api_client=stub, model="fake-model")
        assert stub.last_request is not None
        sent = stub.last_request.messages[-1].content[0].text
        assert "UNIQUE_GOAL_MARKER" in sent


class TestDecomposeGoalFailClosed:
    async def test_call_exception_fails_closed(self) -> None:
        stub = _RaisingStubClient()
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False
        assert result.sub_goals == ()
        assert "decomposer call blew up" in result.feedback or "RuntimeError" in result.feedback

    async def test_empty_response_fails_closed(self) -> None:
        stub = _DecomposerStubClient("")
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False

    async def test_malformed_json_fails_closed(self) -> None:
        stub = _DecomposerStubClient("this is not json at all")
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False

    async def test_non_array_json_fails_closed(self) -> None:
        stub = _DecomposerStubClient('{"score": 1}')
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False

    async def test_empty_array_fails_closed(self) -> None:
        stub = _DecomposerStubClient("[]")
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False

    async def test_too_many_sub_goals_fails_closed(self) -> None:
        stub = _DecomposerStubClient(str([f"step {i}" for i in range(9)]).replace("'", '"'))
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False

    async def test_non_string_element_fails_closed(self) -> None:
        stub = _DecomposerStubClient('["step one", 42, "step three"]')
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False

    async def test_empty_string_element_fails_closed(self) -> None:
        stub = _DecomposerStubClient('["", "do the actual work"]')
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False

    async def test_whitespace_only_element_fails_closed(self) -> None:
        stub = _DecomposerStubClient('["   ", "do the actual work"]')
        result = await decompose_goal("goal", api_client=stub, model="fake-model")
        assert result.ok is False


class TestDecomposeResult:
    def test_is_frozen(self) -> None:
        result = DecomposeResult(ok=True, sub_goals=("a",), feedback="ok")
        with pytest.raises(_dc.FrozenInstanceError):
            result.ok = False  # type: ignore[misc]
