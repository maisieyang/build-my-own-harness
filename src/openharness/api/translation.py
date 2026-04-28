"""Wire-format translation between Anthropic-shape (our ``protocols/``) and
OpenAI-shape (Qwen via DashScope / OpenAI cloud / DeepSeek / Moonshot / etc.).

Two main responsibilities:

1. :func:`to_openai_request` -- convert :class:`ApiMessageRequest` (Anthropic-
   shape) into the dict shape that
   ``openai.AsyncOpenAI().chat.completions.create()`` expects as kwargs.

2. :class:`_StreamAssembler` -- consume OpenAI streaming chunks
   (``chat.completion.chunk`` shape), yield :class:`ApiTextDeltaEvent`s as
   text deltas arrive, and produce a final :class:`ApiMessageCompleteEvent`
   on :meth:`finalize`.

The translation handles 7 wire-format mismatches (see
``decisions/03-api-client-strategy.md``):

- text content (string vs list-of-blocks)
- multi-block text (list-form on both)
- image (different shape: source/data vs image_url/url)
- ToolSpec wrapping (function envelope on OpenAI)
- assistant tool_use (inline content vs top-level ``tool_calls``)
- user tool_result (inline content vs separate ``role: "tool"`` message)
- ``stop_reason`` value mapping (Anthropic 4 values vs OpenAI 4 values)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from openharness.protocols.content import (
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from openharness.protocols.content import ContentBlock
    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.tools import ToolSpec


StopReasonAnthropic = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"]


# ============================================================================
# to_openai_request: Anthropic-shape  →  OpenAI-shape kwargs
# ============================================================================


def to_openai_request(req: ApiMessageRequest) -> dict[str, Any]:
    """Convert an :class:`ApiMessageRequest` to OpenAI ``chat.completions`` kwargs.

    The returned dict is ready to be passed as ``**kwargs`` to
    ``client.chat.completions.create(...)``. None values and absent fields
    follow OpenAI's expected omission rules (e.g., no ``tools`` field when
    request has no tools).
    """
    result: dict[str, Any] = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "stream": req.stream,
    }

    messages: list[dict[str, Any]] = []
    if req.system is not None:
        # Anthropic top-level system field becomes OpenAI role:system message.
        messages.append({"role": "system", "content": req.system})

    for msg in req.messages:
        messages.extend(_translate_message(msg))

    result["messages"] = messages

    if req.tools is not None:
        result["tools"] = [_tool_spec_to_openai(t) for t in req.tools]

    return result


def _translate_message(msg: ConversationMessage) -> list[dict[str, Any]]:
    """Translate one :class:`ConversationMessage` to one or more OpenAI messages.

    Most messages produce one OpenAI message. The exception: a user message
    containing :class:`ToolResultBlock`s expands to N separate
    ``{"role": "tool"}`` messages.
    """
    if msg.role == "user" and any(isinstance(b, ToolResultBlock) for b in msg.content):
        return _translate_user_with_tool_results(msg)

    if msg.role == "assistant":
        return [_translate_assistant_message(msg)]

    return [_translate_plain_user_message(msg)]


def _translate_plain_user_message(msg: ConversationMessage) -> dict[str, Any]:
    """User message with text/image content (no tool results)."""
    return {
        "role": msg.role,
        "content": _content_blocks_to_openai_content(msg.content),
    }


def _content_blocks_to_openai_content(
    blocks: list[ContentBlock],
) -> str | list[dict[str, Any]]:
    """Convert a list of ContentBlocks to OpenAI content (string or list).

    Compactness rule: a single TextBlock collapses to a plain string;
    anything else (multi-block, or non-text blocks) becomes a list of
    typed objects.
    """
    if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
        return blocks[0].text

    items: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            items.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            items.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (f"data:{block.source.media_type};base64,{block.source.data}"),
                    },
                },
            )
        # ToolUseBlock / ToolResultBlock handled by callers, not here.
    return items


def _translate_assistant_message(msg: ConversationMessage) -> dict[str, Any]:
    """Assistant message: text → ``content``, tool_use → top-level ``tool_calls``."""
    non_tool_blocks: list[ContentBlock] = []
    tool_calls: list[dict[str, Any]] = []

    for block in msg.content:
        if isinstance(block, ToolUseBlock):
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                },
            )
        else:
            non_tool_blocks.append(block)

    result: dict[str, Any] = {"role": "assistant"}

    if non_tool_blocks:
        result["content"] = _content_blocks_to_openai_content(non_tool_blocks)
    else:
        # OpenAI accepts content=None when tool_calls is present.
        result["content"] = None

    if tool_calls:
        result["tool_calls"] = tool_calls

    return result


def _translate_user_with_tool_results(
    msg: ConversationMessage,
) -> list[dict[str, Any]]:
    """User message with tool_results: split into role:tool messages.

    Each ToolResultBlock becomes its own ``{"role": "tool", "tool_call_id":
    ..., "content": ...}`` message. Mixed text content (uncommon but legal)
    becomes an additional role:user message.
    """
    result: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, ToolResultBlock):
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": block.content,
                },
            )
        elif isinstance(block, TextBlock):
            result.append({"role": "user", "content": block.text})
        # ImageBlock / ToolUseBlock would be unusual here; ignore for v0.1.
    return result


def _tool_spec_to_openai(spec: ToolSpec) -> dict[str, Any]:
    """Wrap a :class:`ToolSpec` in OpenAI's function envelope."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


# ============================================================================
# _StreamAssembler: OpenAI streaming chunks  →  ApiStreamEvents + final
# ============================================================================


_FINISH_REASON_MAP: dict[str, StopReasonAnthropic] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "stop_sequence",
    "function_call": "tool_use",  # legacy OpenAI field
}


@dataclass
class _ToolCallState:
    """Accumulator for one streaming tool call across multiple chunks."""

    id: str = ""
    name: str = ""
    arguments: str = ""  # raw JSON string, accumulated incrementally


@dataclass
class _StreamAssembler:
    """Consume OpenAI ``chat.completion.chunk`` dicts, yield text-delta events,
    and build the terminal :class:`ApiMessageCompleteEvent`.

    Usage:

        assembler = _StreamAssembler()
        async for chunk in openai_stream:
            for event in assembler.consume(chunk):
                yield event
        yield assembler.finalize()
    """

    _text_buffer: list[str] = field(default_factory=list)
    _tool_calls: dict[int, _ToolCallState] = field(default_factory=dict)
    _stop_reason: str | None = None
    _input_tokens: int = 0
    _output_tokens: int = 0

    def consume(self, chunk: dict[str, Any]) -> list[ApiTextDeltaEvent]:
        """Process one streaming chunk; return 0 or more text-delta events.

        State (text buffer, tool-call accumulators, stop_reason, usage) is
        updated eagerly during this call -- caller does NOT need to iterate
        the returned list for side effects to occur. Tool-call deltas are
        accumulated into internal state and do not produce per-chunk events;
        the assembled tool_use lives in the :class:`ApiMessageCompleteEvent`
        from :meth:`finalize`.

        Returns ``list`` rather than a generator so a caller that ignores
        the events (e.g., a test that only cares about the final message)
        still gets the side effects.
        """
        events: list[ApiTextDeltaEvent] = []

        self._capture_usage(chunk)

        choices = chunk.get("choices") or []
        if not choices:
            return events

        choice = choices[0]
        delta = choice.get("delta") or {}

        content_delta = delta.get("content")
        if content_delta is not None:
            self._text_buffer.append(content_delta)
            events.append(ApiTextDeltaEvent(text=content_delta))

        tool_calls_delta = delta.get("tool_calls")
        if tool_calls_delta is not None:
            for tc_delta in tool_calls_delta:
                self._absorb_tool_call_delta(tc_delta)

        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            self._stop_reason = finish_reason

        return events

    def _capture_usage(self, chunk: dict[str, Any]) -> None:
        usage = chunk.get("usage")
        if usage:
            self._input_tokens = int(usage.get("prompt_tokens", 0))
            self._output_tokens = int(usage.get("completion_tokens", 0))

    def _absorb_tool_call_delta(self, tc_delta: dict[str, Any]) -> None:
        """Merge a tool-call delta into the per-index accumulator."""
        index = int(tc_delta.get("index", 0))
        state = self._tool_calls.setdefault(index, _ToolCallState())

        if tc_delta.get("id"):
            state.id = str(tc_delta["id"])

        function = tc_delta.get("function") or {}
        if function.get("name"):
            state.name = str(function["name"])
        if function.get("arguments") is not None:
            state.arguments += str(function["arguments"])

    def finalize(self) -> ApiMessageCompleteEvent:
        """Produce the terminal :class:`ApiMessageCompleteEvent`.

        Combines accumulated text + tool-call state into a complete
        :class:`ConversationMessage`, attaches usage, and maps the OpenAI
        ``finish_reason`` to our ``stop_reason``.
        """
        content: list[ContentBlock] = []

        if self._text_buffer:
            content.append(TextBlock(text="".join(self._text_buffer)))

        for index in sorted(self._tool_calls.keys()):
            state = self._tool_calls[index]
            args = json.loads(state.arguments) if state.arguments else {}
            content.append(
                ToolUseBlock(id=state.id, name=state.name, input=args),
            )

        message = ConversationMessage(role="assistant", content=content)
        usage = UsageSnapshot(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )
        stop_reason: StopReasonAnthropic = _FINISH_REASON_MAP.get(
            self._stop_reason or "stop", "end_turn"
        )

        return ApiMessageCompleteEvent(
            message=message,
            usage=usage,
            stop_reason=stop_reason,
        )


__all__ = [
    "to_openai_request",
]
