# Phase 1 Todo

> Supersedes `phase-1-todo.md`. Tracks the 5 M-size tasks defined in
> [plan.md](./plan.md). Old `phase-1-plan.md` / `phase-1-todo.md` are kept for
> history but are no longer the source of truth.

## P1-T1: Production-grade Python project foundation ✅

Done. See [learnings/01-scaffolding.md](../learnings/01-scaffolding.md).

---

## P1-T2: Wire-level protocol types ✅

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
| 2f | ApiStreamEvent hierarchy (TextDelta / MessageComplete / Retry) + 18 tests | ✅ | `5b3741f` |
| 2g | `__init__.py` re-exports + integration tests + coverage gate | ✅ | `84b3c42`+`a53eaae`+`05e01ff` |

**Acceptance**: Module 2 complete when `from openharness.protocols import *` exposes the public API and `pytest --cov=openharness.protocols --cov-fail-under=90` passes.

---

## P1-T3: OpenAI-compatible API client + retries (mocked) ✅

**Strategy**: [decisions/03-api-client-strategy.md](../decisions/03-api-client-strategy.md) — Qwen via DashScope as the Phase 1 test target.

| # | Sub-unit | Status |
|---|---------|--------|
| 3a | Error hierarchy (OpenHarnessApiError + 3 subclasses) + 19 tests | ✅ `f681ce6` |
| 3b | Retry policy (exp backoff + jitter, injectable sleep) + 22 tests | ✅ `fa9af30` |
| 3c.1 | Wire translation pure functions (`to_openai_request` + `_StreamAssembler`) + 22 tests | ✅ `e2332b3` |
| 3c.2 | `OpenAICompatibleApiClient` orchestration + 10 tests (covers retry + error translation end-to-end) | ✅ `5849742` |
| 3d | Retry integration (rate-limited / 5xx / auth retried correctly) — covered by 3c.2 tests | ✅ `5849742` |
| 3e | `__init__.py` re-exports + test_client.py uses public path (= integration verification) | ✅ `fe724cb` |

---

## P1-T4: CLI + real-API end-to-end ✅

**外部约束**：[decisions/05-cli.md](../decisions/05-cli.md) — provider-neutral
env vars、`pydantic-settings`、`typer`、`@pytest.mark.integration` marker。
**实现策略**：[learnings/04-cli.md](../learnings/04-cli.md)。

P1-T4 capability shipped — `oh ask "<prompt>"` 流式输出 + 差异化错误 UX +
集成测试 gated。Coverage 92.83%。详见 commit history.

---

## P1-T5: Phase 1 validation + retrospective ✅ (pending CI push)

- [x] `learnings/phase-1.md` written (跨模块复盘)
- [x] README expanded with "How do I try it?" + project structure refresh
- [x] Overall coverage 92.83% (gate 70%)
- [x] Phase 1 DoD all checked except CI push (see below)

---

## Phase 1 Definition of Done

- [x] `uv sync` clean from fresh clone
- [x] `ruff check && ruff format --check` clean — **note**: `ruff format`
  has a known pre-commit hook version mismatch on
  `tests/cli/test_integration.py` (HEAD format ≠ local ruff 0.15.12 format).
  HEAD style is consistent with pre-commit; will pin pre-commit ruff version
  as a Phase 2 cleanup item.
- [x] `mypy --strict src/ tests/` clean
- [x] `pytest --cov` 92.83% (≥ 70%)
- [x] `oh ask "hi"` streams real response from Provider (Qwen via DashScope)
- [ ] CI green on a clean push (pending user push — branch is ahead of origin)
- [x] README explains install + first run + dev workflow
- [x] `learnings/phase-1.md` written

---

## Phase 2 Pre-flight Cleanup TODOs

来自各 module retro + Phase 1 复盘，进 Phase 2 前 batch 处理：

- [ ] 显式定义 `class SupportsStreamingMessages(Protocol)` (learnings/03 #3)
- [ ] `_FAST_POLICY` 抽到 `tests/api/conftest.py` (learnings/03 #4)
- [ ] `_translate_openai_error` 单独 test file (learnings/03 #6)
- [ ] CI 显式加 `-m "not integration"` flag
- [ ] `decisions/00-env.md` 记录代理端口陷阱 (learnings/01 #3)
- [ ] Pin `.pre-commit-config.yaml` ruff hook 版本，消除 `ruff format` 与
  pre-commit 之间的版本飘移
