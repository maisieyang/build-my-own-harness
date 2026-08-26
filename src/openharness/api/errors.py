"""API client error hierarchy.

Three concrete error types descend from a common base, matching HTTP semantics:

- ``AuthenticationFailure``: 401 / 403 -- never retry
- ``RateLimitFailure``: 429 -- carries optional ``retry_after`` seconds
- ``RequestFailure``: other 4xx / 5xx -- retry decision depends on status_code

All three inherit from ``OpenHarnessApiError`` so callers who do not care
about the specific kind can write ``except OpenHarnessApiError:`` once.

P3-T2.2a (D13.4): ``OpenHarnessApiError`` itself now subclasses the
cross-cutting root :class:`openharness.errors.OpenHarnessError`. This lets
the loop / hooks / permissions layers live under the same root without
miscategorizing themselves as API errors. Backward-compat: existing
``from openharness.api import OpenHarnessApiError`` paths still work
unchanged.

When wrapping an SDK exception, always use ``raise X from sdk_error`` so the
``__cause__`` chain is preserved for debug visibility.
"""

from __future__ import annotations

from openharness.errors import OpenHarnessError


class OpenHarnessApiError(OpenHarnessError):
    """Base class for all API client errors.

    Carries the optional HTTP status code so callers can make fine-grained
    decisions (retry vs not) without parsing message strings.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationFailure(OpenHarnessApiError):
    """API key invalid, expired, or insufficient permissions (401 / 403).

    Retry logic must NEVER retry this -- auth errors do not become un-broken
    by waiting.
    """


class RateLimitFailure(OpenHarnessApiError):
    """Provider rate-limited the request (429).

    ``retry_after`` carries the Provider's suggested wait time in seconds,
    when available (from the ``Retry-After`` header). Retry logic should
    prefer this hint over its own backoff if present, subject to its policy cap.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code)
        self.retry_after = retry_after


class QuotaExceededFailure(RateLimitFailure):
    """Provider quota is exhausted until an external reset or purchase.

    Providers commonly deliver this as HTTP 429, but unlike short-lived rate
    limiting it cannot be repaired by retrying the same request.
    """


class RequestFailure(OpenHarnessApiError):
    """Other HTTP 4xx / 5xx errors that are neither auth nor rate-limit.

    Retry behavior depends on status_code: 5xx typically retried (transient
    server issue), 4xx other than auth/rate typically NOT retried (our
    request itself is malformed). The decision logic lives in retry.py
    (sub-unit 3b), not here.
    """


class PromptTooLongFailure(RequestFailure):
    """Provider rejected the request because the input prompt exceeds the
    model's context window (P4-T3 / D14.1 Layer 2).

    Distinct from generic :class:`RequestFailure` because the engine may
    perform one semantic request recompilation and retry the same Agent turn.
    It never treats deterministic overflow as a transient retry or blindly
    deletes raw Conversation messages. Routing happens in
    :func:`api.client._translate_openai_error` by matching the provider
    error message against known patterns (``_PROMPT_TOO_LONG_PATTERNS``).

    Because it subclasses ``RequestFailure``, existing
    ``except RequestFailure`` handlers still catch it (back-compat).
    Engine code that wants to react specifically writes
    ``except PromptTooLongFailure`` first.
    """


class MalformedToolCallFailure(RequestFailure):
    """Model emitted unparseable JSON in a tool_call ``arguments`` string.

    Two common causes surface as this error:

    - ``finish_reason == "length"`` — the per-call ``max_tokens`` cap
      truncated streaming mid-string. Fix is to raise ``--max-tokens``
      or rephrase so the model emits smaller chunks per tool call.
    - other ``finish_reason`` with malformed JSON — the model itself
      emitted broken syntax. Rare; usually transient. Retry or rephrase.

    Raised by :meth:`_StreamAssembler.finalize` in
    ``api/translation.py`` so engine + CLI layers catch a single typed
    error instead of a raw :class:`json.JSONDecodeError`.
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        finish_reason: str | None = None,
        arguments_excerpt: str | None = None,
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.finish_reason = finish_reason
        self.arguments_excerpt = arguments_excerpt
