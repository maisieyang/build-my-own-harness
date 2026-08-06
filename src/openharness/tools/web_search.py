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
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from pydantic import BaseModel, Field

from openharness.tools.base import (
    BaseTool,
    ExecutionDomain,
    ExternalEffectKind,
    ExternalEffectSurface,
    ToolResult,
)

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext

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


# ============================================================================
# WebSearch tool (P14-T2) — BaseTool subclass dispatched through Provider
# ============================================================================


_MIN_NUM_RESULTS = 1
_MAX_NUM_RESULTS = 10
_DEFAULT_NUM_RESULTS = 5


class WebSearchInput(BaseModel):
    """Input schema for :class:`WebSearch`.

    The LLM picks ``num_results`` based on how broadly it wants to
    scan; ``query`` is the natural-language search string. Tavily
    (and most engines) does not require quoting / boolean syntax —
    natural language ranks fine.
    """

    query: str = Field(
        min_length=1,
        description=(
            "Natural-language search query. Be specific (e.g. "
            "'LangChain v0.3 streaming API changes'); generic queries "
            "('AI news') burn context with low-signal hits."
        ),
    )
    num_results: int = Field(
        default=_DEFAULT_NUM_RESULTS,
        ge=_MIN_NUM_RESULTS,
        le=_MAX_NUM_RESULTS,
        description=(
            "How many search hits to return (1-10, default 5). Pick "
            "small (3) when you only need the top hit; pick larger "
            "(8-10) when surveying a topic."
        ),
    )


class WebSearch(BaseTool[WebSearchInput]):
    """Search the web for information.

    Dispatched through a :class:`WebSearchProvider` (D29.2). The
    provider abstraction means swapping Tavily for Brave / Serper /
    etc. is a single-file change; this tool does not know which
    provider is in use.

    Construction is injection-only: the CLI startup wires a concrete
    :class:`TavilySearchProvider` (with API key unwrapped from
    :class:`SecretStr`) and hands it here. Tests inject ``_StubProvider``
    to assert tool-layer behavior without network.

    Errors from the provider are caught and surfaced as
    :class:`ToolResult` with ``is_error=True`` and a category-specific
    message (D29.7). The engine never sees the underlying exception —
    the LLM does, and decides whether to retry / rephrase / give up.
    """

    execution_domain = ExecutionDomain.EXTERNAL_EFFECT
    external_effect_surface = ExternalEffectSurface.WEB
    external_effect_kind = ExternalEffectKind.NETWORK_READ
    external_effect_trusted = True
    name = "WebSearch"
    description = (
        "Search the web for information. Returns up to num_results "
        "URLs with title + snippet. Use to discover URLs you don't "
        "already know; then call WebFetch to read specific pages in "
        "detail. Prefer this over trying to recall facts that require "
        "current information (news, recent releases, evolving topics)."
    )
    input_model = WebSearchInput
    # Read-only: WebSearch dispatches a GET-like network request and
    # mutates nothing locally. Matches Read / Grep / LoadSkillTool —
    # AuthZ Tier 3 lax path applies.
    is_read_only = True

    def __init__(self, provider: WebSearchProvider) -> None:
        self._provider = provider

    async def execute(
        self,
        input: WebSearchInput,
        context: ToolExecutionContext,  # noqa: ARG002 — BaseTool contract; web tool ignores cwd
    ) -> ToolResult:
        try:
            results = await self._provider.search(
                query=input.query,
                num_results=input.num_results,
            )
        except WebSearchAuthError as exc:
            return ToolResult(
                output=(
                    f"WebSearch is not authenticated: {exc}\n"
                    "Hint: set OPENHARNESS_WEB__API_KEY to a valid "
                    "provider API key."
                ),
                is_error=True,
            )
        except WebSearchQuotaError as exc:
            return ToolResult(
                output=(
                    f"WebSearch provider quota exhausted: {exc}\n"
                    "Hint: retry later or upgrade the provider plan."
                ),
                is_error=True,
            )
        except WebSearchNetworkError as exc:
            return ToolResult(
                output=(
                    f"WebSearch network failure: {exc}\nHint: retryable — the LLM may try again."
                ),
                is_error=True,
            )
        except WebSearchRequestError as exc:
            return ToolResult(
                output=f"WebSearch provider error: {exc}",
                is_error=True,
            )

        if not results:
            return ToolResult(
                output=f'No search results for "{input.query}".',
                metadata={"query": input.query, "result_count": 0},
            )

        return ToolResult(
            output=_format_results_as_markdown(input.query, results),
            metadata={"query": input.query, "result_count": len(results)},
        )


def _format_results_as_markdown(query: str, results: list[WebSearchResult]) -> str:
    """Render search hits as numbered markdown the LLM reads cleanly.

    Per-hit shape:

    .. code-block:: markdown

        1. **Title here** (https://example.com/path)
           One-line snippet here.
    """
    lines: list[str] = [f'Search results for "{query}":', ""]
    for i, hit in enumerate(results, start=1):
        title = hit.title or "(no title)"
        url = hit.url or "(no url)"
        snippet = hit.snippet.strip() or "(no snippet)"
        lines.append(f"{i}. **{title}** ({url})")
        lines.append(f"   {snippet}")
        lines.append("")
    # Drop the trailing blank line for a tight end.
    return "\n".join(lines).rstrip()


__all__ = [
    "TavilySearchProvider",
    "WebSearch",
    "WebSearchAuthError",
    "WebSearchInput",
    "WebSearchNetworkError",
    "WebSearchProvider",
    "WebSearchProviderError",
    "WebSearchQuotaError",
    "WebSearchRequestError",
    "WebSearchResult",
]
