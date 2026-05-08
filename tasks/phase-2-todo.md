# Phase 2 Todo

> Tracks the 6 capabilities defined in [phase-2-plan.md](./phase-2-plan.md).
> Phase 1 archive: [plan.md](./plan.md) / [todo.md](./todo.md).

**Phase 2 ✅ COMPLETE.** Next milestone: write `learnings/phase-1-and-2.md`
combined retrospective (per [decisions/06](../decisions/06-phase-2-boundary.md)
Process Meta-Decision), then enter Phase 3 (Safety + Production Hardening).

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

## P2-T2: Tool system foundation ✅

**Decisions**: 06-phase-2-boundary D6.4 (PascalCase names);
Three-Axis sub-decisions D8.1–D8.9 captured in [learnings/06-tool-system.md](../learnings/06-tool-system.md).

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 2a | `ToolResult` + `ToolExecutionContext` dataclasses + tests | ✅ | `06fe5ec` |
| 2b | `BaseTool` ABC + tests via `_FakeTool` fixture | ✅ | `810bf99` |
| 2c | `ToolRegistry` (register / get / list / duplicate detection) + tests | ✅ | `66dfb37` |
| 2d | `to_api_schema()` translation BaseTool → ToolSpec + tests | ✅ | `8e7988f` |
| 2e | `tools/__init__.py` re-exports + `_FakeTool` promoted to `tests/tools/conftest.py` + engine D7.2 hand-off | ✅ | `be5ebb2` |

**Acceptance**: `_FakeTool` registers, executes, returns `ToolResult`; coverage
on `tools/` 100% (3/3 + 36/36 statements); `mypy --strict` clean. P2-T1 D7.2
hand-off cashed: `QueryContext.tool_registry: object` tightened to `ToolRegistry`.

**Hand-offs to later tasks**:
- P2-T6 acceptance must include: tighten `QueryContext.permission_checker: object` → `PermissionChecker` (the last D7.2 marker)

---

## P2-T3: Five base tools (Read / Write / Edit / Bash / Grep) ✅

**Decisions**: [decisions/07-base-tools.md](../decisions/07-base-tools.md)
captures D9.1-D9.6. Retrospective + Python patterns:
[learnings/07-base-tools.md](../learnings/07-base-tools.md).

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 3a | `Read` tool + tests | ✅ | `0344d2c` |
| 3b | `Write` tool + tests | ✅ | `f4ad745` |
| 3c | `Edit` tool (exact string replacement) + tests | ✅ | `99b5db4` |
| 3d | `Bash` tool (asyncio subprocess + 600s timeout + 12k char truncation) + tests | ✅ | `925c52c` |
| 3e | `Grep` tool (ripgrep wrapper + 8MB stream limit + 200/2000 line caps) + tests | ✅ | `e274147` |
| 3f | `create_default_tool_registry()` factory + decisions/07 + integration test | ✅ | `8a4bda1` |

**Acceptance**: All 5 tools registered, each with happy + ≥2 error path tests
(45 tests across 6 files); end-to-end Read / Bash execution verified via the
default-registry integration test. All gates clean.

---

## P2-T4: run_query core loop ✅

**Decisions**: 06-phase-2-boundary D6.1 (loop exit hybrid) + D6.3 (serial execution).
Sub-decisions D10.1–D10.5 + D7.1 amendment captured in
[learnings/08-run-query.md](../learnings/08-run-query.md).

> **Sub-unit count revised from 5 to 6** — added 4c for the
> `PermissionChecker` Protocol (D10.1). Original 4c-4e renumbered to 4d-4f.

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 4a | `ToolExecutionStartedEvent` + `ToolExecutionCompletedEvent` added to discriminated union | ✅ | `c264c92` |
| 4b | `LoopLimitExceeded(OpenHarnessApiError)` + tests | ✅ | `7b56c32` |
| 4c | `PermissionChecker` Protocol + `Decision` enum + tighten `QueryContext` (D7.2 cash 2/2) | ✅ | `dd2a9d7` |
| 4d | `run_query` no-tool path (clean P2-T1 stub tripwires) + tests | ✅ | `4d1b6fb` |
| 4e | `run_query` 1-tool path + 4 recovery flows | ✅ | `aa43d02` |
| 4f | multi-turn + max_turns boundary + programming-error propagation tests | ✅ | `d9c91e3` |

**Acceptance**: `run_query` body fully landed. 19 dedicated tests in
`tests/engine/test_query.py` cover async-generator shape / no-tool exit on 3
stop_reasons / request shape / defensive copy / 1-tool happy path / 4
recovery flows / 3-turn happy / max_turns boundary / programming-error
propagation / multi-tool serial dispatch. `mypy --strict` clean. Both D7.2
hand-offs cashed; `rg "tighten to"` returns no functional markers.

---

## P2-T5: System prompt assembly ✅

**Decisions**: 06-phase-2-boundary D6.5 (function-driven).
Three-Axis sub-decisions D11.1-D11.6 captured in
[learnings/09-prompts.md](../learnings/09-prompts.md).

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 5a | `EnvironmentInfo` dataclass + `detect_environment()` + tests | ✅ | `015c1d4` |
| 5b | `build_system_prompt(tools, env)` + tests (catalog + env + base + sentinel + ordering) | ✅ | `afc25f8` |

**Acceptance**: prompt non-empty; section markers (`## Tools` / `## Environment`)
present; every tool name + description appears verbatim; cwd + os_name + os_version
+ shell + python_version all surface; empty tools renders sentinel; ordering
locked. 13 dedicated tests in `tests/test_prompts.py`. mypy strict + ruff clean.

---

## P2-T6: Minimal permissions + CLI integration ✅

**Decisions**: 06-phase-2-boundary D6.2 (deny-list + flags).
Three-Axis sub-decisions D12.1-D12.8 captured in
[learnings/10-cli-loop.md](../learnings/10-cli-loop.md).

> **Sub-unit count revised from 5 to 6**: split DRY_RUN integration into
> its own sub-unit (6c) since it touches `engine/query.py` distinctly from
> the cli.py rewrite. Original 6a-6e mapping preserved by simply renumbering.

| # | Sub-unit | Status | Commit |
|---|---------|--------|--------|
| 6a | `DenyListChecker` + `PermissionMode` enum + 7 deny patterns + tests | ✅ | `343cd97` |
| 6b | `Settings.permission_mode` + `QueryContext.permission_mode` plumbing + tests | ✅ | `7a04209` |
| 6c | `run_query` DRY_RUN short-circuit + tests | ✅ | `a2f247a` |
| 6d | `_stream_render.py` extension for 5-event dispatch + tests | ✅ | `7bb283f` |
| 6e | `cli.py` `_run_ask` rewrite using `run_query` + `--auto` / `--dry-run` flags + tests | ✅ | `cbe7c32` |
| 6f | end-to-end mock integration (real Bash + DenyListChecker + render) | ✅ | `0ed3b5a` |

**Acceptance** (all green via `tests/cli/test_loop_integration.py`):
- `oh ask "..."` invokes Bash via the loop and surfaces real subprocess output
- `--dry-run` emits `[Bash] → would call Bash with {...}` without executing
- Deny-list rejects `rm -rf /` with `[Bash error] permission denied: Bash`
- LLM's recovery text flows through after a denial, loop doesn't crash

351 total tests, mypy strict + ruff clean, total coverage 93%+.

---

## Phase 2 Definition of Done

- [x] All 6 capabilities ✅ in this file
- [x] Overall coverage ≥ 70% (Phase 1 baseline maintained) — actual 93%+
- [x] `mypy --strict src/ tests/` clean
- [x] `ruff check && ruff format --check` clean
- [x] `oh ask --dry-run "<anything>"` lists tool calls without side-effects
      (verified by `tests/cli/test_loop_integration.py`)
- [ ] `oh ask "what files are in /tmp"` works against real Qwen
      (mock-integration verified; real-API smoke is user-controlled)
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
