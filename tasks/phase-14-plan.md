# Phase 14 Implementation Plan — Web tools (`WebSearch` + `WebFetch`) + anti-substitution system-prompt guard

> Boundary contract: [`decisions/29-phase-14-boundary.md`](../decisions/29-phase-14-boundary.md).
> First net-new tool ship since Phase 6 (`SpawnAgent`). Driven by
> v0.2.0 dogfood: an LLM asked for external research substituted
> `Read`/`Grep` on local files and confabulated findings. Fix is
> two-pronged — register the missing tools + harden the default
> system prompt to refuse substitution when web is unavailable.

## Overview

**Phase 14 goal**: ship `WebSearch` + `WebFetch` as opt-in tools
behind `--enable-web`, ship the Tavily provider as the v1 default
behind a `WebSearchProvider` Protocol, and update the default
system prompt to add the anti-substitution paragraph whenever web
tools are NOT registered.

The **cross-cutting invariant** (the 8th compounding test of the
abstraction-first pattern):

- `markdown_store / skills / commands / bundles / plugins / mcp /
  permissions / prompts / protocols / memory / hooks` — zero diff
  (Phase 13 hit 11/11 — Phase 14 must hold this)
- `services/summarize.py` — zero diff (no 8th consumer yet; web
  tools do their own httpx-based dispatch)
- `services/snapshot.py` — zero diff (snapshots are unaffected)
- Existing 6 tools (`Read` / `Write` / `Edit` / `Bash` / `Grep` /
  `Agent`) — byte-identical
- `oh tools list` (without `--enable-web`) — byte-identical to
  v0.2.0 output

Only `cli.py` (one new flag + one new conditional tool registration
block), `config/settings.py` (one new nested model), `prompts/`
(one additive kwarg + one paragraph), `pyproject.toml` (one new
dep), and the two new `tools/web_*.py` files get touched.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/29-phase-14-boundary.md`](../decisions/29-phase-14-boundary.md) | D29.1 two tools (WebSearch + WebFetch, separate); D29.2 Tavily as v1 default behind `WebSearchProvider` Protocol; D29.3 opt-in via `--enable-web`; D29.4 nested `WebSettings` with generic `api_key`; D29.5 httpx + markdownify (new dep); D29.6 system-prompt anti-substitution paragraph when OFF + positive guidance when ON; D29.7 tool errors are LLM-visible `ToolResult(success=False)`, never raise to engine |

---

## Task list

### P14-T1: Provider abstraction + `TavilySearchProvider` impl 🔜 NEXT

**Description**: Land the `WebSearchProvider` Protocol and ship the
`TavilySearchProvider` implementation. Wire it behind a new nested
`WebSettings` block under `Settings`. No tool exposure yet — pure
infrastructure landing.

**Acceptance**:

- [ ] `src/openharness/tools/web_search.py` defines `WebSearchProvider`
  Protocol with `async def search(query: str, num_results: int) ->
  list[WebSearchResult]`.
- [ ] `WebSearchResult` is a frozen dataclass: `url: str` + `title: str`
  + `snippet: str`. Pydantic-validated.
- [ ] `TavilySearchProvider` implements the Protocol, POSTs to
  `https://api.tavily.com/search`, parses response into
  `WebSearchResult` list.
- [ ] `WebSettings` nested under `Settings`:
  - `enabled: bool = False` (`OPENHARNESS_WEB__ENABLED`)
  - `search_provider: Literal["tavily"] = "tavily"`
    (`OPENHARNESS_WEB__SEARCH_PROVIDER`)
  - `api_key: SecretStr | None = None`
    (`OPENHARNESS_WEB__API_KEY`)
  - `fetch_timeout_seconds: float = 10.0`
  - `fetch_max_bytes: int = 5_000_000`
  - `fetch_default_max_chars: int = 10_000`
- [ ] `pyproject.toml`: add `markdownify>=0.11,<1.0` to
  `[project.dependencies]`. (`httpx` already present via openai
  SDK.)
- [ ] Stub-provider unit test: a `_StubProvider` returns canned
  results; verifies `WebSearchProvider` Protocol contract.
- [ ] Tavily integration test gated by
  `OPENHARNESS_WEB__API_KEY` env (mirrors real-LLM gating pattern
  in `tests/cli/test_integration.py`).
- [ ] Provider error categories: `WebSearchProviderError` for
  bad-request / quota / network — non-retryable surfaced to caller.
- [ ] ruff + mypy --strict src clean.

**Predicted retro questions**:
- Did the Protocol abstraction shape fit Tavily cleanly (no
  Tavily-specific kwargs leaking into Protocol)?
- How long did the Tavily account setup + key acquisition take in
  practice? Worth noting as a "user setup" UX point.

---

### P14-T2: `WebSearch` tool

**Description**: Build the `WebSearch` tool on top of the
`WebSearchProvider` Protocol from P14-T1. Tool input gets Pydantic-
validated; output is a markdown-formatted result list returned as
`ToolResult(success=True)` or a structured error `ToolResult(success=
False)` per D29.7.

**Acceptance**:

- [ ] `WebSearchInput(BaseModel)` with `query: str` (required, ≥1
  char), `num_results: int = 5` (ge=1, le=10).
- [ ] `WebSearch(BaseTool[WebSearchInput])` implements:
  - `name = "WebSearch"`
  - `description = "Search the web for information. Returns up to
    num_results URLs with title + snippet. Use to discover URLs;
    follow up with WebFetch to read specific pages in detail."`
  - `read_only = True` (matches Read/Grep tool family)
  - `async def execute(input, context) -> ToolResult` invokes the
    configured provider and formats result list as markdown.
- [ ] Output format example:
  ```
  Search results for "site:anthropic.com claude":

  1. **Claude 3.5 Sonnet** (anthropic.com/news/claude-3-5-sonnet)
     Anthropic's most intelligent model so far...

  2. **Building agents with Claude** (anthropic.com/agents)
     ...
  ```
- [ ] Error categories surface as `ToolResult(success=False, ...)`:
  - "WebSearch is not enabled — set `--enable-web` and configure
    `OPENHARNESS_WEB__API_KEY`."
  - "Provider quota exhausted — retry later or upgrade plan."
  - "Provider request failed: HTTP <status>"
- [ ] Tool registered into `ToolRegistry` only when
  `WebSettings.enabled` is True (skipped silently otherwise).
- [ ] Unit tests use `_StubProvider` from P14-T1; no real network.

**Predicted retro questions**:
- Was the markdown output format LLM-friendly? Should snippet
  length be capped?
- Did 5-result default feel right vs 3 or 10? Tavily latency at
  each level worth measuring.

---

### P14-T3: `WebFetch` tool

**Description**: Build `WebFetch` to GET a URL, strip nav/script/
style, render to markdown via `markdownify`, truncate with the same
`[+N chars]` pattern as Phase 4 microcompact. No dependence on the
`WebSearchProvider` — pure httpx + markdownify.

**Acceptance**:

- [ ] `WebFetchInput(BaseModel)`:
  - `url: HttpUrl` (Pydantic-validated; must be http/https)
  - `max_chars: int = 10_000` (ge=100, le=100_000)
- [ ] `WebFetch(BaseTool[WebFetchInput])`:
  - `name = "WebFetch"`
  - `description = "Fetch a URL and return its content as
    markdown. Use to read specific pages discovered via WebSearch
    or provided by the user. Honors max_chars (default 10000)."`
  - `read_only = True`
  - GETs the URL with `User-Agent: OpenHarness/<version>
    (+webfetch)`, timeout from settings (default 10s).
  - Streams body; aborts if > `fetch_max_bytes` (default 5MB).
  - Strips `<script>`, `<style>`, `<nav>`, `<aside>`,
    `<header>`, `<footer>` before markdownify conversion.
  - Truncates output at `max_chars` with `[+N chars truncated]`
    suffix (Phase 4 pattern).
- [ ] Error categories as `ToolResult(success=False, ...)`:
  - "URL timed out after Xs — retry with smaller content or try
    a different source."
  - "URL returned HTTP 404 — page not found."
  - "URL returned HTTP 5xx — retryable."
  - "URL body exceeded 5MB cap — page too large to fetch."
  - "Invalid URL — must be http:// or https://."
  - "Markdownify failed to render the page."
- [ ] Tool registered only when `WebSettings.enabled` is True.
- [ ] Unit tests use a small bundled HTML fixture file (not network)
  to verify markdownify wiring + script/style stripping + truncation.
- [ ] Integration test (real httpx GET): hits an httpbin-like
  endpoint, gated by env var to skip in CI.

**Predicted retro questions**:
- Did 10s timeout / 5MB cap / 10k char default hit the right
  90th-percentile balance, or did dogfood expose them as too
  tight / too loose?
- Did markdownify handle code blocks (`<pre>`) well? Tables?
- Were there sites where stripping `<header>`/`<footer>` lost
  necessary context (e.g. attribution lines)?

---

### P14-T4: Engine wiring + system prompt + CLI flag

**Description**: Land the `--enable-web` CLI flag on both `oh ask`
and `oh chat`, conditionally register the two new tools, and update
the default system prompt builder to inject the anti-substitution
paragraph when web is OFF (the bug fix) or the positive guidance
paragraph when web is ON. THE bug fix (D29.6) lives here.

**Acceptance**:

- [ ] `oh ask --enable-web` and `oh chat --enable-web` flags wired
  via typer (mirrors `--enable-plugins` / `--enable-memory`).
- [ ] CLI startup conditionally registers `WebSearch` + `WebFetch`
  into `ToolRegistry` when `settings.web.enabled` OR
  `--enable-web` flag is set.
- [ ] `build_system_prompt(...)` gains additive kwarg
  `web_enabled: bool = False`. When `True`, appends the positive
  guidance paragraph (D29.6 text). When `False`, appends the
  anti-substitution paragraph (D29.6 text).
- [ ] Byte-identity guard test: `build_system_prompt(...)` with
  `web_enabled=False` produces output identical to v0.2.0 EXCEPT
  for the new paragraph. (Phase 10 D28.4 precedent — snapshot
  current Phase 13 output, diff after Phase 14 should be exactly
  the inserted paragraph.)
- [ ] System prompt test against stub LLM: when shown the OFF
  prompt and asked "what is the latest AI news?", the stub LLM
  catalog response should include the "no internet" decline path.
  (Acknowledged as best-effort — stub LLM behavior is not
  deterministic; flag this as a Phase 14 retro evaluation point.)
- [ ] `oh tools list --enable-web` shows `WebSearch` + `WebFetch`;
  `oh tools list` (no flag) does not (byte-identical to v0.2.0).
- [ ] Backward-compat for users without `--enable-web`: behavior
  identical to v0.2.0 except for the new system-prompt paragraph.

**Predicted retro questions**:
- Did the system-prompt guard alone reduce substitution behavior
  (test with `--enable-web` OFF and a research-style prompt)?
  This is the cleanest A/B for whether the prompt-side fix
  matters relative to the tool-side fix.
- Did `build_system_prompt` kwarg explosion become a smell? It
  now has at least `claude_md_content`, `memory_manifest`, and
  `web_enabled` — three kwargs. Are we approaching the boundary
  where the prompt builder needs a config-object refactor?

---

### P14-T5: E2E + cross-cutting invariant verification + retro

**Description**: End-to-end test the full workflow with real Tavily
key (gated). Verify the 11 protected directories + 6 existing tools
+ `services/summarize.py` + `services/snapshot.py` all stay byte-
identical. Write `learnings/phase-14.md` retro. Add CHANGELOG
[Unreleased] entry sketching v0.3.0.

**Acceptance**:

- [ ] E2E test: `oh ask --enable-web "What is the latest news
  about the Tavily search API?"` — verifies WebSearch is invoked,
  results returned, LLM uses them to compose answer. Gated by
  `OPENHARNESS_WEB__API_KEY`.
- [ ] E2E test: same query WITHOUT `--enable-web` — verifies LLM
  declines politely per system prompt guard. (Best-effort with
  stub LLM; real LLM gated.)
- [ ] E2E test: `oh ask --enable-web "Fetch this URL and
  summarize: https://httpbin.org/html"` — verifies WebFetch is
  invoked, content returned, summarization works.
- [ ] Cross-cutting invariant verification (git log diff):
  - 11 protected directories: zero diff between Phase 13 close
    (`7c306fe` ancestor) and Phase 14 close.
  - `services/summarize.py`: zero diff.
  - `services/snapshot.py`: zero diff.
  - All 6 existing tools' source files: zero diff.
- [ ] `learnings/phase-14.md` retro covering:
  - Quantitative table (LoC, tests, time, dep additions vs Phase
    13)
  - 7th-consumer-style observation: did the
    `WebSearchProvider` Protocol abstraction earn its keep
    (could a Brave provider land in <30% of Tavily's LoC)?
  - System-prompt-vs-tool A/B observation from T4.
  - Phase 15+ candidates (caching / PDF / JS rendering /
    prompt-injection quarantine / Brave swap).
- [ ] `CHANGELOG.md` `[Unreleased]` entry sketches v0.3.0 with:
  - Added: WebSearch + WebFetch tools, opt-in via `--enable-web`
  - Added: TavilySearchProvider (Brave/Serper deferred to v0.4+)
  - Added: anti-substitution system prompt guard (default-on, no
    flag — even users without `--enable-web` get it)
  - Added: `markdownify` runtime dep
  - Quality bars: test count growth, coverage held, mypy + ruff
    clean.

---

## Checkpoints

| After | What MUST be true before proceeding |
|---|---|
| **P14-T1** | Protocol + Tavily impl wired; stub-provider tests GREEN; `markdownify` dep landed; no tool exposure yet. |
| **P14-T2** | `WebSearch` tool dispatches end-to-end via stub provider; `ToolResult` shape correct; conditional registration verified. |
| **P14-T3** | `WebFetch` GETs HTML fixture, strips nav/script, markdownifies, truncates correctly. |
| **P14-T4** | `--enable-web` flag wired in both `oh ask` + `oh chat`; system prompt byte-identical to v0.2.0 except the new paragraph. THE bug fix is live. |
| **P14-T5** | All E2E tests GREEN; 11 protected dirs zero-diff verified; retro + CHANGELOG written. |

---

## Risks

| Risk | Mitigation |
|---|---|
| Tavily account setup blocks the build (need real key) | Stub provider lets P14-T1/T2 + most of T3/T4 ship without key; T5 E2E is the only Tavily-required step |
| markdownify renders messy output on JS-heavy sites | Strip script/style/nav/aside/header/footer before convert; cap output at `max_chars`; LLM can ask for smaller cap |
| Anti-substitution prompt doesn't actually deter the LLM | Best-effort; retro evaluates; tool-side fix (T1-T4) is the harder line of defense regardless |
| `build_system_prompt` kwarg list grows unwieldy | Acknowledged; flag in retro; refactor candidate for Phase 15+ |
| Existing system-prompt tests break on new paragraph | Snapshot tests get updated; byte-identity guard catches unintended changes only |
| New dep `markdownify` introduces CVE down the road | Pin range; review quarterly |

## Risks specifically NOT mitigated (Phase 15+)

- **Prompt injection from fetched content.** `WebFetch` returns
  text directly; if a page contains "ignore previous instructions",
  the LLM might follow it. Trusting Phase 3 permissions + LLM
  refusal training for now. Phase 15+ may quarantine fetched
  content in a `tool_result` block tagged "external untrusted".
- **Caching.** No dedup of repeated fetches. Defer.
- **Rate limiting against single domains.** Defer.

---

## Pointers

- **Test parity with existing tools**: read `tests/tools/test_read.py`
  + `tests/tools/test_bash.py` to mirror style.
- **Sub-agent recursion**: `WebSearch` + `WebFetch` should work
  inside `SpawnAgent` — sub-agents inherit the tool catalog. Test
  this in T5.
- **Permission system interaction**: web tools are read-only
  network operations; permission checker should let them through
  without prompt by default (matches Grep behavior). Verify
  during T2/T3.
- **CHANGELOG `[Unreleased]` will become `[0.3.0]` at release.**
  Tag will be `v0.3.0`. Compare link footer needs updating.
