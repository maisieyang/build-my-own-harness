# Decision 05 — CLI + Real-API End-to-End

- **Date**: 2026-04-29
- **Phase / Module**: Phase 1 / P1-T4
- **Status**: Decided

## Context

P1-T4 is Phase 1's "go-live moment" — the first time `oh ask "hi"` actually
hits an LLM and streams a response back. It's a thin layer (~200 lines)
that wires four already-built pieces together:

```
env vars  ──►  Settings (pydantic-settings)
                    │
                    ▼
              AsyncOpenAI(api_key, base_url)
                    │
                    ▼
         OpenAICompatibleApiClient   (P1-T3)
                    │
                    ▼
             ApiMessageRequest        (P1-T2)
                    │
                    ▼  AsyncIterator[ApiStreamEvent]
                    │
              streaming renderer
                    │
                    ▼  terminal
```

This module is also the **second real test** of our anti-corruption design:
if `protocols/` and `api/` are right, the CLI should be boring. If anything
is wrong, the pain will surface here.

## Decisions

### D5.1 — Env var naming: **provider-neutral with `OPENHARNESS_` prefix**

- `OPENHARNESS_API_KEY` — the API key (required)
- `OPENHARNESS_BASE_URL` — the OpenAI-compatible base URL (required for
  DashScope; optional for vanilla OpenAI later)
- `OPENHARNESS_MODEL` — default model name (optional; CLI `--model` overrides)

**Why not `DASHSCOPE_API_KEY`?** Phase 2 will likely add an Anthropic-native
client. If we hard-code provider names into env vars, every Provider
addition forces a config-layer rewrite. Provider-neutral names keep the
contract stable across Providers; the **value** of `BASE_URL` is what
selects the provider, not the variable name.

**Why not multi-name compatibility (`DASHSCOPE_API_KEY` || `OPENAI_API_KEY` ||
…)?** Premature compatibility. We have one Provider in Phase 1 — pick one
clean name, document it, move on. Add aliases only when a real second
Provider arrives.

**Trade-off**: Users coming from DashScope docs see `DASHSCOPE_API_KEY`;
they have to read our README to know we use a different name. Acceptable
for an OSS learning harness; would revisit for a public release.

### D5.2 — Config loading: **`pydantic-settings` BaseSettings**

- New dep: `pydantic-settings>=2.1`
- New module: `src/openharness/config/settings.py` defining a `Settings` class
- `SettingsConfigDict(env_prefix="OPENHARNESS_", env_file=".env",
  env_file_encoding="utf-8")`
- Required fields with no default → pydantic raises a clear validation
  error if `OPENHARNESS_API_KEY` is missing

**Why not hand-rolled `os.environ`?**
- We already have Pydantic v2 in the project — adding `pydantic-settings`
  is consistent
- mypy strict-friendly out of the box (no `cast` / `Any` ceremony)
- Validation happens at config load, not lazily at first use → fail fast
- `.env` file support comes free, useful for local dev

**Trade-off**: One more dep; one more concept to learn. Both small.

### D5.3 — Default model: **`qwen-plus`**

- Balanced between `qwen-turbo` (cheap, weaker) and `qwen-max` (expensive,
  strongest)
- Documented in `Settings` as the default; users can override via env
  (`OPENHARNESS_MODEL`) or CLI (`--model`)

### D5.4 — `--model` flag: **supported**

- `oh ask "hi" --model qwen-max` overrides settings default
- Resolution order: CLI `--model` > env `OPENHARNESS_MODEL` > settings default

### D5.5 — Streaming render: **append-only `print(end="", flush=True)`**

- For each `ApiTextDeltaEvent`: print `event.delta` with no newline, flushed
- For `ApiMessageCompleteEvent`: print final newline + (optionally) usage stats
- For `ApiRetryEvent`: print to stderr with a short `[retry n/N: <reason>]` notice

**Why not Rich `Live` markdown re-render?**
- Rich `Live` re-renders the whole markdown on every delta — flicker, complexity, edge cases (terminal resize, scrollback)
- Append-only is what `cat`, `curl --no-buffer`, `ssh`, every classic Unix
  tool does — and it composes with pipes (`oh ask "..." | tee out.txt`)
- Markdown re-rendering belongs in Tier 1 (proper Print mode) where we'd
  also handle JSON output, --output flags, etc.
- **Phase 1 is "first signal" — boring is good**

**Trade-off**: Markdown lists/headers won't render formatted in Phase 1.
Acceptable; cleartext is fine for a harness's first run.

### D5.6 — Error UX: **differentiated by exception type**

- `OpenHarnessApiError` subclasses are user-facing — each gets a tailored hint:
  - `AuthenticationFailure` → "Set `OPENHARNESS_API_KEY` (got HTTP 401 from <provider>)."
  - `RateLimitFailure` → "Provider rate-limited the request after N retries. Try again in a moment."
  - `RequestFailure` → "Provider returned HTTP <status>: <message>"
  - `pydantic.ValidationError` from `Settings` → "Configuration error: <field>: <reason>"
- All errors → exit code 1, message to stderr, no Python traceback (unless
  `--debug` flag set; deferred to Tier 1)

**Why not a single generic "<error>: <message>"?**
- CLI is the **user-perceived layer**. The user doesn't see our exception
  hierarchy; they see what we print. Differentiated hints turn each error
  into a "next step" instead of a wall.
- Effort is small (1-line hint per type); value is high.

### D5.7 — Integration test gating: **`@pytest.mark.integration` marker**

- Register `integration` marker in `pyproject.toml` `[tool.pytest.ini_options]`
- `tests/cli/test_integration.py::test_real_qwen_streaming_e2e` carries the marker
- Test body: `pytest.skipif(not os.environ.get("OPENHARNESS_API_KEY"))` to skip
  silently when no key is present
- Default `pytest` run does **not** include the marker (unit tests only)
- Manual / CI integration run: `uv run pytest -m integration`

**Why marker over a separate directory?**
- Markers are pytest's idiomatic mechanism for opt-in test classes
- Co-locating integration test with unit tests in `tests/cli/` keeps related
  tests discoverable
- CI can run `-m "not integration"` for the default pipeline and an
  `integration` job (with the env var as a secret) for nightly / on-demand

### D5.8 — Print mode scope: **streaming text only**

- Phase 1 ships only the streaming text path
- `--output json` / `--output text` / proper Print mode = **Tier 1, deferred**
- Reasoning: Phase 1's bar is "user can run `oh ask "hi"` and see a
  streamed response". JSON, transcript-style output, format flags expand
  scope without adding to that bar.

## Sub-unit shape (preview)

| # | Sub-unit | Files (rough) | Test focus |
|---|----------|---------------|------------|
| 4a | Config layer (Settings + loading) | `src/openharness/config/{__init__,settings}.py` + tests | env var loading / missing key error / `.env` precedence |
| 4b | `oh ask` Typer command (mocked client) | `src/openharness/cli.py` (rewrite) + `tests/cli/test_cli.py` | flag parsing / model override / output capture |
| 4c | Real client wiring + streaming renderer | `src/openharness/cli.py` (extend), `src/openharness/_stream_render.py` (new) + tests | event → terminal mapping / retry-event stderr / error UX |
| 4d | Integration test against real Qwen | `tests/cli/test_integration.py` + `pyproject.toml` marker | gated streaming round-trip |

Detailed task list: see [tasks/todo.md](../tasks/todo.md).

## Open Questions

- **Config 4-level priority (CLI > ENV > File > Default) from REFERENCE §7**:
  P1-T4 only does the ENV layer. The 4-level layered config is Tier 1
  hardening — we'll revisit when we add the file layer.
- **Multi-Provider env var aliasing**: when Phase 2 adds a second
  Provider, we'll likely add `OPENHARNESS_PROVIDER` (qwen / anthropic /
  ...) and route to the right client. Today's contract stays.
- **`--debug` flag for raw tracebacks**: deferred. CLI errors stay clean
  in Phase 1; debug mode lands when we add observability.
