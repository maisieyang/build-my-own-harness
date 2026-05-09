# Phase 1 Implementation Plan

> Supersedes the earlier `phase-1-plan.md` / `phase-1-todo.md`. Same Phase 1 goal,
> coarser task granularity (5 M-size tasks instead of 40 micro-tasks).
>
> Top-level multi-phase strategy lives in [ARCHITECTURE.md](../ARCHITECTURE.md).

## Overview

**Phase 1 goal**: `oh ask "hi"` → streams a real response from Anthropic, with
production-grade Python toolchain (mypy strict / ruff / pytest / CI / pre-commit).

**Total scope**: ~3-4 weeks of focused work, 5 tasks, ~20-30 commits expected.

## Architecture Decisions

- [decisions/01-scaffolding.md](../decisions/01-scaffolding.md): uv + hatchling,
  mypy strict, ruff, pre-commit (ruff only)
- [decisions/02-protocols.md](../decisions/02-protocols.md): Pydantic v2,
  discriminated union by `type` field, mutable models, subfolder layout
- More land as Modules 3-4 begin

## Task Sizing Principle (revised)

**Definition of "task"**: one complete capability slice that can be independently
verified. Approx 1-2 hours of focused work, 1-5 files, 2-5 commits.

**Definition of "micro-cycle"**: RED → GREEN → COMMIT for **one complete logical
unit** (e.g., one Pydantic class with all its fields + a full test class). NOT
"add one field". The earlier "field-by-field" splitting was over-fragmentation.

## Task List

### P1-T1: Production-grade Python project foundation ✅ DONE

**Description**: Project scaffolding + complete toolchain wired up to CI.

**Acceptance criteria**:
- [x] `uv sync` works from a clean clone
- [x] `ruff check && ruff format --check && mypy --strict src/ && pytest` all green
- [x] `pre-commit install` + hooks run on commit (ruff + hygiene)
- [x] GitHub Actions CI green on push (matrix Python 3.10 / 3.11)

**Files**: `pyproject.toml`, `uv.lock`, `src/openharness/{__init__,__main__,cli}.py`,
`tests/{conftest,test_smoke}.py`, `.gitignore`, `LICENSE`, `README.md`,
`.pre-commit-config.yaml`, `.github/workflows/ci.yml`

**Done in commits**: `4067995` and follow-ups.

---

### P1-T2: Wire-level protocol types ✅ DONE

**Description**: Complete Pydantic v2 data model layer for Anthropic Messages API
wire format. After this task, downstream code can construct, validate, and roundtrip
any request shape we send and any response shape we receive.

**Acceptance criteria**:
- [x] All wire-level types exist as Pydantic models with discriminated union dispatch
- [x] `from openharness.protocols import ContentBlock, ConversationMessage, ApiMessageRequest, ApiStreamEvent, UsageSnapshot, ToolSpec` works (top-level re-exports)
- [x] Test coverage on `src/openharness/protocols/` ≥ 90% (actual: 100%)
- [x] Real Anthropic API JSON samples (request + response) can be parsed end-to-end (`tests/protocols/test_integration.py`)
- [x] `mypy --strict` clean; `ruff check && ruff format --check` clean

**Verification**:
```bash
uv run pytest tests/protocols/ --cov=openharness.protocols --cov-fail-under=90
uv run mypy --strict src/ tests/
```

**Files**: `src/openharness/protocols/{__init__,_base,content,messages,usage,requests,stream_events,tools}.py` + parallel tests

**Sub-units (each = one micro-cycle = one commit)**:
- [x] 2a — toolchain + StrictModel base + package skeleton
- [x] 2b — ContentBlock (4 variants + discriminated union) + tests
- [x] 2c — ConversationMessage + tests
- [x] 2d — UsageSnapshot + tests
- [x] 2e-1 — ApiMessageRequest minimal (model / max_tokens / messages) + tests _(over-split — see Notes)_
- [x] 2e-2 — ApiMessageRequest gains optional system _(over-split)_
- [x] 2e — CONSOLIDATE: ApiMessageRequest with full fields (stream / tools) + ToolSpec landed
- [x] 2f — ApiStreamEvent hierarchy (TextDelta / MessageComplete / Retry) + tests
- [x] 2g — `__init__.py` re-exports + integration tests + coverage gate

**Notes on over-splitting**: Sub-units 2e-1 and 2e-2 demonstrated the TDD rhythm
but were too granular. Going forward, one Pydantic class = one micro-cycle.

---

### P1-T3: API client + retries (mocked tests) ✅ DONE

**Description**: `OpenAICompatibleApiClient` exposes `stream_message(req) → AsyncIterator[ApiStreamEvent]`
(targeting Qwen via DashScope per `decisions/03-api-client-strategy.md`; an Anthropic-native
client is deferred to a later phase). Retries with exponential backoff on 429/5xx; full
error hierarchy. All unit tests use mocked SDK calls — no real API key required.

**Acceptance criteria**:
- [x] Single happy-path streaming call returns ordered events ending in `ApiMessageCompleteEvent`
- [x] 429 / 500 / 503 responses retried with exponential backoff + jitter (max 3 attempts)
- [x] Auth failure (401/403) raises `AuthenticationFailure`
- [x] All paths tested with mocked SDK; coverage on `api/` ≥ 90% (actual: errors 100% / retry 97% / translation 94% / client 95%+)

**Verification**:
```bash
uv run pytest tests/api/ --cov=openharness.api --cov-fail-under=90
```

**Files**: `src/openharness/api/{__init__,client,errors,retry,translation}.py` + parallel tests

**Sub-units**:
- [x] 3a — Error hierarchy (`OpenHarnessApiError` → Auth/RateLimit/Request) + tests
- [x] 3b — Retry policy (exponential backoff + jitter) + tests with deterministic clock
- [x] 3c — `OpenAICompatibleApiClient` happy-path streaming with mocked SDK + tests
- [x] 3c.1 — `translation.py` (Anthropic ↔ OpenAI wire-format anti-corruption layer) + tests
- [x] 3d — Retry integration: rate-limited responses retried + tests
- [x] 3e — `__init__.py` re-exports + cross-module integration tests

**Implementation rationale**: see `learnings/03-api-client.md`.

---

### P1-T4: CLI + real-API end-to-end ✅ DONE

**Description**: Replace placeholder CLI with a Typer-based `oh ask "<prompt>"`
command that wires up: load env via `Settings` → construct request →
call OpenAI-compatible Provider (Qwen via DashScope) → stream response
to terminal with append-only renderer + differentiated error UX. One
integration test gated by `@pytest.mark.integration`.

**Acceptance criteria**:
- [x] `uv run oh ask "hi"` produces streamed text from real Provider
- [x] `--model` flag overrides default model (`qwen-plus`)
- [x] `--max-tokens` flag caps generation length (min 1, default 1024)
- [x] Missing `OPENHARNESS_API_KEY` / `_BASE_URL` produces a clear error
  message (no Python traceback in default mode)
- [x] Integration test (skipped without env vars) passes against real API

**Verification**:
```bash
OPENHARNESS_API_KEY=... OPENHARNESS_BASE_URL=... uv run oh ask "hi"
uv run pytest tests/cli/                                         # unit tests
uv run pytest -m integration                                     # gated real API
```

**Files**: `src/openharness/cli.py`, `src/openharness/_stream_render.py`,
`src/openharness/__init__.py`, `tests/cli/{test_cli,test_render,test_integration,test_smoke}.py`

**Implementation rationale**: see `learnings/04-cli.md`.

---

### P1-T5: Phase 1 validation + retrospective ✅ (pending CI push)

**Description**: Final pass — Phase 1 DoD checklist green, learnings written,
README expanded for first-time contributors.

**Acceptance criteria**:
- [x] All Phase 1 DoD items in [todo.md](./todo.md) checked off (except CI push, see below)
- [x] `learnings/phase-1.md` written — cross-module synthesis (anti-corruption
  layer 实测验证 / 测试名 = 产品契约 / mypy strict 抓真 bug / 工作流两次纠正
  / Python 规则集 / 可迁移到 Phase 2 的 4 个 architecture pattern)
- [x] README has working "How do I try it?" section with API key + env var
  setup, model / max-tokens flag examples, error UX table
- [x] `uv run pytest --cov` shows 92.83% (gate 70%)
- [ ] CI green on a clean push (pending — local branch ahead of `origin/main`,
  user-controlled push)

**Files**: `learnings/phase-1.md`, `README.md` (expanded with Try-it section
and updated project structure), `tasks/{plan,todo}.md` (DoD checked off).

---

## Checkpoints

### After P1-T2 ✅
- [x] All protocol types covered, integration tests pass
- [x] Coverage on protocols/ ≥ 90% (100%)
- [x] **Human review**: types validated as ergonomic — `protocols/` 经受了 T3 (translation) + T4 (CLI) 两轮真实使用而无需返工

### After P1-T3 ✅
- [x] Mocked client tests all pass
- [x] Retry behavior verified deterministically (`_system_random` injectable, `sleep` injectable in `with_retry`)
- [x] **Human review**: AsyncIterator API shape validated — T4 直接消费无适配

### After P1-T4 ✅
- [x] First successful `oh ask "hi"` against real Provider (Qwen via DashScope)
- [x] **Human review**: UX validated — 用户独立验证 `--max-tokens` / `-m` / 默认三种调用 (2026-05-07)

### After P1-T5 (Phase 1 complete) 🟡
- [x] Phase 1 DoD all green (除 CI push)
- [x] Retrospective written (`learnings/phase-1.md`)
- [x] **Decision point**: 进入 Phase 2，契约固化在 [decisions/06-phase-2-boundary.md](../decisions/06-phase-2-boundary.md)
- [x] 4 个失败测试已修（`tests/{config,cli}/conftest.py` autouse fixture 增加 `monkeypatch.chdir(tmp_path)`，让 pydantic-settings 找不到项目根的 `.env`）—— 2026-05-07
- [ ] 待办：CI push 让远端 main 跟上

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Anthropic SDK API changes | Med | Pin version (`anthropic>=0.40,<1.0`); integration test catches breakage |
| Streaming SSE parsing edge cases | Med | Mock real SSE responses captured from API; test reconnection paths |
| API key leak via logs | High | Never log request bodies in default mode; explicit opt-in via env var |
| Network/proxy issues blocking integration tests | Low | Integration test is gated by env var; can be skipped locally and run on a known-good network |

## Open Questions

- ~~For Module 3 retry policy: should we expose the policy as a configurable parameter, or hard-code it?~~
  **Resolved**: `RetryPolicy` is a `frozen=True` dataclass exposed via `api/__init__.py`,
  injectable into `OpenAICompatibleApiClient(retry_policy=...)`. `DEFAULT_POLICY` covers
  the common case; tests inject custom policies for deterministic timing.
- ~~For Module 4 streaming render: Rich's live `Markdown` re-render on each delta vs append-only line-buffered?~~
  **Resolved**: Append-only chosen (D5.5). Rich live re-render deferred to Tier 1
  Print-mode work; the simple renderer composes correctly with `oh ask "..." | tee`.
