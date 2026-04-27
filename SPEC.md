# OpenHarness — Project Specification

> The project's canonical contract — **what we're building, what's in scope,
> and how we behave while building it.**
>
> Companion documents:
>
> - [ARCHITECTURE.md](./ARCHITECTURE.md) — multi-phase strategy (tier division,
>   dependency graph, phase ordering)
> - [REFERENCE.md](./REFERENCE.md) — OpenHarness reverse-engineered reference
>   (the project we draw inspiration from; **not** the contract for our build)
> - [decisions/](./decisions/) — per-decision trade-off records
> - [tasks/plan.md](./tasks/plan.md) + [tasks/todo.md](./tasks/todo.md) —
>   current phase plan and running todo
>
> If you only have 5 minutes to onboard, read this file. If you have 30 minutes,
> read this + ARCHITECTURE.md + the latest decision in `decisions/`.

---

## 1. Objective

Build a **production-grade Python harness for LLM agents** from scratch as a
deliberate learning project, with two co-equal goals:

1. **Production deliverable** — a Python CLI that streams real responses from
   Anthropic, with mypy strict / ruff / pytest / CI / pre-commit / coverage
   gates
   - Modeled after Claude Code's harness pattern (Tool-Loop, `stop_reason`-driven)
   - Time horizon: 2-3 months across 7 phases (see
     [ARCHITECTURE.md](./ARCHITECTURE.md))
2. **Capability training** — become a domain expert in agent harness design
   and a product engineer who can rationalize architectural decisions from
   first principles

**Target users of the resulting harness**:

- Solo developers who want a customizable CLI agent
- Engineers studying agent harness internals (the codebase IS the
  documentation)

**Target user of this learning project**: the author. This is **not** aimed
at being a published OSS framework competing with Claude Code or LangGraph.

**Out of scope** (locked in
[ARCHITECTURE.md "Out of Scope"](./ARCHITECTURE.md)):
React TUI, multi-platform chat gateway, Autopilot, voice / Vim modes, all
23 providers (only 2-3 selected), full multi-agent swarm.

---

## 2. Commands

The harness ships a single CLI with three command-name aliases (`oh`,
`openharness`, `openh`). Phase 1 delivers the core invocation; subsequent
phases add subcommands.

### Phase 1 (current target)

```bash
oh ask "<prompt>"                              # Stream a single Anthropic response
oh ask --model claude-3-5-sonnet "<prompt>"    # Override default model
oh --version
oh --help
```

### Phase 2-3 (Tool Loop + Hardening)

```bash
oh ask "<prompt>"                              # gains tool-use loop (Read/Bash/Grep/Edit/Write)
oh chat                                        # interactive REPL
oh tools list                                  # list registered tools
oh tools show <name>                           # show tool schema
```

### Phase 5+ (Extensibility)

```bash
oh mcp add <server-spec>                       # register an MCP server
oh mcp list                                    # list registered MCP servers
oh /<slash-command>                            # invoke a slash command
oh skill run <skill-name>                      # run a skill
oh config show                                 # show effective config
oh config edit                                 # open ~/.config/openharness/config.toml
```

### Auth

API key via env var (`ANTHROPIC_API_KEY`) for v0.1; later phases may add
keyring-backed profile management
(see [ARCHITECTURE.md Tier 0 — Auth](./ARCHITECTURE.md)).

---

## 3. Project Structure

See [README.md `Project structure`](./README.md#project-structure) for the
canonical tree. Roles in summary:

| Path | Role |
|------|------|
| `src/openharness/` | Source (`src` layout — must `uv sync` before import) |
| `tests/` | pytest suite, mirrors `src/` structure |
| `decisions/<NN>-<topic>.md` | Decision records — one per non-trivial trade-off |
| `learnings/<NN>-<module>.md` | Per-module retrospectives |
| `tasks/plan.md` + `tasks/todo.md` | Current phase plan + running todo |
| `docs/ideas/` | Blog drafts and ideation outputs |
| `docs/learning/` | Living learning resources (book lists etc.) |
| `ARCHITECTURE.md` | Multi-phase strategy (macro) |
| `REFERENCE.md` | OpenHarness reverse-engineered reference (study source) |
| `SPEC.md` | This file — project specification |

---

## 4. Development Workflow

The project flows through five layers of artifact, each at a different time
scale and answering a different question. Understanding which layer you're
operating in (and which one you are *not*) is essential.

### Data flow

```
   外部参考                      内部产物                       工具触发

REFERENCE.md  (OpenHarness 完整逆向，固定的输入)
       │
       │ /spec  ←━━━━━━━━━━━━━━━━━━━━━━━━━━━ ① 生成"做什么+不做什么+行为"
       ▼
SPEC.md       (本文档——What 是什么 + 边界 + 测试 + 行为)
       │
       │ /make-plan (项目尺度) ←━━━━━━━━━━━━━ ② 生成"分阶段战略地图"
       ▼
ARCHITECTURE.md (战略 plan——Tier / Phase 顺序 / 依赖图)
       │
       │ /make-plan (Phase 尺度) ←━━━━━━━━━━━ ③ 当前 Phase 的战术 plan
       ▼
tasks/plan.md ←→ tasks/todo.md (5 任务 + 检查点 / 运行清单)
       │
       │ /build  ←━━━━━━━━━━━━━━━━━━━━━━━━━━ ④ 实施每个任务
       ▼
src/  +  tests/  +  commits

       Module 完成时
       ▼
learnings/<NN>.md (复盘——下一次做时的知识)

       任何"我选了 A 而不是 B"的瞬间
       ▼
decisions/<NN>.md (横切的 — Why 选 X 不选 Y)
```

### Layer roles

| 文档 | 时间尺度 | 回答的问题 | 工具命令 |
|------|---------|----------|--------|
| **REFERENCE.md** | 不变 | "OpenHarness 是什么样" | （外部输入，没有命令） |
| **SPEC.md** | 项目级（2-3 月） | "我要做什么 / 不做什么 / 怎么做" | `/spec` |
| **ARCHITECTURE.md** | 项目级（多 Phase） | "分多少阶段？什么顺序？依赖？" | `/make-plan`（项目尺度） |
| **tasks/plan.md** + **todo.md** | Phase 级（1-3 周） | "本 Phase 拆成几个任务？验收？" | `/make-plan`（Phase 尺度） |
| **src/** + tests + commits | 任务级（小时） | "代码长什么样" | `/build` |

### Why these are different documents

它们看起来都在"做计划"，但**回答的问题完全不同**：

- **SPEC** —— 合同性的："任何在做的人都要遵守这些规则"
- **ARCHITECTURE** —— 战略性的："我们大概分 7 个阶段做，先 A 后 B 因为 B
  依赖 A"
- **tasks/plan** —— 战术性的："现在这个阶段我有 5 个具体任务，每个验收
  什么样"

**不能用一份文档承担三种角色**——会变成怪物（要么太宏观无法执行，
要么太微观看不到全局）。

### Workflow loop

每完成一层，回到上一层 review：

```
完成一个任务（=  RED → GREEN → COMMIT 一轮）
   ↓
完成一个 Module（多个任务）
   ↓ 写 learnings/<NN>.md，更新 todo
完成一个 Phase（多个 Module）
   ↓ 写 learnings/phase-N.md，归档当前 tasks/plan.md，
   ↓ 重新 /make-plan 下个 Phase 的战术任务
完成整个项目
   ↓ 写 learnings/project.md，可能更新 SPEC.md
```

**SPEC.md 应该很少变**。每次变都要写
`decisions/<NN>-spec-revision.md` 解释为什么——它是契约，频繁修改 = 契约
失效。

---

## 5. Code Style

Single source of truth: `pyproject.toml`. Decisions documented in
[decisions/01-scaffolding.md](./decisions/01-scaffolding.md) and
[decisions/02-protocols.md](./decisions/02-protocols.md). Highlights:

- **Python 3.10 baseline** (CI tests 3.10 + 3.11)
- **`mypy --strict`** — all functions fully typed; no implicit `Any`
- **`ruff check && ruff format --check`** — replaces flake8 / black / isort /
  pyupgrade
- **`from __future__ import annotations`** in every Python file (PEP 604 union
  syntax on 3.10)
- **`src/` layout** — package importable only after editable install
- **Pydantic v2** with `extra="forbid"` + `validate_assignment=True` for all
  protocol models (see `src/openharness/protocols/_base.py`)
- **Discriminated unions** via `Annotated[..., Field(discriminator="type")]`
  for any tagged-union shape (matches Anthropic wire format 1:1)
- **Async-first**: all I/O is `async def`; loops are async generators /
  `AsyncIterator`
- **No comments unless WHY is non-obvious** — names + tests are documentation
- **`raise SystemExit(main())`** as entry-point idiom

---

## 6. Testing Strategy

The previous artifacts lacked a unified statement here. **This is the
canonical testing contract.**

### Test pyramid

```
        ╱╲
       ╱  ╲       E2E (~5%) — `oh ask` against real Anthropic API
      ╱────╲       (gated by ANTHROPIC_API_KEY env var; not in CI)
     ╱      ╲
    ╱        ╲    Integration (~15%) — wires multiple modules; fakes only
   ╱──────────╲    at the HTTP boundary
  ╱            ╲
 ╱      ←       ╲   Unit (~80%) — pure logic, no I/O, milliseconds each
╱───────────────╲
```

### Mock boundary rule

**Mock only at process boundaries.** Specifically:

| Layer | Mock? | Why |
|-------|-------|-----|
| Anthropic HTTP API | ✅ Yes (in unit tests) | External, slow, costs money, network-dependent |
| Subprocess (Bash tool) | ❌ No | OS subprocess is fast and deterministic enough |
| File system | ❌ No (use `tmp_path` fixture) | Real FS in tests catches real bugs |
| Pydantic models | ❌ Never | Mocking validation defeats the purpose |
| Internal modules | ❌ Never | Test the real wiring; refactor-safe |

When in doubt: don't mock.

### Coverage gates

| Scope | Gate | Enforced via |
|-------|------|-------------|
| `src/openharness/protocols/` | ≥ 90% | `pytest --cov-fail-under=90` per Module 2 task |
| `src/openharness/api/` | ≥ 90% | Same per Module 3 task |
| Total | ≥ 70% | Phase 1 DoD checklist |

Coverage is a **floor, not a ceiling**. Goal is meaningful behavior coverage,
not line-coverage games.

### TDD discipline

- **Micro-cycle = one complete logical unit** (e.g., one Pydantic class with
  all fields + one full test class). NOT "add one field". Field-level
  splitting was over-fragmentation (see Phase 1 Module 2 retrospective).
- **RED first** — every new behavior gets a failing test before
  implementation. A test that passes on first run is a yellow flag — verify
  it's testing the new behavior.
- **GREEN minimum** — implement the smallest code to pass the test. No
  fields "for later".
- **COMMIT each cycle** — every (test, code) pair lands as one commit.

### Test sizing

- **Unit tests**: < 100 ms each. No I/O, no sleeps, no real network.
- **Integration tests**: < 5 s each. Allowed to use real subprocess, real FS.
- **E2E**: marked with `@pytest.mark.integration`, skipped without env var.

### CI behavior

- All units + integrations run on every push and PR (matrix Python 3.10 / 3.11)
- E2E tests skipped in CI (env var not set)
- Coverage XML uploaded as artifact (no Codecov dependency)

---

## 7. Boundaries

Behavior contract for working on this project. The previous artifacts had no
explicit "always do / ask first / never do" list — this section codifies it.

### Always do (defaults — no permission needed)

- ✅ Run `ruff check && ruff format --check && mypy --strict src/ tests/ && pytest`
  before any commit
- ✅ Write a failing test before implementing new behavior
- ✅ Use `from __future__ import annotations` in every Python file
- ✅ Update `tasks/todo.md` when a task's status changes
- ✅ Write a `decisions/<NN>-<topic>.md` whenever a non-trivial trade-off is
  made
- ✅ Write a `learnings/<NN>-<module>.md` when a module completes
- ✅ Stage **only** the files relevant to the current logical unit (no
  `git add -A` unless intentional)
- ✅ Each commit message follows Conventional Commits (`feat` / `fix` / `docs`
  / `chore` / `refactor` / `test` / `style`)
- ✅ Run `pre-commit install` once on a fresh clone

### Ask first (must surface intent before doing)

- ⚠ **Adding a new runtime dependency** to `pyproject.toml`
  `[project] dependencies` → first ask "could the stdlib do this?"
- ⚠ **Removing or relaxing a strict-mode setting** (e.g., dropping
  `mypy strict` for a module, adding `# type: ignore` without `[error-code]`
  and a justifying comment)
- ⚠ **Changing the CI workflow** (`.github/workflows/ci.yml`) — esp. matrix
  changes, removing checks, or skipping tests
- ⚠ **Adding a new top-level directory** under repo root
- ⚠ **Picking a model** for an integration test (which Anthropic model? cost?)
- ⚠ **Pushing to remote** when commits are not yet reviewed by the human

### Never do (project-level prohibitions)

- ❌ **`git commit --no-verify`** as a routine — only acceptable as a one-off
  unblock (see Phase 1 Module 1 retrospective for the proxy story)
- ❌ **`git push --force` to `main`** under any circumstance
- ❌ **`git rebase -i` rewriting published history** (commits already on
  `origin`)
- ❌ **Delete tests** to make CI pass — fix the test or fix the code
- ❌ **Mock internal modules** in unit tests — only mock at process boundaries
- ❌ **Hard-code API keys, tokens, or secrets** — env vars only
- ❌ **Format conversion in protocol layer** — `protocols/` describes wire
  shape only; OpenAI-format conversion is the API client layer's job
- ❌ **Add `# type: ignore` without an `[error-code]` and a justifying comment**
- ❌ **Land a Module without a `learnings/<NN>.md` retrospective**
- ❌ **Field-by-field micro-cycles** for Pydantic models — one class = one cycle

### External integrations

- Anthropic SDK pinned (`anthropic>=0.40,<1.0`) — major-version upgrades
  require an explicit decision document
- MCP integration (Phase 5) starts with stdio transport only; HTTP / WS
  deferred
- All third-party services interact through a typed `Protocol` interface
  (`SupportsStreamingMessages`, etc.) — never depend directly on the SDK type
  surface beyond the call site

---

## Modification log

- **2026-04-27 (b)** — Added §4 Development Workflow (data flow diagram +
  layer roles + workflow loop). Renumbered §4-§6 to §5-§7 accordingly.
- **2026-04-27 (a)** — Initial creation. Renamed previous `SPEC.md`
  (OpenHarness reverse-engineered reference) to `REFERENCE.md`. Consolidated
  scattered spec content from `ARCHITECTURE.md`, `README.md`, `decisions/`,
  and `tasks/`. Added Testing Strategy and Boundaries — the two weakest areas
  before this consolidation.
