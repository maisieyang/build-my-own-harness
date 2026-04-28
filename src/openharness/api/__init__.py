"""API client layer -- Provider-specific clients implementing the
``SupportsStreamingMessages`` protocol.

Phase 1 ships :class:`OpenAICompatibleApiClient` targeting Qwen via DashScope
(see ``decisions/03-api-client-strategy.md`` and
``decisions/04-api-client-implementation.md``). Future Provider clients
(Anthropic-native, etc.) are added as siblings here.

Public API is re-exported from this module; callers do not need to know
about internal submodules:

    from openharness.api import (
        OpenAICompatibleApiClient,
        OpenHarnessApiError,
        RetryPolicy,
        DEFAULT_POLICY,
    )

Internal modules (not re-exported, but accessible for tests / advanced use):

- ``openharness.api.translation`` -- ``to_openai_request`` and
  ``_StreamAssembler``
- ``openharness.api.retry`` -- ``with_retry`` / ``compute_delay`` /
  ``is_retryable``
"""

from __future__ import annotations

from openharness.api.client import OpenAICompatibleApiClient
from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)
from openharness.api.retry import DEFAULT_POLICY, RetryPolicy

__all__ = [
    "DEFAULT_POLICY",
    "AuthenticationFailure",
    "OpenAICompatibleApiClient",
    "OpenHarnessApiError",
    "RateLimitFailure",
    "RequestFailure",
    "RetryPolicy",
]
