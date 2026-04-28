"""Tests for the API client retry policy.

The retry layer must:

1. Compute exponential-with-jitter delays correctly (and cap at max_delay)
2. Decide retryability via the ``api.errors`` hierarchy (conservative on unknown)
3. Retry an async call up to ``max_attempts`` times, preferring
   ``RateLimitFailure.retry_after`` over computed backoff
4. Surface retries to callers via an optional ``on_retry`` callback
5. Be fully testable without real ``asyncio.sleep`` (sleep is injected)
"""

from __future__ import annotations

from openharness.api.errors import (
    AuthenticationFailure,
    RateLimitFailure,
    RequestFailure,
)
from openharness.api.retry import (
    DEFAULT_POLICY,
    RetryPolicy,
    compute_delay,
    is_retryable,
    with_retry,
)


class TestRetryPolicy:
    def test_default_policy_matches_reference(self) -> None:
        # REFERENCE.md §4.2: 3 attempts, base 1s, max 30s, jitter 0.25
        assert DEFAULT_POLICY.max_attempts == 3
        assert DEFAULT_POLICY.base_delay == 1.0
        assert DEFAULT_POLICY.max_delay == 30.0
        assert DEFAULT_POLICY.jitter == 0.25

    def test_custom_policy_construction(self) -> None:
        policy = RetryPolicy(max_attempts=5, base_delay=0.5, max_delay=60.0, jitter=0.1)
        assert policy.max_attempts == 5
        assert policy.base_delay == 0.5
        assert policy.max_delay == 60.0
        assert policy.jitter == 0.1


class TestComputeDelay:
    """Fractional-jitter exponential backoff math.

    Formula: actual = base * (1 - jitter * random_fn())
    where base = min(base_delay * 2^(attempt-1), max_delay).
    Range: [base * (1 - jitter), base].
    """

    def test_attempt_1_no_jitter(self) -> None:
        # random_fn=0 → no jitter applied → max possible delay
        delay = compute_delay(1, DEFAULT_POLICY, random_fn=lambda: 0.0)
        assert delay == 1.0  # base_delay * 2^0 * (1 - 0)

    def test_attempt_1_full_jitter(self) -> None:
        # random_fn=1 → full jitter applied → min possible delay
        delay = compute_delay(1, DEFAULT_POLICY, random_fn=lambda: 1.0)
        assert delay == 0.75  # 1.0 * (1 - 0.25 * 1.0)

    def test_exponential_growth(self) -> None:
        delay_1 = compute_delay(1, DEFAULT_POLICY, random_fn=lambda: 0.0)
        delay_2 = compute_delay(2, DEFAULT_POLICY, random_fn=lambda: 0.0)
        delay_3 = compute_delay(3, DEFAULT_POLICY, random_fn=lambda: 0.0)
        assert delay_1 == 1.0
        assert delay_2 == 2.0
        assert delay_3 == 4.0

    def test_capped_at_max_delay(self) -> None:
        # Without cap, attempt=10 would be 1.0 * 2^9 = 512s
        delay = compute_delay(10, DEFAULT_POLICY, random_fn=lambda: 0.0)
        assert delay == 30.0

    def test_jitter_always_within_bounds(self) -> None:
        # 100 real-random samples must all stay within [base*(1-jitter), base]
        for _ in range(100):
            delay = compute_delay(1, DEFAULT_POLICY)
            assert 0.75 <= delay <= 1.0


class TestIsRetryable:
    def test_authentication_failure_never_retried(self) -> None:
        assert is_retryable(AuthenticationFailure("invalid", status_code=401)) is False
        assert is_retryable(AuthenticationFailure("forbidden", status_code=403)) is False

    def test_rate_limit_always_retried(self) -> None:
        assert is_retryable(RateLimitFailure("limited", status_code=429)) is True

    def test_request_failure_5xx_retried(self) -> None:
        assert is_retryable(RequestFailure("server", status_code=500)) is True
        assert is_retryable(RequestFailure("bad gateway", status_code=502)) is True
        assert is_retryable(RequestFailure("unavailable", status_code=503)) is True

    def test_request_failure_4xx_not_retried(self) -> None:
        assert is_retryable(RequestFailure("bad request", status_code=400)) is False
        assert is_retryable(RequestFailure("not found", status_code=404)) is False

    def test_request_failure_no_status_not_retried(self) -> None:
        # Conservative: if we do not know the status, do not retry.
        assert is_retryable(RequestFailure("unknown", status_code=None)) is False

    def test_unknown_exception_not_retried(self) -> None:
        # Conservative: surface unknown errors fast rather than reflexively retrying.
        assert is_retryable(ValueError("unrelated")) is False
        assert is_retryable(RuntimeError("internal")) is False
        assert is_retryable(ConnectionError("network")) is False


class TestWithRetryHappyPath:
    async def test_first_call_succeeds_no_retry(self) -> None:
        call_count = 0

        async def call() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        result = await with_retry(call, sleep=fake_sleep)
        assert result == "success"
        assert call_count == 1
        assert slept == []  # No retries → no sleep

    async def test_returns_value_from_eventual_success(self) -> None:
        attempts: list[int] = []

        async def call() -> int:
            attempts.append(len(attempts))
            if len(attempts) < 3:
                raise RateLimitFailure("limited", status_code=429)
            return 42

        async def fake_sleep(seconds: float) -> None:
            pass

        result = await with_retry(call, sleep=fake_sleep)
        assert result == 42
        assert len(attempts) == 3


class TestWithRetryFailureModes:
    async def test_non_retryable_raises_immediately_no_sleep(self) -> None:
        async def call() -> str:
            raise AuthenticationFailure("invalid key", status_code=401)

        sleep_called = False

        async def fake_sleep(seconds: float) -> None:
            nonlocal sleep_called
            sleep_called = True

        try:
            await with_retry(call, sleep=fake_sleep)
            raise AssertionError("Expected AuthenticationFailure")
        except AuthenticationFailure:
            pass

        assert sleep_called is False

    async def test_exhausted_attempts_raises_last_error(self) -> None:
        attempt_count = 0

        async def call() -> str:
            nonlocal attempt_count
            attempt_count += 1
            raise RateLimitFailure("persistently limited", status_code=429)

        async def fake_sleep(seconds: float) -> None:
            pass

        try:
            await with_retry(call, sleep=fake_sleep)
            raise AssertionError("Expected RateLimitFailure")
        except RateLimitFailure:
            pass

        assert attempt_count == DEFAULT_POLICY.max_attempts  # 3 attempts total

    async def test_unknown_exception_not_retried(self) -> None:
        async def call() -> str:
            raise ValueError("unrelated bug")

        sleep_called = False

        async def fake_sleep(seconds: float) -> None:
            nonlocal sleep_called
            sleep_called = True

        try:
            await with_retry(call, sleep=fake_sleep)
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass

        assert sleep_called is False


class TestWithRetryDelaySources:
    async def test_retry_after_takes_priority_over_backoff(self) -> None:
        attempts: list[int] = []

        async def call() -> str:
            attempts.append(len(attempts))
            if len(attempts) < 2:
                raise RateLimitFailure("limited", status_code=429, retry_after=10.0)
            return "done"

        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        result = await with_retry(call, sleep=fake_sleep)
        assert result == "done"
        # Provider said 10s — that beats whatever exponential backoff would compute
        assert slept == [10.0]

    async def test_no_retry_after_uses_computed_backoff(self) -> None:
        attempts: list[int] = []

        async def call() -> str:
            attempts.append(len(attempts))
            if len(attempts) < 2:
                raise RateLimitFailure("limited", status_code=429)  # no retry_after
            return "done"

        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        await with_retry(call, sleep=fake_sleep)
        # First retry uses compute_delay(attempt=1) which is in [0.75, 1.0]
        assert len(slept) == 1
        assert 0.75 <= slept[0] <= 1.0


class TestWithRetryCallback:
    async def test_on_retry_invoked_for_each_retry(self) -> None:
        attempts: list[int] = []

        async def call() -> str:
            attempts.append(len(attempts))
            if len(attempts) < 3:
                raise RateLimitFailure("limited", status_code=429)
            return "ok"

        retry_events: list[tuple[int, float, Exception]] = []

        async def on_retry(attempt: int, delay: float, error: Exception) -> None:
            retry_events.append((attempt, delay, error))

        async def fake_sleep(seconds: float) -> None:
            pass

        await with_retry(call, sleep=fake_sleep, on_retry=on_retry)
        # 3 calls = 1 success + 2 prior retries → 2 callback invocations
        assert len(retry_events) == 2
        assert retry_events[0][0] == 1  # First retry is after attempt 1
        assert retry_events[1][0] == 2  # Second retry is after attempt 2
        assert all(isinstance(e[2], RateLimitFailure) for e in retry_events)

    async def test_on_retry_not_invoked_on_non_retryable(self) -> None:
        async def call() -> str:
            raise AuthenticationFailure("invalid", status_code=401)

        retry_events: list[tuple[int, float, Exception]] = []

        async def on_retry(attempt: int, delay: float, error: Exception) -> None:
            retry_events.append((attempt, delay, error))

        async def fake_sleep(seconds: float) -> None:
            pass

        try:
            await with_retry(call, sleep=fake_sleep, on_retry=on_retry)
            raise AssertionError("Expected AuthenticationFailure")
        except AuthenticationFailure:
            pass

        assert retry_events == []  # No retry → no callback
