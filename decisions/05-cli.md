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

> **Note (2026-05-07)**: 原 D5.5（流式渲染策略）/ D5.6（错误 UX 措辞）/ D5.8
> （Print mode 范围）已移出本文档，归到 `learnings/04-cli.md`。这三条是内部
> 实现策略，不属于"外部约束 + 不可逆决策"——按新工作流应在 build 中 emerge、
> 做完后沉淀到 learnings。

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
