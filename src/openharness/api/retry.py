"""Retry policy for API client calls.

Provides:

- :class:`RetryPolicy` -- frozen dataclass holding retry parameters
- :data:`DEFAULT_POLICY` -- 3 attempts / 1s base / 30s cap / 0.25 jitter,
  matching REFERENCE.md §4.2
- :func:`compute_delay` -- fractional-jitter exponential backoff
- :func:`is_retryable` -- decision matrix using the ``api.errors`` hierarchy
- :func:`with_retry` -- main API; wraps an async callable with retry-on-failure

Designed for testability:

- :func:`compute_delay` accepts an injectable ``random_fn``
- :func:`with_retry` accepts an injectable ``sleep``

So tests run in milliseconds without ever touching real wall-clock.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from openharness.api.errors import (
    AuthenticationFailure,
    RateLimitFailure,
    RequestFailure,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")


def _system_random() -> float:
    """Default randomness source for :func:`compute_delay`.

    Wrapped (rather than passing ``random.random`` directly) so the parameter
    type ``Callable[[], float]`` does not widen to ``Callable[[], Any]`` on
    mypy --strict.
    """
    return random.random()


@dataclass(frozen=True)
class RetryPolicy:
    """Retry parameters. Immutable so a single instance can be shared globally."""

    max_attempts: int
    base_delay: float
    max_delay: float
    jitter: float


DEFAULT_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    jitter=0.25,
)


def compute_delay(
    attempt: int,
    policy: RetryPolicy,
    *,
    random_fn: Callable[[], float] = _system_random,
) -> float:
    """Fractional-jitter exponential backoff.

    For attempt n (1-indexed):

    1. ``base = min(policy.base_delay * 2 ** (n - 1), policy.max_delay)``
    2. ``actual = base * (1 - policy.jitter * random_fn())``

    Result is in ``[base * (1 - jitter), base]``. With default policy
    (jitter = 0.25), the actual delay falls within 75-100% of the
    exponential base. Caps at ``max_delay`` regardless of attempt.
    """
    # ``2.0 ** ...`` (not ``2 ** ...``) — int.__pow__(int) returns ``Any`` in
    # typeshed (because the result can be int or float depending on sign of the
    # exponent), which would leak ``Any`` into our return type. Forcing the base
    # to ``2.0`` keeps the whole expression strictly ``float``.
    base = min(policy.base_delay * (2.0 ** (attempt - 1)), policy.max_delay)
    return base * (1 - policy.jitter * random_fn())


def is_retryable(error: Exception) -> bool:
    """Decide whether an error should trigger a retry.

    Conservative -- unknown error types are NOT retried, so unexpected
    bugs surface fast rather than getting eaten by a retry loop.
    """
    if isinstance(error, AuthenticationFailure):
        return False
    if isinstance(error, RateLimitFailure):
        return True
    if isinstance(error, RequestFailure):
        return error.status_code is not None and 500 <= error.status_code < 600
    return False


async def with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_POLICY,
    on_retry: Callable[[int, float, Exception], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Wrap an async call with retry-on-failure.

    The call is invoked up to ``policy.max_attempts`` times. Between
    attempts:

    - If the error is :class:`RateLimitFailure` with ``retry_after``
      set, that wins (Provider knows best)
    - Otherwise :func:`compute_delay` calculates the backoff
    - ``on_retry`` callback is invoked (e.g., to emit ``ApiRetryEvent``)
    - ``sleep`` is awaited

    Raises the last error if all attempts exhaust. Non-retryable errors
    raise immediately on the first failure with no sleep.

    The ``call`` parameter must be a **factory** (zero-arg callable),
    not a bare coroutine, because awaitables are one-shot -- we need to
    produce a fresh one per attempt.
    """
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await call()
        except Exception as e:
            if not is_retryable(e):
                raise
            if attempt >= policy.max_attempts:
                raise

            if isinstance(e, RateLimitFailure) and e.retry_after is not None:
                delay = e.retry_after
            else:
                delay = compute_delay(attempt, policy)

            if on_retry is not None:
                await on_retry(attempt, delay, e)

            await sleep(delay)

    # Unreachable: we either return inside the try, or we re-raise on the
    # last failure above. This line exists only to satisfy mypy's
    # "must return" check on a function that never falls through.
    raise RuntimeError("with_retry: unreachable — loop must return or raise")
