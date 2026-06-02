"""``WebFetch`` tool (P14-T3).

GETs a URL, strips chrome (nav / script / style / aside / header /
footer), renders the remaining HTML to markdown via ``markdownify``,
and truncates with the same ``[+N chars truncated]`` suffix the
Phase 4 microcompact already uses for ``Read`` and tool-result
clipping.

Design notes (D29.5):

- **Body cap is a streaming guard**, not a post-hoc length check.
  ``httpx.AsyncClient.stream()`` lets us accumulate chunk-by-chunk
  and abort the moment we exceed ``max_bytes`` — pathological pages
  cannot pin 5MB+ of memory just because we asked them politely.
- **BeautifulSoup pre-pass, then markdownify.** markdownify's
  ``strip=[...]`` kwarg removes the *wrapping* tag but keeps inner
  text — so ``<script>alert(1)</script>`` becomes literal
  ``alert(1)`` in the markdown. We want chrome and inline JS / CSS
  *gone entirely*, so a BeautifulSoup ``decompose()`` pass runs
  first. BeautifulSoup is already in the dep tree (pulled by
  markdownify) and listed explicitly in pyproject.toml since this
  module now uses its public API.
- **No provider abstraction.** Unlike :class:`WebSearch` (which can
  route through Tavily / Brave / Serper), there is only one
  reasonable way to fetch a URL: HTTP GET. A Protocol here would be
  premature abstraction, so the tool is self-contained.
- **Errors are :class:`ToolResult(is_error=True)`, not raises.** Same
  contract as :class:`Bash`: the engine never sees an HTTP exception,
  the LLM does, and decides whether to retry / pick a different URL /
  give up (D29.7).
- **User-Agent identifies us honestly**, not stealth. Stealth
  user-agents conflict with the "agent is a citizen of the web" framing
  we want for this tool's product surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, HttpUrl

from openharness.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext

_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MAX_BYTES = 5_000_000
_DEFAULT_MAX_CHARS = 10_000
_MIN_MAX_CHARS = 100
_MAX_MAX_CHARS = 100_000
_STRIP_TAGS = ["script", "style", "nav", "aside", "header", "footer"]
_DEFAULT_USER_AGENT = "OpenHarness/+webfetch"


class WebFetchInput(BaseModel):
    """Input schema for :class:`WebFetch`."""

    url: HttpUrl = Field(
        description=(
            "Full URL to fetch (http:// or https://). The LLM should "
            "pick URLs surfaced by WebSearch hits or supplied by the "
            "user — do NOT guess URLs based on training data."
        ),
    )
    max_chars: int = Field(
        default=_DEFAULT_MAX_CHARS,
        ge=_MIN_MAX_CHARS,
        le=_MAX_MAX_CHARS,
        description=(
            "Cap on returned markdown length (100-100000, default "
            "10000). Reduce when a single fetch must coexist with "
            "many other tool results in the same turn."
        ),
    )


class WebFetch(BaseTool[WebFetchInput]):
    """Fetch a URL and return its content as markdown.

    Read-only network operation; matches the AuthZ disposition of
    :class:`WebSearch` / :class:`Read` / :class:`Grep`.

    Constructor accepts the settings derived from
    :class:`WebSettings`:

    - ``timeout_seconds`` — per-URL deadline
    - ``max_bytes`` — body cap (streaming abort)
    - ``default_max_chars`` — used when the LLM does not specify
      ``max_chars`` on the input (Pydantic field default mirrors
      this; the constructor value is the **factory default**)

    ``transport`` is the test-injection seam for
    :class:`httpx.MockTransport`, mirroring
    :class:`TavilySearchProvider`.
    """

    name = "WebFetch"
    description = (
        "Fetch a URL and return its content as markdown. Use to read "
        "specific pages discovered via WebSearch or provided by the "
        "user. Honors max_chars (default 10000) and strips nav / "
        "script / style chrome so the LLM sees just the article body."
    )
    input_model = WebFetchInput
    is_read_only = True

    def __init__(
        self,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        user_agent: str = _DEFAULT_USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._user_agent = user_agent
        self._transport = transport

    async def execute(
        self,
        input: WebFetchInput,
        context: ToolExecutionContext,  # noqa: ARG002 — BaseTool contract; web tool ignores cwd
    ) -> ToolResult:
        url_str = str(input.url)
        body, fetch_error = await self._stream_body(url_str)
        if fetch_error is not None:
            return fetch_error

        # ``body`` is bytes by here; decode as UTF-8 with replacement so
        # mis-declared encodings don't surface as a tool-layer crash.
        # markdownify is happy with replacement chars; an LLM reading
        # the output will treat them as noise rather than signal.
        raw_html = bytes(body).decode("utf-8", errors="replace")

        try:
            cleaned_html = _strip_chrome(raw_html)
            markdown = markdownify(cleaned_html)
        except Exception as exc:
            return ToolResult(
                output=(
                    f"Failed to render page as markdown: {exc}\nHint: page may use unusual encoding."
                ),
                is_error=True,
            )

        truncated_chars = 0
        if len(markdown) > input.max_chars:
            truncated_chars = len(markdown) - input.max_chars
            markdown = markdown[: input.max_chars] + f"\n[+{truncated_chars} chars truncated]"

        return ToolResult(
            output=markdown,
            metadata={
                "url": url_str,
                "bytes_fetched": len(body),
                "chars_truncated": truncated_chars,
            },
        )

    async def _stream_body(
        self,
        url: str,
    ) -> tuple[bytearray, ToolResult | None]:
        """GET ``url``, accumulating up to ``max_bytes`` of body.

        Returns ``(body, None)`` on success, or
        ``(empty_bytearray, ToolResult(is_error=True))`` on any failure
        the caller should surface to the LLM. Splitting body
        acquisition from markdown rendering keeps the error branches
        narrow + each error case becomes a single early return.
        """
        body = bytearray()
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    transport=self._transport,
                    headers={"User-Agent": self._user_agent},
                    follow_redirects=True,
                ) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code == 404:
                    return body, ToolResult(
                        output=f"URL returned HTTP 404 (page not found): {url}",
                        is_error=True,
                    )
                if 400 <= response.status_code < 500:
                    return body, ToolResult(
                        output=(
                            f"URL returned HTTP {response.status_code} "
                            f"(client error, not retryable): {url}"
                        ),
                        is_error=True,
                    )
                if response.status_code >= 500:
                    return body, ToolResult(
                        output=(
                            f"URL returned HTTP {response.status_code} "
                            f"(server error, retryable): {url}"
                        ),
                        is_error=True,
                    )

                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > self._max_bytes:
                        return body, ToolResult(
                            output=(
                                f"URL body exceeded {self._max_bytes}-byte cap before fetch completed: "
                                f"{url}\nHint: page is too large; try a more specific URL."
                            ),
                            is_error=True,
                        )
                    body.extend(chunk)
        except httpx.TimeoutException:
            return body, ToolResult(
                output=(
                    f"URL timed out after {self._timeout_seconds}s: {url}\n"
                    "Hint: retryable — the server may be slow; the LLM may try again."
                ),
                is_error=True,
            )
        except httpx.InvalidURL as exc:
            return body, ToolResult(
                output=f"Invalid URL: {exc}",
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return body, ToolResult(
                output=f"URL fetch failed: {exc}",
                is_error=True,
            )

        return body, None


def _strip_chrome(raw_html: str) -> str:
    """Remove chrome tags + their inner content before markdownify.

    Uses ``BeautifulSoup.decompose()`` so the stripped tags vanish
    entirely — markdownify alone would convert ``<script>alert(1)
    </script>`` into the literal text ``alert(1)``, which is exactly
    the noise the LLM should not see.
    """
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag_name in _STRIP_TAGS:
        for element in soup.find_all(tag_name):
            element.decompose()
    return str(soup)


__all__ = [
    "WebFetch",
    "WebFetchInput",
]
