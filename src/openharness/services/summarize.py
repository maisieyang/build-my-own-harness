"""The :func:`summarize` primitive — P11-T1.

Shared LLM-dispatch + retry + timeout machinery for the three Phase
11 triggers (semantic compact / extract / `/compact` REPL). Per
:doc:`decisions/26-phase-11-boundary` D29.2: this primitive owns
**only** the LLM call. Callers construct their own ``system_prompt``
and parse their own output (JSON for extract, ``<summary>`` tags for
compact). No ``if trigger == compact:`` branches here.

Two layers nested outside-in:

1. **Timeout** (outermost) — :func:`asyncio.wait_for` enforces
   ``timeout_seconds``;raises :class:`asyncio.TimeoutError` on expiry.
2. **Streaming retry** (innermost) — :class:`OpenHarnessApiError`
   (EXCLUDING :class:`AuthenticationFailure` and
   :class:`PromptTooLongFailure`) retries up to 2 times. Auth never
   retries (waiting won't fix invalid credentials). PTL propagates to the
   request compiler, which alone owns semantic recompilation.

This primitive never deletes input messages. A Prompt Too Long response is
deterministic evidence that the caller must construct a different request;
retrying a mutated slice here could silently discard goals or constraints.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    PromptTooLongFailure,
)
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import ApiTextDeltaEvent

if TYPE_CHECKING:
    from openharness.api.client import SupportsStreamingMessages
    from openharness.protocols.messages import ConversationMessage


# Retry budgets (D29.2). "N retries" means N attempts AFTER the
# initial — so max attempts = N + 1.
_STREAMING_RETRY_ATTEMPTS = 2  # → 3 total streaming attempts


async def summarize(
    *,
    messages: list[ConversationMessage],
    system_prompt: str,
    model: str,
    api_client: SupportsStreamingMessages,
    max_tokens: int = 2048,
    timeout_seconds: float = 25.0,
    tools_disabled: bool = True,
) -> str:
    """Call the LLM with a structured-output ``system_prompt`` and
    return the concatenated text response.

    ``tools_disabled=True`` (default) forwards ``tools=[]`` so the
    provider unambiguously rejects any tool-use attempt in the
    response. ``tools_disabled=False`` forwards ``tools=None`` (field
    omitted) — kept as opt-out for future callers, but Phase 11's
    three triggers all use ``True``.

    The caller's ``messages`` list is **not mutated**.

    Raises :class:`asyncio.TimeoutError` if the LLM call(s) take
    longer than ``timeout_seconds`` total.

    Raises :class:`PromptTooLongFailure` immediately. The request compiler,
    not this low-level LLM primitive, owns semantic recompilation.

    Raises :class:`OpenHarnessApiError` subclasses (other than
    :class:`AuthenticationFailure` which propagates immediately) if
    streaming fails 3 consecutive times.
    """
    return await asyncio.wait_for(
        _summarize_with_streaming_retry(
            messages=list(messages),  # defensive copy — never mutate caller's list
            system_prompt=system_prompt,
            model=model,
            api_client=api_client,
            max_tokens=max_tokens,
            tools_disabled=tools_disabled,
        ),
        timeout=timeout_seconds,
    )


async def _summarize_with_streaming_retry(
    *,
    messages: list[ConversationMessage],
    system_prompt: str,
    model: str,
    api_client: SupportsStreamingMessages,
    max_tokens: int,
    tools_disabled: bool,
) -> str:
    """Streaming retry loop. Catches transient API errors only.

    Explicitly does NOT catch :class:`AuthenticationFailure` or
    :class:`PromptTooLongFailure`: neither becomes valid by retrying the same
    request.
    """
    last_exc: OpenHarnessApiError | None = None
    for _ in range(_STREAMING_RETRY_ATTEMPTS + 1):  # +1 for initial
        try:
            return await _stream_and_collect(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                api_client=api_client,
                max_tokens=max_tokens,
                tools_disabled=tools_disabled,
            )
        except (AuthenticationFailure, PromptTooLongFailure):
            # Auth: never retry. PTL: caller must compile a new request.
            raise
        except OpenHarnessApiError as exc:
            # 5xx / rate-limit / transient network — retry.
            last_exc = exc
            continue
    assert last_exc is not None
    raise last_exc


async def _stream_and_collect(
    *,
    messages: list[ConversationMessage],
    system_prompt: str,
    model: str,
    api_client: SupportsStreamingMessages,
    max_tokens: int,
    tools_disabled: bool,
) -> str:
    """One LLM call. Concatenates text deltas, returns the result.

    ``ApiMessageCompleteEvent`` is consumed but not used for the
    return value — text deltas are the authoritative source. This
    matches how the engine's run_query loop assembles text in real
    time (D8.3 stream-event ordering contract from Phase 2).
    """
    request = ApiMessageRequest(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
        # ``tools=[]`` is the explicit "no tools allowed" signal per
        # D29.2 — different from ``tools=None`` which means "field
        # omitted from the wire request" (caller might add tools).
        tools=[] if tools_disabled else None,
    )
    text_parts: list[str] = []
    async for event in api_client.stream_message(request):
        if isinstance(event, ApiTextDeltaEvent):
            text_parts.append(event.text)
        # ApiMessageCompleteEvent + ApiRetryEvent ignored: complete is
        # terminal-marker only; retry events are observability metadata
        # the engine renders to the UI but not part of the summary text.
    return "".join(text_parts)
