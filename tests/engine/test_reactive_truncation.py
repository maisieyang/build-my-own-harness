"""Prompt Too Long recovery at the main request boundary.

The provider error is deterministic evidence that the compiled request did
not fit. The engine gets one semantic recompilation attempt. It never deletes
whole Conversation messages or loops through arbitrary Tool pairs.
"""

from __future__ import annotations

import dataclasses
import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from openharness.api.errors import AuthenticationFailure, PromptTooLongFailure
from openharness.engine.context import QueryContext
from openharness.engine.query import run_query
from openharness.observability import configure_logging
from openharness.protocols import ApiMessageCompleteEvent, ConversationMessage, TextBlock
from openharness.tools import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from openharness.api import SupportsStreamingMessages
    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    yield stream
    logging.getLogger().handlers.clear()


def _configure(stream: io.StringIO) -> None:
    configure_logging(level="DEBUG", format="json", stream=stream)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def _messages() -> list[ConversationMessage]:
    return [
        ConversationMessage(role="user", content=[TextBlock(text=f"message-{index}")])
        for index in range(20)
    ]


def _context(client: object) -> QueryContext:
    return QueryContext(
        api_client=cast("SupportsStreamingMessages", client),
        tool_registry=ToolRegistry(),
        system_prompt="system",
        cwd=Path("/tmp"),
        model="qwen-plus",
        max_tokens=64,
    )


class _ProviderStub:
    def __init__(self, *, prompt_too_long_calls: int) -> None:
        self.prompt_too_long_calls = prompt_too_long_calls
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)
        if len(self.requests) <= self.prompt_too_long_calls:
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]
            raise PromptTooLongFailure("context_length_exceeded", status_code=400)
        yield ApiMessageCompleteEvent.model_validate(
            {
                "message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "end_turn",
            }
        )


@pytest.fixture
def forced_recompile(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def _compact(
        messages: list[ConversationMessage], **_kwargs: object
    ) -> tuple[list[ConversationMessage], bool]:
        calls.append(len(messages))
        return [
            ConversationMessage(role="user", content=[TextBlock(text="boundary")]),
            ConversationMessage(role="user", content=[TextBlock(text="semantic summary")]),
            *messages[-2:],
        ], True

    monkeypatch.setattr("openharness.engine.query.compact_for_request_budget", _compact)
    return calls


class TestOneSemanticRecompile:
    async def test_real_recompile_path_summarizes_then_retries_main_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openharness.protocols import ApiTextDeltaEvent
        from openharness.services.compact import compact_for_request_budget as production_recompile

        monkeypatch.setattr(
            "openharness.engine.query.compact_for_request_budget", production_recompile
        )

        class _MainAndSummaryStub:
            def __init__(self) -> None:
                self.requests: list[ApiMessageRequest] = []
                self.main_calls = 0

            async def stream_message(
                self, request: ApiMessageRequest
            ) -> AsyncIterator[ApiStreamEvent]:
                self.requests.append(request)
                if request.tools == []:
                    summary = "<summary>semantic state</summary>"
                    yield ApiTextDeltaEvent(text=summary)
                    yield ApiMessageCompleteEvent.model_validate(
                        {
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "text", "text": summary}],
                            },
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                            "stop_reason": "end_turn",
                        }
                    )
                    return

                self.main_calls += 1
                if self.main_calls == 1:
                    if False:  # pragma: no cover
                        yield  # type: ignore[unreachable]
                    raise PromptTooLongFailure("context_length_exceeded", status_code=400)
                yield ApiMessageCompleteEvent.model_validate(
                    {
                        "message": {"role": "assistant", "content": []},
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "stop_reason": "end_turn",
                    }
                )

        stub = _MainAndSummaryStub()
        async for _ in run_query(_messages(), _context(stub)):
            pass

        assert len(stub.requests) == 3
        assert stub.requests[0].tools is None
        assert stub.requests[1].tools == []
        assert stub.requests[2].tools is None
        assert len(stub.requests[2].messages) == 14

    async def test_one_ptl_recompiles_once_then_succeeds(
        self, log_stream: io.StringIO, forced_recompile: list[int]
    ) -> None:
        _configure(log_stream)
        stub = _ProviderStub(prompt_too_long_calls=1)

        events = [event async for event in run_query(_messages(), _context(stub))]

        assert forced_recompile == [20]
        assert len(stub.requests) == 2
        assert len(stub.requests[0].messages) == 20
        assert [
            block.text
            for message in stub.requests[1].messages[:2]
            for block in message.content
            if isinstance(block, TextBlock)
        ] == ["boundary", "semantic summary"]
        assert len([event for event in events if isinstance(event, ApiMessageCompleteEvent)]) == 1
        recompiles = [
            event for event in _lines(log_stream) if event["event"] == "prompt_too_long_recompile"
        ]
        assert len(recompiles) == 1
        assert recompiles[0]["attempt"] == 1

    async def test_second_ptl_is_explicit_failure_without_more_deletion(
        self, log_stream: io.StringIO, forced_recompile: list[int]
    ) -> None:
        _configure(log_stream)
        stub = _ProviderStub(prompt_too_long_calls=2)

        with pytest.raises(PromptTooLongFailure):
            async for _ in run_query(_messages(), _context(stub)):
                pass

        assert forced_recompile == [20]
        assert len(stub.requests) == 2
        failures = [
            event
            for event in _lines(log_stream)
            if event["event"] == "prompt_too_long_unrecoverable"
        ]
        assert len(failures) == 1
        assert failures[0]["request_tokens"] > 0
        assert failures[0]["input_budget"] > 0

    async def test_failed_recompile_does_not_retry_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _cannot_compact(
            messages: list[ConversationMessage], **_kwargs: object
        ) -> tuple[list[ConversationMessage], bool]:
            return messages, False

        monkeypatch.setattr("openharness.engine.query.compact_for_request_budget", _cannot_compact)
        stub = _ProviderStub(prompt_too_long_calls=1)

        with pytest.raises(PromptTooLongFailure):
            async for _ in run_query(_messages(), _context(stub)):
                pass

        assert len(stub.requests) == 1

    async def test_recompile_does_not_consume_agent_turn_budget(
        self, forced_recompile: list[int]
    ) -> None:
        stub = _ProviderStub(prompt_too_long_calls=1)
        context = dataclasses.replace(_context(stub), max_turns=1)

        events = [event async for event in run_query(_messages(), context)]

        assert forced_recompile == [20]
        assert len([event for event in events if isinstance(event, ApiMessageCompleteEvent)]) == 1


class TestOtherErrors:
    async def test_authentication_failure_is_not_recompiled(
        self, forced_recompile: list[int]
    ) -> None:
        class _AuthFailureStub:
            async def stream_message(
                self, request: ApiMessageRequest
            ) -> AsyncIterator[ApiStreamEvent]:
                del request
                if False:  # pragma: no cover
                    yield  # type: ignore[unreachable]
                raise AuthenticationFailure("invalid key", status_code=401)

        with pytest.raises(AuthenticationFailure):
            async for _ in run_query(_messages(), _context(_AuthFailureStub())):
                pass

        assert forced_recompile == []
