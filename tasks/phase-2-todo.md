# Phase 2 Todo

> Tracks the 6 capabilities defined in [phase-2-plan.md](./phase-2-plan.md).
> Phase 1 archive: [plan.md](./plan.md) / [todo.md](./todo.md).

**Currently working on**: P2-T2 (tool system foundation) — NEXT after Three-Axis kickoff for P2-T2.

---

## P2-T1: Engine skeleton ✅

**Decisions**: [decisions/06-phase-2-boundary.md](../decisions/06-phase-2-boundary.md);
Three-Axis sub-decisions D7.1–D7.5 captured in [learnings/05-engine-skeleton.md](../learnings/05-engine-skeleton.md).

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 1a | `QueryContext` frozen dataclass + tests | ✅ | `9ca4233` |
| 1b | `messages.py` helpers (append_user_text / append_assistant_message / append_tool_results / extract_tool_uses) + tests | ✅ | `9c3d0d7` |
| 1c | `query.py` typed stub raising NotImplementedError + tests | ✅ | `c0fab58` |
| 1d | `engine/__init__.py` re-exports + cross-module integration | ✅ | `31a06eb` |

**Acceptance**: `from openharness.engine import QueryContext, run_query` works;
coverage on `engine/` 100% (4/4 files); `mypy --strict` clean. Loop body still
missing — that's P2-T4.

**Hand-offs to later tasks** (recorded so they don't get lost):
- P2-T2 acceptance must include: tighten `QueryContext.tool_registry: object` → `ToolRegistry`
- P2-T6 acceptance must include: tighten `QueryContext.permission_checker: object` → `PermissionChecker`
- P2-T4 must clean up the three "stub tripwires" in `engine/query.py`
  (`# type: ignore[unreachable]` on `yield`, two `# noqa: ARG001` on params)

---

## P2-T2: Tool system foundation

**Decisions**: 06-phase-2-boundary D6.4 (PascalCase names) + tool-system specifics
TBD via Three-Axis discussion at task entry.

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 2a | `ToolResult` + `ToolExecutionContext` dataclasses + tests | ☐ | |
| 2b | `BaseTool` ABC + tests via `_FakeTool` fixture | ☐ | |
| 2c | `ToolRegistry` (register / get / list / duplicate detection) + tests | ☐ | |
| 2d | `to_api_schema()` translation BaseTool → ToolSpec + tests | ☐ | |
| 2e | `tools/__init__.py` re-exports + `_FakeTool` promoted to `tests/tools/conftest.py` | ☐ | |

**Acceptance**: A `_FakeTool` registers, executes, returns `ToolResult`; coverage on `tools/` ≥ 90%.

---

## P2-T3: Five base tools (Read / Write / Edit / Bash / Grep)

**Decisions**: per-tool semantics (timeout / truncation / safety) — Three-Axis discussion
at task entry; expect a new `decisions/0X-base-tools.md`.

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 3a | `Read` tool + tests | ☐ | |
| 3b | `Write` tool + tests | ☐ | |
| 3c | `Edit` tool (exact string replacement) + tests | ☐ | |
| 3d | `Bash` tool (asyncio subprocess + 600s timeout + 12k char truncation) + tests | ☐ | |
| 3e | `Grep` tool (ripgrep wrapper + 8MB stream limit + 200/2000 line caps) + tests | ☐ | |
| 3f | `create_default_tool_registry()` factory + integration test (all 5 round-trip) | ☐ | |

**Acceptance**: All 5 tools registered, each with happy path + ≥2 error path tests; coverage ≥ 90%.

---

## P2-T4: run_query core loop

**Decisions**: 06-phase-2-boundary D6.1 (loop exit hybrid) + D6.3 (serial execution).
Sub-decisions on event ordering / error semantics — Three-Axis at task entry.

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 4a | `ToolExecutionStarted` / `ToolExecutionCompleted` added to `ApiStreamEvent` discriminated union + tests | ☐ | |
| 4b | `LoopLimitExceeded` added to error hierarchy + tests | ☐ | |
| 4c | `run_query` no-tool path (immediate `end_turn`) + tests | ☐ | |
| 4d | `run_query` single-tool path (1 turn → tool → end_turn) + tests | ☐ | |
| 4e | `run_query` multi-turn + max_turns boundary + tests | ☐ | |

**Acceptance**: end-to-end test with mocked client + 2 fake tools covers 0-turn / 1-turn / max_turns;
coverage on `engine/query.py` ≥ 90%.

---

## P2-T5: System prompt assembly

**Decisions**: 06-phase-2-boundary D6.5 (function-driven). Internal structure of the
prompt itself (sections / order / phrasing) — Three-Axis at task entry.

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 5a | `EnvironmentInfo` dataclass + `detect_environment()` + tests | ☐ | |
| 5b | `build_system_prompt(tools, env)` + tests (catalog + env injection) | ☐ | |

**Acceptance**: prompt non-empty; contains every tool name + cwd + OS; snapshot test for structure.

---

## P2-T6: Minimal permissions + CLI integration

**Decisions**: 06-phase-2-boundary D6.2 (deny-list + flags). Specific deny patterns
+ render UX for tool calls — Three-Axis at task entry.

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 6a | `PermissionChecker` + hardcoded deny-list + tests | ☐ | |
| 6b | `permission_mode` plumbed through `Settings` + `QueryContext` + tests | ☐ | |
| 6c | `--auto` / `--dry-run` CLI flags + tests | ☐ | |
| 6d | `_run_ask` rewrite (single call → loop) + `_stream_render.py` extension for tool events + tests | ☐ | |
| 6e | Smoke test: `oh ask "list files in $PWD"` actually invokes `Bash` (or new gated integration test) | ☐ | |

**Acceptance**: `oh ask "what files are in /tmp"` invokes `Bash` and returns the answer;
`--dry-run` lists without executing; deny-list rejects dangerous commands.

---

## Phase 2 Definition of Done

- [ ] All 6 capabilities ✅ in this file
- [ ] Overall coverage ≥ 70% (Phase 1 baseline maintained)
- [ ] `mypy --strict src/ tests/` clean
- [ ] `ruff check && ruff format --check` clean
- [ ] `oh ask "what files are in /tmp"` actually works against real Qwen
- [ ] `oh ask --dry-run "<anything>"` lists tool calls without side-effects
- [ ] Combined Phase 1+2 retrospective written: `learnings/phase-1-and-2.md`
  (per [decisions/06](../decisions/06-phase-2-boundary.md) Process Meta-Decision)
- [ ] CI green on a clean push

---

## Phase 2 Pre-flight Cleanup TODOs

来自 Phase 1 各 module retro，进 Phase 2 任意 capability 前 batch 处理（建议 P2-T1 之前先做完）：

- [ ] 显式定义 `class SupportsStreamingMessages(Protocol)` (learnings/03 #3)
- [ ] `_FAST_POLICY` 抽到 `tests/api/conftest.py` (learnings/03 #4)
- [ ] `_translate_openai_error` 单独 test file (learnings/03 #6)
- [ ] CI 显式加 `-m "not integration"` flag
- [ ] `decisions/00-env.md` 记录代理端口陷阱 (learnings/01 #3)
- [ ] Pin `.pre-commit-config.yaml` ruff hook 版本，消除 `ruff format` 与 pre-commit 之间的版本飘移
