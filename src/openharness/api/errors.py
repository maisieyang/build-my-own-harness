"""API client error hierarchy.

Three concrete error types descend from a common base, matching HTTP semantics:

- ``AuthenticationFailure``: 401 / 403 -- never retry
- ``RateLimitFailure``: 429 -- carries optional ``retry_after`` seconds
- ``RequestFailure``: other 4xx / 5xx -- retry decision depends on status_code

All three inherit from ``OpenHarnessApiError`` so callers who do not care
about the specific kind can write ``except OpenHarnessApiError:`` once.

When wrapping an SDK exception, always use ``raise X from sdk_error`` so the
``__cause__`` chain is preserved for debug visibility.
"""

from __future__ import annotations


class OpenHarnessApiError(Exception):
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
    prefer this hint over its own backoff if present.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code)
        self.retry_after = retry_after


class RequestFailure(OpenHarnessApiError):
    """Other HTTP 4xx / 5xx errors that are neither auth nor rate-limit.

    Retry behavior depends on status_code: 5xx typically retried (transient
    server issue), 4xx other than auth/rate typically NOT retried (our
    request itself is malformed). The decision logic lives in retry.py
    (sub-unit 3b), not here.
    """
