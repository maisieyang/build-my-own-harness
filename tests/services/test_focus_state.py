"""Tests for ``services/focus_state.py`` — P13-T3 (D31.7).

LLM-authored ``task_focus_state``. The 7th consumer of the
``services/summarize.py`` substrate (verified by P13-T4 invariant
test: summarize.py must not be modified to support focus-state).

Surfaces:

1. ``FocusState`` dataclass + ``empty()`` factory + ``to_dict()``
2. Prompt template integrity (FOCUS_STATE_SYSTEM_PROMPT)
3. ``infer_focus_state`` happy path — valid LLM JSON
4. Failure isolation: malformed JSON / timeout / API exception
   all return ``FocusState.empty()`` + warn log
5. Skip when ``messages`` < 2 (nothing to infer from)
6. JSON-with-markdown-fence tolerance
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.services.focus_state import (
    FOCUS_STATE_SYSTEM_PROMPT,
    FocusState,
    _parse_focus_state_response,
    infer_focus_state,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# --------------------------------------------------------------------------- #
# Stubs                                                                       #
# --------------------------------------------------------------------------- #


class _ResponseStub:
    """Stub api_client returning a canned text response per call."""

    def __init__(self, *, response: str) -> None:
        self._response = response
        self.calls = 0

    async def stream_message(self, request: object) -> AsyncIterator[object]:
        del request
        self.calls += 1
        from openharness.protocols.stream_events import (
            ApiMessageCompleteEvent,
            ApiTextDeltaEvent,
        )
        from openharness.protocols.usage import UsageSnapshot

        yield ApiTextDeltaEvent(text=self._response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._response)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class _RaisingStub:
    """Stub api_client whose stream_message always raises."""

    def __init__(self, *, exc: Exception) -> None:
        self._exc = exc

    async def stream_message(self, request: object) -> AsyncIterator[object]:
        del request
        if False:  # pragma: no cover
            yield
        raise self._exc


def _two_messages() -> list[ConversationMessage]:
    return [
        ConversationMessage(role="user", content=[TextBlock(text="fix the failing test")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="I'll look at it")]),
    ]


# --------------------------------------------------------------------------- #
# FocusState dataclass                                                        #
# --------------------------------------------------------------------------- #


class TestFocusStateDataclass:
    def test_empty_factory(self) -> None:
        empty = FocusState.empty()
        assert empty.goal is None
        assert empty.next_step is None

    def test_to_dict_shape(self) -> None:
        fs = FocusState(goal="g", next_step="n")
        assert fs.to_dict() == {"goal": "g", "next_step": "n"}

    def test_to_dict_preserves_none(self) -> None:
        fs = FocusState(goal=None, next_step="something")
        assert fs.to_dict() == {"goal": None, "next_step": "something"}

    def test_frozen_dataclass(self) -> None:
        fs = FocusState(goal="g", next_step="n")
        with pytest.raises(Exception, match=r"cannot assign|frozen"):
            fs.goal = "different"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# FOCUS_STATE_SYSTEM_PROMPT integrity                                         #
# --------------------------------------------------------------------------- #


class TestPromptIntegrity:
    def test_prompt_demands_json_only(self) -> None:
        assert "JSON" in FOCUS_STATE_SYSTEM_PROMPT
        assert "goal" in FOCUS_STATE_SYSTEM_PROMPT
        assert "next_step" in FOCUS_STATE_SYSTEM_PROMPT

    def test_prompt_provides_example(self) -> None:
        # The prompt includes an example output — helps the LLM
        # follow the format. Loss of this would make malformed
        # responses more common.
        assert "Example" in FOCUS_STATE_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# _parse_focus_state_response                                                 #
# --------------------------------------------------------------------------- #


class TestParseFocusStateResponse:
    def test_valid_json_parses(self) -> None:
        raw = json.dumps({"goal": "fix bug", "next_step": "rerun tests"})
        fs = _parse_focus_state_response(raw)
        assert fs.goal == "fix bug"
        assert fs.next_step == "rerun tests"

    def test_markdown_fence_stripped(self) -> None:
        # LLM may wrap output in ```json ... ``` despite the prompt.
        # Parser strips fences.
        raw = '```json\n{"goal": "g", "next_step": "n"}\n```'
        fs = _parse_focus_state_response(raw)
        assert fs.goal == "g"
        assert fs.next_step == "n"

    def test_plain_fence_stripped(self) -> None:
        raw = '```\n{"goal": "g", "next_step": "n"}\n```'
        fs = _parse_focus_state_response(raw)
        assert fs.goal == "g"
        assert fs.next_step == "n"

    def test_malformed_json_returns_empty(self) -> None:
        fs = _parse_focus_state_response("not a json {{{")
        assert fs == FocusState.empty()

    def test_root_not_a_dict_returns_empty(self) -> None:
        fs = _parse_focus_state_response('["list", "not", "dict"]')
        assert fs == FocusState.empty()

    def test_non_string_goal_becomes_none(self) -> None:
        raw = json.dumps({"goal": 42, "next_step": "ok"})
        fs = _parse_focus_state_response(raw)
        assert fs.goal is None
        assert fs.next_step == "ok"

    def test_missing_fields_become_none(self) -> None:
        fs = _parse_focus_state_response(json.dumps({}))
        assert fs == FocusState.empty()

    def test_whitespace_around_json_ok(self) -> None:
        raw = '\n\n  {"goal": "g", "next_step": "n"}  \n'
        fs = _parse_focus_state_response(raw)
        assert fs.goal == "g"


# --------------------------------------------------------------------------- #
# infer_focus_state                                                           #
# --------------------------------------------------------------------------- #


class TestInferFocusStateHappyPath:
    @pytest.mark.asyncio
    async def test_returns_authored_state(self) -> None:
        stub = _ResponseStub(response=json.dumps({"goal": "fix bug X", "next_step": "rerun tests"}))
        fs = await infer_focus_state(
            messages=_two_messages(),
            api_client=stub,  # type: ignore[arg-type]
            model="qwen-plus",
        )
        assert fs.goal == "fix bug X"
        assert fs.next_step == "rerun tests"
        assert stub.calls == 1

    @pytest.mark.asyncio
    async def test_prior_focus_state_included_in_prompt(self) -> None:
        # Spy on the request to verify prior_focus_state flows into
        # the user message. Use a recording stub.

        class _RecordingStub:
            def __init__(self) -> None:
                self.last_messages: list[ConversationMessage] | None = None

            async def stream_message(self, request: object) -> AsyncIterator[object]:
                # request.messages contains the user message we built
                self.last_messages = request.messages  # type: ignore[attr-defined]
                from openharness.protocols.stream_events import (
                    ApiMessageCompleteEvent,
                    ApiTextDeltaEvent,
                )
                from openharness.protocols.usage import UsageSnapshot

                response = json.dumps({"goal": "g", "next_step": "n"})
                yield ApiTextDeltaEvent(text=response)
                yield ApiMessageCompleteEvent(
                    message=ConversationMessage(
                        role="assistant", content=[TextBlock(text=response)]
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                    stop_reason="end_turn",
                )

        stub = _RecordingStub()
        prior = FocusState(goal="old goal", next_step="old step")
        await infer_focus_state(
            messages=_two_messages(),
            api_client=stub,  # type: ignore[arg-type]
            model="qwen-plus",
            prior_focus_state=prior,
        )
        assert stub.last_messages is not None
        prompt_text = stub.last_messages[0].content[0].text  # type: ignore[attr-defined,union-attr]
        assert "old goal" in prompt_text
        assert "old step" in prompt_text


class TestInferFocusStateFailureIsolation:
    @pytest.mark.asyncio
    async def test_llm_exception_returns_empty(self) -> None:
        stub = _RaisingStub(exc=RuntimeError("api unreachable"))
        fs = await infer_focus_state(
            messages=_two_messages(),
            api_client=stub,  # type: ignore[arg-type]
            model="qwen-plus",
        )
        assert fs == FocusState.empty()

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty(self) -> None:
        stub = _ResponseStub(response="not even json")
        fs = await infer_focus_state(
            messages=_two_messages(),
            api_client=stub,  # type: ignore[arg-type]
            model="qwen-plus",
        )
        assert fs == FocusState.empty()

    @pytest.mark.asyncio
    async def test_skip_when_fewer_than_2_messages(self) -> None:
        # No LLM call should fire for trivial conversations
        stub = _ResponseStub(response=json.dumps({"goal": "g", "next_step": "n"}))
        fs = await infer_focus_state(
            messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            api_client=stub,  # type: ignore[arg-type]
            model="qwen-plus",
        )
        assert fs == FocusState.empty()
        assert stub.calls == 0

    @pytest.mark.asyncio
    async def test_empty_messages_skip(self) -> None:
        stub = _ResponseStub(response=json.dumps({"goal": "g", "next_step": "n"}))
        fs = await infer_focus_state(
            messages=[],
            api_client=stub,  # type: ignore[arg-type]
            model="qwen-plus",
        )
        assert fs == FocusState.empty()
        assert stub.calls == 0


class TestInferFocusStateMessageRendering:
    @pytest.mark.asyncio
    async def test_tool_use_block_rendered_compactly(self) -> None:
        # tool_use blocks render as "[tool] <name>" in the prompt
        class _RecordingStub:
            def __init__(self) -> None:
                self.last_text: str | None = None

            async def stream_message(self, request: object) -> AsyncIterator[object]:
                msgs = request.messages  # type: ignore[attr-defined]
                self.last_text = msgs[0].content[0].text
                from openharness.protocols.stream_events import (
                    ApiMessageCompleteEvent,
                    ApiTextDeltaEvent,
                )
                from openharness.protocols.usage import UsageSnapshot

                yield ApiTextDeltaEvent(text="{}")
                yield ApiMessageCompleteEvent(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="{}")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                    stop_reason="end_turn",
                )

        stub = _RecordingStub()
        messages = [
            ConversationMessage(role="user", content=[TextBlock(text="read x")]),
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="Read", input={"path": "/x"})],
            ),
            ConversationMessage(
                role="user", content=[ToolResultBlock(tool_use_id="t1", content="ok")]
            ),
        ]
        await infer_focus_state(
            messages=messages,
            api_client=stub,  # type: ignore[arg-type]
            model="qwen-plus",
        )
        assert stub.last_text is not None
        assert "[tool] Read" in stub.last_text
        assert "[result] ok" in stub.last_text
