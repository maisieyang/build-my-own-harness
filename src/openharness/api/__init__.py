"""API client layer -- Provider-specific clients implementing the
``SupportsStreamingMessages`` protocol.

Phase 1 ships the OpenAI-compatible client targeting Qwen via DashScope.
See ``decisions/03-api-client-strategy.md`` for the strategic choice.

Public API is re-exported from this module; callers do not need to know
about internal submodules.
"""

from __future__ import annotations

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)

__all__ = [
    "AuthenticationFailure",
    "OpenHarnessApiError",
    "RateLimitFailure",
    "RequestFailure",
]
