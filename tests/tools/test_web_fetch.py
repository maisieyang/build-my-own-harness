"""Tests for the Phase 14 ``WebFetch`` tool (P14-T3).

httpx is mocked via the public ``httpx.MockTransport`` API; no
network is touched.

The HTML fixtures are intentionally simple — markdownify is a
well-tested library; we are not re-testing it. Our tests assert:

- input schema validates correctly (HttpUrl reject non-http schemes)
- chrome-stripping kwargs reach markdownify
- the streaming body cap fires before allocating the whole payload
- HTTP errors map to the right ``ToolResult`` categories
- truncation suffix shows up at the right length
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from openharness.tools.base import ToolExecutionContext
from openharness.tools.web_fetch import (
    WebFetch,
    WebFetchInput,
)


def _ctx() -> ToolExecutionContext:
    """Bare ToolExecutionContext; web tools don't read it."""
    return ToolExecutionContext(cwd=Path("/tmp"))


def _mock_transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ============================================================================
# WebFetchInput — schema validation
# ============================================================================


class TestWebFetchInput:
    def test_http_url_accepted(self) -> None:
        i = WebFetchInput(url="http://example.com/")  # type: ignore[arg-type]
        assert str(i.url) == "http://example.com/"

    def test_https_url_accepted(self) -> None:
        i = WebFetchInput(url="https://example.com/")  # type: ignore[arg-type]
        assert str(i.url) == "https://example.com/"

    @pytest.mark.parametrize(
        "bad_url",
        ["ftp://example.com/", "file:///etc/passwd", "javascript:alert(1)"],
    )
    def test_non_http_schemes_rejected(self, bad_url: str) -> None:
        with pytest.raises(ValueError):
            WebFetchInput(url=bad_url)  # type: ignore[arg-type]

    def test_max_chars_default_ten_thousand(self) -> None:
        i = WebFetchInput(url="https://example.com/")  # type: ignore[arg-type]
        assert i.max_chars == 10_000

    @pytest.mark.parametrize("bad", [0, 50, 99, -1, 100_001, 1_000_000])
    def test_max_chars_out_of_range_rejected(self, bad: int) -> None:
        with pytest.raises(ValueError):
            WebFetchInput(url="https://example.com/", max_chars=bad)  # type: ignore[arg-type]


# ============================================================================
# WebFetch — happy path
# ============================================================================


class TestWebFetchHappyPath:
    async def test_html_rendered_as_markdown(self) -> None:
        html = "<html><body><h1>Hello World</h1><p>This is a paragraph.</p></body></html>"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=html.encode("utf-8"))

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/article"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is False
        assert "Hello World" in result.output
        assert "This is a paragraph." in result.output
        assert result.metadata["url"] == "https://example.com/article"
        assert result.metadata["bytes_fetched"] == len(html)
        assert result.metadata["chars_truncated"] == 0

    async def test_script_and_style_stripped(self) -> None:
        html = (
            "<html><head>"
            "<style>body { color: red; }</style>"
            "<script>alert('hi');</script>"
            "</head><body>"
            "<p>visible text</p>"
            "</body></html>"
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=html.encode("utf-8"))

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is False
        assert "visible text" in result.output
        assert "color: red" not in result.output
        assert "alert" not in result.output

    async def test_nav_aside_header_footer_stripped(self) -> None:
        html = (
            "<html><body>"
            "<nav>Site navigation links</nav>"
            "<header>Site header text</header>"
            "<main><p>main article body</p></main>"
            "<aside>sidebar content</aside>"
            "<footer>copyright notice</footer>"
            "</body></html>"
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=html.encode("utf-8"))

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is False
        assert "main article body" in result.output
        assert "Site navigation" not in result.output
        assert "Site header" not in result.output
        assert "sidebar" not in result.output
        assert "copyright" not in result.output

    async def test_user_agent_header_sent(self) -> None:
        seen_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.update(dict(request.headers))
            return httpx.Response(status_code=200, content=b"<p>ok</p>")

        tool = WebFetch(
            user_agent="OpenHarness/test (+webfetch)",
            transport=_mock_transport(handler),
        )
        await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert seen_headers["user-agent"] == "OpenHarness/test (+webfetch)"


# ============================================================================
# WebFetch — truncation
# ============================================================================


class TestWebFetchTruncation:
    async def test_short_body_no_truncation_suffix(self) -> None:
        html = "<p>short</p>"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=html.encode("utf-8"))

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/", max_chars=10_000),  # type: ignore[arg-type]
            _ctx(),
        )
        assert "[+" not in result.output
        assert result.metadata["chars_truncated"] == 0

    async def test_long_body_truncation_suffix_with_count(self) -> None:
        # Long body — markdownify turns the <p>aaaa...</p> into roughly
        # the same number of chars + paragraph framing.
        html = "<p>" + ("a" * 5000) + "</p>"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=html.encode("utf-8"))

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/", max_chars=500),  # type: ignore[arg-type]
            _ctx(),
        )
        assert "[+" in result.output
        assert "chars truncated]" in result.output
        # Output length is roughly max_chars + suffix; the suffix's
        # exact count depends on markdownify's framing.
        assert result.metadata["chars_truncated"] > 0


# ============================================================================
# WebFetch — error paths
# ============================================================================


class TestWebFetchErrorPaths:
    async def test_404_surfaces_page_not_found(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=404, content=b"Not Found")

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/missing"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is True
        assert "404" in result.output
        assert "page not found" in result.output

    @pytest.mark.parametrize("status_code", [400, 401, 403, 410, 418])
    async def test_other_4xx_surface_as_client_error_not_retryable(self, status_code: int) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=status_code, content=b"oops")

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is True
        assert str(status_code) in result.output
        assert "not retryable" in result.output

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    async def test_5xx_surface_as_server_error_retryable(self, status_code: int) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=status_code, content=b"server oops")

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is True
        assert str(status_code) in result.output
        assert "retryable" in result.output

    async def test_timeout_surfaces_with_retry_hint(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("simulated timeout")

        tool = WebFetch(timeout_seconds=0.5, transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is True
        assert "timed out" in result.output
        assert "0.5" in result.output

    async def test_body_size_cap_aborts_with_hint(self) -> None:
        big_body = b"x" * 10_000  # 10 KiB

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=big_body)

        tool = WebFetch(max_bytes=1_000, transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is True
        assert "exceeded" in result.output
        assert "1000-byte cap" in result.output

    async def test_generic_http_error_surfaces(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated DNS failure")

        tool = WebFetch(transport=_mock_transport(handler))
        result = await tool.execute(
            WebFetchInput(url="https://example.com/"),  # type: ignore[arg-type]
            _ctx(),
        )
        assert result.is_error is True
        assert "URL fetch failed" in result.output


# ============================================================================
# WebFetch — class-level metadata
# ============================================================================


class TestWebFetchClassMetadata:
    def test_static_metadata_matches_d29_1(self) -> None:
        assert WebFetch.name == "WebFetch"
        assert WebFetch.is_read_only is True
        assert WebFetch.input_model is WebFetchInput
        # Description must reference WebSearch so the LLM understands
        # the search-then-fetch chain pattern (D29.1).
        assert "WebSearch" in WebFetch.description
