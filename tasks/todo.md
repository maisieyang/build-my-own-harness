# Phase 1 Todo

> Supersedes `phase-1-todo.md`. Tracks the 5 M-size tasks defined in
> [plan.md](./plan.md). Old `phase-1-plan.md` / `phase-1-todo.md` are kept for
> history but are no longer the source of truth.

## P1-T1: Production-grade Python project foundation ✅

Done. See [learnings/01-scaffolding.md](../learnings/01-scaffolding.md).

---

## P1-T2: Wire-level protocol types 🟡

**Decisions**: [decisions/02-protocols.md](../decisions/02-protocols.md)

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 2a | Toolchain + StrictModel base + skeleton | ✅ | `eaab8b7` |
| 2b | ContentBlock (4 variants + discriminator) + tests | ✅ | `346979c` |
| 2c | ConversationMessage + tests | ✅ | `6f96fa6` |
| 2d | UsageSnapshot + tests | ✅ | `f334542` |
| 2e-1 | ApiMessageRequest minimal | ✅ over-split | `4fa3ea6` |
| 2e-2 | ApiMessageRequest + system | ✅ over-split | `f6a7975` |
| 2e | Complete ApiMessageRequest + ToolSpec (stream / tools / max_tokens validation) + 14 tests | ✅ | `7f96f06` |
| **2f** | **ApiStreamEvent hierarchy** (TextDelta / MessageComplete / Retry) + tests | 🟡 **NEXT** | — |
| 2g | `__init__.py` re-exports + integration tests + coverage gate ≥ 90% | ⏸ | — |

**Acceptance**: Module 2 complete when `from openharness.protocols import *` exposes the public API and `pytest --cov=openharness.protocols --cov-fail-under=90` passes.

---

## P1-T3: Anthropic API client + retries (mocked) ⏸

| # | Sub-unit | Status |
|---|---------|--------|
| 3a | Error hierarchy + tests | ⏸ |
| 3b | Retry policy (exponential backoff + jitter) + tests | ⏸ |
| 3c | `AnthropicApiClient` happy-path streaming with mocked SSE + tests | ⏸ |
| 3d | Retry integration with rate-limited responses + tests | ⏸ |
| 3e | `__init__.py` re-exports + cross-module integration tests | ⏸ |

---

## P1-T4: CLI + real-API end-to-end ⏸

| # | Sub-unit | Status |
|---|---------|--------|
| 4a | Config layer (`ANTHROPIC_API_KEY` from env) + tests | ⏸ |
| 4b | `oh ask` Typer command + tests with mocked client | ⏸ |
| 4c | Wiring real client + Rich streaming renderer | ⏸ |
| 4d | Integration test against real API (gated by env var) | ⏸ |

---

## P1-T5: Phase 1 validation + retrospective ⏸

- [ ] `learnings/phase-1.md` written
- [ ] README expanded with first-run instructions
- [ ] Overall coverage ≥ 70%
- [ ] Phase 1 DoD all checked

---

## Phase 1 Definition of Done

- [ ] `uv sync` clean from fresh clone
- [ ] `ruff check && ruff format --check` clean
- [ ] `mypy --strict src/ tests/` clean
- [ ] `pytest --cov` ≥ 70%
- [ ] `oh ask "hi"` streams real response from Anthropic
- [ ] CI green on a clean push
- [ ] README explains install + first run + dev workflow
- [ ] `learnings/phase-1.md` written

---

## Cleanup TODOs

- [ ] After P1-T2 done: `git rm tasks/phase-1-plan.md tasks/phase-1-todo.md` and update any references in `learnings/01-scaffolding.md` if needed
