"""Web search infrastructure (P14-T1).

Lands the provider abstraction and one concrete implementation
(:class:`TavilySearchProvider`). The :class:`WebSearch` tool itself is
P14-T2; this module is the substrate it dispatches through.

Architecture (D29.2 — Protocol + identity impl + future swap):

- :class:`WebSearchResult` — wire shape returned to the tool layer
  (frozen dataclass; never mutated after construction).
- :class:`WebSearchProvider` — Protocol any provider can implement.
  ``async def search(query, num_results) -> list[WebSearchResult]``.
- :class:`TavilySearchProvider` — v1 default implementation, POSTs to
  the Tavily API. Designed to be swappable: a future
  ``BraveSearchProvider`` lands as a sibling without touching
  :class:`WebSearch`.
- :class:`WebSearchProviderError` (+ specializations) — surfaces the
  provider-level error categories the tool layer routes into
  :class:`ToolResult` (D29.7).

Key design choices:

- Per-call ``httpx.AsyncClient``: simpler than caching a client at
  module scope; per-search cost is dominated by network round-trip
  (~hundreds of ms) — client construction (~ms) is negligible. If
  measurements ever justify caching, the Protocol shape doesn't
  change.
- API key handling: the provider's ``__init__`` takes a plain
  ``str``. The factory at the tool-build site is responsible for
  unwrapping ``SecretStr`` from ``WebSettings`` so secrets do not
  leak into ``__repr__``.
- No retry inside the provider: the engine layer's retry policy
  (api/retry.py) is for LLM dispatch, not arbitrary HTTP. Web tool
  failures surface to the LLM, which decides whether to retry —
  same pattern as :class:`Bash` (engine never retries a Bash call
  silently).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class WebSearchResult:
    """One hit returned by a :class:`WebSearchProvider`.

    Frozen because hits are immutable artefacts of a single search;
    any aggregation (dedup / rerank) builds a new list rather than
    mutating in place.
    """

    url: str
    title: str
    snippet: str


class WebSearchProviderError(Exception):
    """Base for any failure originating in a :class:`WebSearchProvider`.

    Subclasses encode the error category so the tool layer can route
    into the right ``ToolResult(is_error=True)`` message without
    parsing exception strings.
    """


class WebSearchAuthError(WebSearchProviderError):
    """API key missing, invalid, or expired (HTTP 401 / 403)."""


class WebSearchQuotaError(WebSearchProviderError):
    """Provider quota exhausted (HTTP 429 or provider-specific quota signal)."""


class WebSearchRequestError(WebSearchProviderError):
    """Provider returned an unexpected HTTP error (non-auth, non-quota)."""


class WebSearchNetworkError(WebSearchProviderError):
    """Network failure — timeout, DNS, connection refused."""


class WebSearchProvider(Protocol):
    """Contract every web-search backend implements.

    A provider takes a query string and a requested result count and
    returns a list of :class:`WebSearchResult`. The caller (the
    :class:`WebSearch` tool, P14-T2) is responsible for clamping
    ``num_results`` to a sensible range — providers SHOULD honor it
    as a hint but MAY return fewer if the index has nothing better.
    """

    async def search(
        self,
        query: str,
        num_results: int,
    ) -> list[WebSearchResult]: ...


class TavilySearchProvider:
    """Tavily implementation of :class:`WebSearchProvider` (D29.2 default).

    Tavily was purpose-built for LLM-agent search workflows: it
    returns clean snippets without ad chrome and ranks for
    informational queries rather than commercial intent. Free tier
    is 1000 searches / month without credit card, sufficient for
    dogfood.

    The provider POSTs JSON to ``api.tavily.com/search`` with the
    API key in the body (Tavily's convention; they do not use the
    ``Authorization`` header). Response shape:

    .. code-block:: json

        {
          "query": "...",
          "results": [
            {"url": "...", "title": "...", "content": "...", "score": 0.95},
            ...
          ]
        }

    ``content`` is Tavily's pre-cleaned snippet — mapped directly
    to :attr:`WebSearchResult.snippet` without further processing.
    """

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise WebSearchAuthError("Tavily API key is empty. Set OPENHARNESS_WEB__API_KEY.")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        # ``transport`` is a test-injection seam — ``httpx.MockTransport``
        # lets unit tests assert on the request body without a real
        # network call. Production paths pass ``None`` and httpx
        # selects its default async transport.
        self._transport = transport

    async def search(
        self,
        query: str,
        num_results: int,
    ) -> list[WebSearchResult]:
        body: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": num_results,
            "search_depth": "basic",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(_TAVILY_ENDPOINT, json=body)
        except httpx.TimeoutException as exc:
            raise WebSearchNetworkError(
                f"Tavily request timed out after {self._timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise WebSearchNetworkError(f"Tavily network error: {exc}") from exc

        if response.status_code in (401, 403):
            raise WebSearchAuthError(
                f"Tavily rejected the API key (HTTP {response.status_code}). "
                "Verify OPENHARNESS_WEB__API_KEY."
            )
        if response.status_code == 429:
            raise WebSearchQuotaError(
                "Tavily quota exhausted (HTTP 429). Wait for monthly reset or upgrade plan."
            )
        if response.status_code >= 400:
            raise WebSearchRequestError(
                f"Tavily request failed (HTTP {response.status_code}): {response.text[:200]}"
            )

        payload = response.json()
        raw_results = payload.get("results", [])
        return [
            WebSearchResult(
                url=str(item.get("url", "")),
                title=str(item.get("title", "")),
                snippet=str(item.get("content", "")),
            )
            for item in raw_results
        ]


__all__ = [
    "TavilySearchProvider",
    "WebSearchAuthError",
    "WebSearchNetworkError",
    "WebSearchProvider",
    "WebSearchProviderError",
    "WebSearchQuotaError",
    "WebSearchRequestError",
    "WebSearchResult",
]
