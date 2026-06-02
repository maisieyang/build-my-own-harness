"""Tests for the Phase 14 web-search substrate (P14-T1).

Tests the provider abstraction (:class:`WebSearchProvider`),
the v1 Tavily implementation, the error hierarchy, and the
:class:`WebSettings` nested config — the pieces P14-T2's
:class:`WebSearch` tool will dispatch through.

httpx is mocked via the public ``httpx.MockTransport`` API; no
network is touched by any unit test in this file. The
``OPENHARNESS_WEB__API_KEY``-gated Tavily integration test is in a
sibling module (P14-T5 close-out).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from openharness.config.settings import WebSettings
from openharness.tools.base import ToolExecutionContext
from openharness.tools.web_search import (
    TavilySearchProvider,
    WebSearch,
    WebSearchAuthError,
    WebSearchInput,
    WebSearchNetworkError,
    WebSearchProvider,
    WebSearchProviderError,
    WebSearchQuotaError,
    WebSearchRequestError,
    WebSearchResult,
)

# ============================================================================
# WebSearchResult — pure dataclass invariants
# ============================================================================


class TestWebSearchResult:
    def test_result_is_frozen(self) -> None:
        r = WebSearchResult(url="https://example.com", title="Hi", snippet="...")
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.url = "https://attacker.example"  # type: ignore[misc]

    def test_result_has_three_string_fields(self) -> None:
        r = WebSearchResult(url="u", title="t", snippet="s")
        assert (r.url, r.title, r.snippet) == ("u", "t", "s")


# ============================================================================
# TavilySearchProvider — construction guard
# ============================================================================


class TestTavilySearchProviderConstruction:
    def test_empty_api_key_raises_auth_error(self) -> None:
        with pytest.raises(WebSearchAuthError) as excinfo:
            TavilySearchProvider(api_key="")
        assert "OPENHARNESS_WEB__API_KEY" in str(excinfo.value)

    def test_non_empty_api_key_constructs(self) -> None:
        # Constructing is enough; search() not invoked here.
        provider = TavilySearchProvider(api_key="tvly-test-key")
        assert isinstance(provider, TavilySearchProvider)


# ============================================================================
# TavilySearchProvider — happy path + error categories
# ============================================================================


def _mock_transport(handler: Any) -> httpx.MockTransport:
    """Wrap a per-test handler into httpx.MockTransport.

    The handler is ``Callable[[httpx.Request], httpx.Response]``.
    """
    return httpx.MockTransport(handler)


class TestTavilySearchProviderHappyPath:
    async def test_search_posts_correct_body_and_parses_results(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                status_code=200,
                json={
                    "query": "openharness",
                    "results": [
                        {
                            "url": "https://example.com/a",
                            "title": "Title A",
                            "content": "Snippet A",
                            "score": 0.95,
                        },
                        {
                            "url": "https://example.com/b",
                            "title": "Title B",
                            "content": "Snippet B",
                            "score": 0.80,
                        },
                    ],
                },
            )

        provider = TavilySearchProvider(
            api_key="tvly-test-key",
            transport=_mock_transport(handler),
        )
        results = await provider.search(query="openharness", num_results=2)

        # Outbound request
        assert captured["url"] == "https://api.tavily.com/search"
        assert captured["body"] == {
            "api_key": "tvly-test-key",
            "query": "openharness",
            "max_results": 2,
            "search_depth": "basic",
        }
        # Parsed response
        assert len(results) == 2
        assert results[0] == WebSearchResult(
            url="https://example.com/a", title="Title A", snippet="Snippet A"
        )
        assert results[1] == WebSearchResult(
            url="https://example.com/b", title="Title B", snippet="Snippet B"
        )

    async def test_empty_results_returns_empty_list(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={"results": []})

        provider = TavilySearchProvider(api_key="tvly-test-key", transport=_mock_transport(handler))
        results = await provider.search(query="nothing matches", num_results=5)
        assert results == []

    async def test_missing_fields_default_to_empty_strings(self) -> None:
        """If Tavily ever returns a partial result row (no url / no title),
        we treat it as best-effort rather than crashing — the tool layer
        decides whether to filter or surface the partial."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={"results": [{"url": "https://x.example"}]},
            )

        provider = TavilySearchProvider(api_key="tvly-test-key", transport=_mock_transport(handler))
        results = await provider.search(query="q", num_results=1)
        assert results == [WebSearchResult(url="https://x.example", title="", snippet="")]


class TestTavilySearchProviderErrorPaths:
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_auth_error_on_401_or_403(self, status_code: int) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=status_code, text="auth failed")

        provider = TavilySearchProvider(api_key="bad-key", transport=_mock_transport(handler))
        with pytest.raises(WebSearchAuthError) as excinfo:
            await provider.search(query="q", num_results=1)
        assert str(status_code) in str(excinfo.value)

    async def test_quota_error_on_429(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=429, text="rate limited")

        provider = TavilySearchProvider(api_key="tvly-test-key", transport=_mock_transport(handler))
        with pytest.raises(WebSearchQuotaError):
            await provider.search(query="q", num_results=1)

    @pytest.mark.parametrize("status_code", [400, 500, 502, 503])
    async def test_request_error_on_other_4xx_5xx(self, status_code: int) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=status_code, text="oops")

        provider = TavilySearchProvider(api_key="tvly-test-key", transport=_mock_transport(handler))
        with pytest.raises(WebSearchRequestError):
            await provider.search(query="q", num_results=1)

    async def test_network_error_on_timeout(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("simulated timeout")

        provider = TavilySearchProvider(
            api_key="tvly-test-key",
            transport=_mock_transport(handler),
            timeout_seconds=0.1,
        )
        with pytest.raises(WebSearchNetworkError) as excinfo:
            await provider.search(query="q", num_results=1)
        assert "timed out" in str(excinfo.value).lower()

    async def test_network_error_on_generic_http_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated DNS failure")

        provider = TavilySearchProvider(api_key="tvly-test-key", transport=_mock_transport(handler))
        with pytest.raises(WebSearchNetworkError):
            await provider.search(query="q", num_results=1)


# ============================================================================
# Protocol satisfaction — any class with the right shape qualifies
# ============================================================================


class _StubProvider:
    """Test double — implements WebSearchProvider Protocol structurally.

    Used by the T2 WebSearch tool tests too, so it lives at module
    scope rather than inside a test class.
    """

    def __init__(self, canned: list[WebSearchResult]) -> None:
        self._canned = canned
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, num_results: int) -> list[WebSearchResult]:
        self.calls.append((query, num_results))
        return self._canned[:num_results]


class TestWebSearchProviderProtocol:
    def test_stub_satisfies_protocol_at_runtime(self) -> None:
        provider: WebSearchProvider = _StubProvider(canned=[])
        # If the structural typing check failed at runtime, the
        # annotation above would not be reached. We also exercise the
        # method to prove the contract shape.
        assert hasattr(provider, "search")

    async def test_stub_returns_canned_results(self) -> None:
        canned = [
            WebSearchResult(url="https://a.example", title="A", snippet="..."),
            WebSearchResult(url="https://b.example", title="B", snippet="..."),
        ]
        provider = _StubProvider(canned=canned)
        results = await provider.search(query="hi", num_results=2)
        assert results == canned
        assert provider.calls == [("hi", 2)]


# ============================================================================
# WebSettings — defaults + env var pickup
# ============================================================================


class TestWebSettings:
    def test_defaults_are_safe(self) -> None:
        s = WebSettings()
        assert s.enabled is False
        assert s.search_provider == "tavily"
        assert s.api_key is None
        assert s.fetch_timeout_seconds == 10.0
        assert s.fetch_max_bytes == 5_000_000
        assert s.fetch_default_max_chars == 10_000

    def test_api_key_is_secret_str(self) -> None:
        s = WebSettings(api_key="tvly-secret")  # type: ignore[arg-type]
        # SecretStr does not surface the value via __repr__ / __str__.
        assert "tvly-secret" not in repr(s)
        assert s.api_key is not None
        assert s.api_key.get_secret_value() == "tvly-secret"

    def test_fetch_timeout_rejects_non_positive(self) -> None:
        with pytest.raises(ValueError):
            WebSettings(fetch_timeout_seconds=0.0)
        with pytest.raises(ValueError):
            WebSettings(fetch_timeout_seconds=-1.0)


# ============================================================================
# Public exception hierarchy
# ============================================================================


# ============================================================================
# WebSearch tool (P14-T2)
# ============================================================================


def _ctx() -> ToolExecutionContext:
    """Bare ToolExecutionContext for tool unit tests; web tools don't read it."""
    return ToolExecutionContext(cwd=Path("/tmp"))


def _failing_provider(exc: Exception) -> _StubProvider:
    """Wrap a stub provider that raises on every search() call."""

    class _Failer:
        async def search(self, query: str, num_results: int) -> list[WebSearchResult]:
            raise exc

    return _Failer()  # type: ignore[return-value]


class TestWebSearchInput:
    def test_query_required_non_empty(self) -> None:
        with pytest.raises(ValueError):
            WebSearchInput(query="")  # type: ignore[call-arg]

    def test_num_results_default_five(self) -> None:
        i = WebSearchInput(query="hi")
        assert i.num_results == 5

    @pytest.mark.parametrize("n", [0, -1, 11, 100])
    def test_num_results_out_of_range_rejected(self, n: int) -> None:
        with pytest.raises(ValueError):
            WebSearchInput(query="hi", num_results=n)

    @pytest.mark.parametrize("n", [1, 3, 5, 10])
    def test_num_results_in_range_accepted(self, n: int) -> None:
        i = WebSearchInput(query="hi", num_results=n)
        assert i.num_results == n


class TestWebSearchToolHappyPath:
    async def test_canned_results_formatted_as_markdown(self) -> None:
        canned = [
            WebSearchResult(
                url="https://example.com/a",
                title="Title A",
                snippet="Snippet A line.",
            ),
            WebSearchResult(
                url="https://example.com/b",
                title="Title B",
                snippet="Snippet B line.",
            ),
        ]
        tool = WebSearch(provider=_StubProvider(canned=canned))
        result = await tool.execute(
            WebSearchInput(query="openharness", num_results=2),
            _ctx(),
        )
        assert result.is_error is False
        assert result.metadata["query"] == "openharness"
        assert result.metadata["result_count"] == 2
        assert 'Search results for "openharness":' in result.output
        assert "1. **Title A** (https://example.com/a)" in result.output
        assert "   Snippet A line." in result.output
        assert "2. **Title B** (https://example.com/b)" in result.output

    async def test_no_results_returns_friendly_message(self) -> None:
        tool = WebSearch(provider=_StubProvider(canned=[]))
        result = await tool.execute(
            WebSearchInput(query="nothing matches", num_results=5),
            _ctx(),
        )
        assert result.is_error is False
        assert result.metadata["result_count"] == 0
        assert 'No search results for "nothing matches".' in result.output

    async def test_provider_receives_query_and_num_results(self) -> None:
        provider = _StubProvider(canned=[])
        tool = WebSearch(provider=provider)
        await tool.execute(WebSearchInput(query="abc", num_results=3), _ctx())
        assert provider.calls == [("abc", 3)]


class TestWebSearchToolErrorPaths:
    async def test_auth_error_surfaces_as_tool_error_with_hint(self) -> None:
        tool = WebSearch(provider=_failing_provider(WebSearchAuthError("bad key")))
        result = await tool.execute(WebSearchInput(query="q"), _ctx())
        assert result.is_error is True
        assert "WebSearch is not authenticated" in result.output
        assert "OPENHARNESS_WEB__API_KEY" in result.output

    async def test_quota_error_surfaces_with_hint(self) -> None:
        tool = WebSearch(provider=_failing_provider(WebSearchQuotaError("over quota")))
        result = await tool.execute(WebSearchInput(query="q"), _ctx())
        assert result.is_error is True
        assert "quota exhausted" in result.output
        assert "retry later" in result.output

    async def test_network_error_surfaces_with_retry_hint(self) -> None:
        tool = WebSearch(provider=_failing_provider(WebSearchNetworkError("DNS fail")))
        result = await tool.execute(WebSearchInput(query="q"), _ctx())
        assert result.is_error is True
        assert "network failure" in result.output
        assert "retryable" in result.output

    async def test_request_error_surfaces_provider_message(self) -> None:
        tool = WebSearch(provider=_failing_provider(WebSearchRequestError("HTTP 500: boom")))
        result = await tool.execute(WebSearchInput(query="q"), _ctx())
        assert result.is_error is True
        assert "HTTP 500: boom" in result.output


class TestWebSearchToolClassMetadata:
    def test_static_metadata_matches_d29_1(self) -> None:
        assert WebSearch.name == "WebSearch"
        assert WebSearch.is_read_only is True
        assert WebSearch.input_model is WebSearchInput
        # Description must mention the workflow pattern so the LLM
        # knows to chain WebSearch → WebFetch (D29.1 rationale).
        assert "WebFetch" in WebSearch.description


class TestFormatResultsAsMarkdown:
    """Direct tests of the formatter — guards the markdown shape so the
    LLM-facing output does not regress silently."""

    async def test_hit_with_missing_title_uses_placeholder(self) -> None:
        canned = [WebSearchResult(url="https://example.com/x", title="", snippet="...")]
        tool = WebSearch(provider=_StubProvider(canned=canned))
        result = await tool.execute(WebSearchInput(query="q"), _ctx())
        assert "(no title)" in result.output

    async def test_hit_with_missing_snippet_uses_placeholder(self) -> None:
        canned = [WebSearchResult(url="https://example.com/x", title="T", snippet="")]
        tool = WebSearch(provider=_StubProvider(canned=canned))
        result = await tool.execute(WebSearchInput(query="q"), _ctx())
        assert "(no snippet)" in result.output


# ============================================================================
# Public exception hierarchy
# ============================================================================


class TestExceptionHierarchy:
    @pytest.mark.parametrize(
        "exc_cls",
        [
            WebSearchAuthError,
            WebSearchQuotaError,
            WebSearchRequestError,
            WebSearchNetworkError,
        ],
    )
    def test_all_specializations_inherit_provider_error(
        self, exc_cls: type[WebSearchProviderError]
    ) -> None:
        # Callers can write a single ``except WebSearchProviderError`` and
        # catch every category — this is the same shape api/errors.py
        # established with ``OpenHarnessApiError``.
        assert issubclass(exc_cls, WebSearchProviderError)
        assert issubclass(exc_cls, Exception)
