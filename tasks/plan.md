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

### P1-T2: Wire-level protocol types 🟡 ~60% DONE

**Description**: Complete Pydantic v2 data model layer for Anthropic Messages API
wire format. After this task, downstream code can construct, validate, and roundtrip
any request shape we send and any response shape we receive.

**Acceptance criteria**:
- [ ] All wire-level types exist as Pydantic models with discriminated union dispatch
- [ ] `from openharness.protocols import ContentBlock, ConversationMessage, ApiMessageRequest, ApiStreamEvent, UsageSnapshot, ToolSpec` works (top-level re-exports)
- [ ] Test coverage on `src/openharness/protocols/` ≥ 90%
- [ ] Real Anthropic API JSON samples (request + response) can be parsed end-to-end
- [ ] `mypy --strict` clean; `ruff check && ruff format --check` clean

**Verification**:
```bash
uv run pytest tests/protocols/ --cov=openharness.protocols --cov-fail-under=90
uv run mypy --strict src/ tests/
```

**Files**: `src/openharness/protocols/{__init__,_base,content,messages,usage,requests,stream_events}.py` + parallel tests

**Sub-units (each = one micro-cycle = one commit)**:
- [x] 2a — toolchain + StrictModel base + package skeleton
- [x] 2b — ContentBlock (4 variants + discriminated union) + tests
- [x] 2c — ConversationMessage + tests
- [x] 2d — UsageSnapshot + tests
- [x] 2e-1 — ApiMessageRequest minimal (model / max_tokens / messages) + tests _(over-split — see Notes)_
- [x] 2e-2 — ApiMessageRequest gains optional system _(over-split)_
- [ ] 2e — **CONSOLIDATE**: complete ApiMessageRequest + ToolSpec + remaining tests (stream / max_tokens validation / tools)
- [ ] 2f — ApiStreamEvent hierarchy (TextDelta / MessageComplete / Retry) + tests
- [ ] 2g — `__init__.py` re-exports + integration tests + coverage gate

**Notes on over-splitting**: Sub-units 2e-1 and 2e-2 demonstrated the TDD rhythm
but were too granular. Going forward, one Pydantic class = one micro-cycle.

---

### P1-T3: Anthropic API client + retries (mocked tests)

**Description**: `AnthropicApiClient` exposes `stream_message(req) → AsyncIterator[ApiStreamEvent]`.
Built against Anthropic's Python SDK; retries with exponential backoff on 429/5xx;
full error hierarchy. All tests use mocked HTTP — no real API key required.

**Acceptance criteria**:
- [ ] Single happy-path streaming call returns ordered events ending in `ApiMessageCompleteEvent`
- [ ] 429 / 500 / 503 responses retried with exponential backoff + jitter (max 3 attempts)
- [ ] Auth failure (401/403) raises `AuthenticationFailure`
- [ ] All paths tested with mocked HTTP responses; ≥ 90% coverage on `api/`

**Verification**:
```bash
uv run pytest tests/api/ --cov=openharness.api --cov-fail-under=90
```

**Files**: `src/openharness/api/{__init__,client,errors,retry}.py` + tests

**Sub-units**:
- [ ] 3a — Error hierarchy (`OpenHarnessApiError` → Auth/RateLimit/Request) + tests
- [ ] 3b — Retry policy (exponential backoff + jitter) + tests with deterministic clock
- [ ] 3c — `AnthropicApiClient` happy-path streaming with mocked SSE + tests
- [ ] 3d — Retry integration: rate-limited responses retried + tests
- [ ] 3e — `__init__.py` re-exports + cross-module integration tests

---

### P1-T4: CLI + real-API end-to-end

**Description**: Replace placeholder CLI with a Typer-based `oh ask "hi"` command
that wires up: load API key from env → construct request → call Anthropic →
stream response to terminal. One integration test gated by `ANTHROPIC_API_KEY`
env var.

**Acceptance criteria**:
- [ ] `uv run oh ask "hi"` produces streamed text from real Anthropic
- [ ] `--model` flag overrides default model
- [ ] Missing `ANTHROPIC_API_KEY` produces a clear error message
- [ ] Integration test (skipped without env var) passes against real API

**Verification**:
```bash
ANTHROPIC_API_KEY=... uv run oh ask "hi"   # human verifies streamed output
uv run pytest tests/cli/                    # all unit tests green
ANTHROPIC_API_KEY=... uv run pytest tests/cli/ -m integration  # real API
```

**Files**: `src/openharness/cli.py` (rewrite), `src/openharness/config/{__init__,settings}.py`,
`tests/cli/test_cli.py`, `tests/cli/test_integration.py`

**Sub-units**:
- [ ] 4a — Config layer (load `ANTHROPIC_API_KEY` from env) + tests
- [ ] 4b — `oh ask` Typer command (no real API) + tests using mocked client
- [ ] 4c — Wiring: real `AnthropicApiClient` + Rich streaming renderer
- [ ] 4d — Integration test against real API (gated by env var)

---

### P1-T5: Phase 1 validation + retrospective

**Description**: Final pass — Phase 1 DoD checklist green, learnings written,
README expanded for first-time contributors.

**Acceptance criteria**:
- [ ] All Phase 1 DoD items in [todo.md](./todo.md) checked off
- [ ] `learnings/phase-1.md` written (Python patterns / product decisions /
  cross-module lessons)
- [ ] README has working "How do I try it?" section with API key setup
- [ ] `uv run pytest --cov` shows ≥ 70% overall coverage
- [ ] CI green on a clean push

**Files**: `learnings/phase-1.md`, `README.md` (expand), any cleanup

---

## Checkpoints

### After P1-T2 (current focus)
- [ ] All protocol types covered, integration tests pass
- [ ] Coverage on protocols/ ≥ 90%
- [ ] **Human review**: are the types ergonomic for Module 3 to use?

### After P1-T3
- [ ] Mocked client tests all pass
- [ ] Retry behavior verified deterministically (no real network)
- [ ] **Human review**: is the AsyncIterator API shape what Module 4 needs?

### After P1-T4
- [ ] First successful `oh ask "hi"` against real Anthropic
- [ ] **Human review**: is the UX clean enough to ship?

### After P1-T5 (Phase 1 complete)
- [ ] Phase 1 DoD all green
- [ ] Retrospective written
- [ ] **Decision point**: enter Phase 2 (Tool Loop) or pause

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Anthropic SDK API changes | Med | Pin version (`anthropic>=0.40,<1.0`); integration test catches breakage |
| Streaming SSE parsing edge cases | Med | Mock real SSE responses captured from API; test reconnection paths |
| API key leak via logs | High | Never log request bodies in default mode; explicit opt-in via env var |
| Network/proxy issues blocking integration tests | Low | Integration test is gated by env var; can be skipped locally and run on a known-good network |

## Open Questions

- For Module 3 retry policy: should we expose the policy as a configurable
  parameter, or hard-code it? **Tentative**: hard-code in v0.1, expose later.
- For Module 4 streaming render: Rich's live `Markdown` re-render on each
  delta vs append-only line-buffered? **Tentative**: append-only for v0.1.
