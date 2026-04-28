"""Tests for the API client error hierarchy.

The hierarchy must support three behavioral guarantees:

1. Construction with the right fields (message / status_code / retry_after)
2. Polymorphic catch: ``except OpenHarnessApiError`` catches any subclass
3. Exception chain preservation when wrapping SDK errors with ``raise X from Y``
"""

from __future__ import annotations

import pytest

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)


class TestOpenHarnessApiError:
    """Base exception class."""

    def test_construct_with_message_only(self) -> None:
        err = OpenHarnessApiError("API failed")
        assert str(err) == "API failed"
        assert err.status_code is None

    def test_construct_with_status_code(self) -> None:
        err = OpenHarnessApiError("Server unavailable", status_code=503)
        assert err.status_code == 503
        assert str(err) == "Server unavailable"

    def test_inherits_from_exception(self) -> None:
        # Otherwise ``raise OpenHarnessApiError(...)`` would not work.
        assert issubclass(OpenHarnessApiError, Exception)


class TestAuthenticationFailure:
    def test_construct(self) -> None:
        err = AuthenticationFailure("Invalid API key", status_code=401)
        assert err.status_code == 401
        assert str(err) == "Invalid API key"

    def test_construct_with_403(self) -> None:
        # Auth failures cover both 401 (no creds) and 403 (insufficient perms).
        err = AuthenticationFailure("Forbidden", status_code=403)
        assert err.status_code == 403

    def test_is_subclass_of_base(self) -> None:
        assert issubclass(AuthenticationFailure, OpenHarnessApiError)


class TestRateLimitFailure:
    def test_construct_without_retry_after(self) -> None:
        err = RateLimitFailure("Rate limited", status_code=429)
        assert err.status_code == 429
        assert err.retry_after is None

    def test_construct_with_retry_after_float(self) -> None:
        err = RateLimitFailure("Rate limited", status_code=429, retry_after=30.5)
        assert err.retry_after == 30.5

    def test_construct_with_retry_after_int(self) -> None:
        # Provider Retry-After header may be a plain integer; accept it.
        err = RateLimitFailure("Rate limited", status_code=429, retry_after=30)
        assert err.retry_after == 30

    def test_is_subclass_of_base(self) -> None:
        assert issubclass(RateLimitFailure, OpenHarnessApiError)


class TestRequestFailure:
    def test_construct_4xx(self) -> None:
        err = RequestFailure("Bad request", status_code=400)
        assert err.status_code == 400

    def test_construct_5xx(self) -> None:
        err = RequestFailure("Internal server error", status_code=500)
        assert err.status_code == 500

    def test_is_subclass_of_base(self) -> None:
        assert issubclass(RequestFailure, OpenHarnessApiError)


class TestPolymorphicCatch:
    """Catching the base must catch every subclass.

    This is the entire point of the hierarchy: callers who do not care about
    the *kind* of error can write ``except OpenHarnessApiError:`` once.
    """

    def test_base_catches_authentication_failure(self) -> None:
        with pytest.raises(OpenHarnessApiError):
            raise AuthenticationFailure("creds rejected", status_code=403)

    def test_base_catches_rate_limit_failure(self) -> None:
        with pytest.raises(OpenHarnessApiError):
            raise RateLimitFailure("rate limited", status_code=429, retry_after=1.0)

    def test_base_catches_request_failure(self) -> None:
        with pytest.raises(OpenHarnessApiError):
            raise RequestFailure("server error", status_code=500)

    def test_specific_subclass_does_not_catch_sibling(self) -> None:
        # AuthenticationFailure handler must NOT swallow a RateLimitFailure.
        with pytest.raises(RateLimitFailure):
            try:
                raise RateLimitFailure("rate limited", status_code=429)
            except AuthenticationFailure:
                pytest.fail("AuthenticationFailure should not catch RateLimitFailure")


class TestExceptionChainPreserved:
    """``raise X from Y`` must keep ``Y`` reachable via ``__cause__`` so that
    debug visibility (full traceback rendering) survives the wrap."""

    def test_cause_preserved_with_raise_from(self) -> None:
        original = ValueError("network blew up under the SDK")
        try:
            try:
                raise original
            except ValueError as e:
                raise RequestFailure("wrapped: API request failed", status_code=500) from e
        except RequestFailure as final:
            assert final.__cause__ is original
            assert isinstance(final.__cause__, ValueError)

    def test_cause_chain_through_multiple_wraps(self) -> None:
        # Realistic: SDK error → our SDK-level exception → final API error.
        # All three should be visible in the chain.
        root = ConnectionError("socket closed")
        try:
            try:
                try:
                    raise root
                except ConnectionError as e:
                    raise RuntimeError("SDK internal") from e
            except RuntimeError as e:
                raise AuthenticationFailure("auth verify failed", status_code=401) from e
        except AuthenticationFailure as final:
            assert final.__cause__ is not None
            assert isinstance(final.__cause__, RuntimeError)
            assert final.__cause__.__cause__ is root
