# Phase 2 Implementation Plan — Tool Loop

> Phase 1's `plan.md` / `todo.md` remain as Phase 1 archive. This file is the
> active plan for Phase 2.
>
> Boundary contract: [decisions/06-phase-2-boundary.md](../decisions/06-phase-2-boundary.md).
> First-principles map: [learnings/openharness-first-principles.md](../learnings/openharness-first-principles.md).
> Top-level multi-phase strategy: [ARCHITECTURE.md](../ARCHITECTURE.md).

## Overview

**Phase 2 goal**: `oh ask "what files are in /tmp"` → LLM picks `Bash`
→ Bash runs → result fed back → LLM writes the answer. The agent loop
(§1 of the first-principles map) starts beating.

**Total scope**: ~2-3 weeks of focused work, 6 capabilities, ~25-40 commits expected.

## Architecture Decisions (locked before build)

| Doc | What it locks |
|---|---|
| [decisions/06-phase-2-boundary.md](../decisions/06-phase-2-boundary.md) | Phase 2 In/Out scope; D6.1 loop exit (hybrid stop_reason + max_turns); D6.2 permission baseline (deny-list + --auto / --dry-run); D6.3 serial tool execution; D6.4 PascalCase tool names; D6.5 system prompt as function |
| New decisions land here | Each capability may surface sub-decisions during build → recorded in `decisions/0X-<topic>.md` per CLAUDE.md ("决策时") |

## Task Sizing Principle

Same as Phase 1: **task = capability slice**, independently verifiable,
~1-3 days of focused work, 1-5 files at module-level granularity.

**Three-Axis discussion timing**: ARCHITECTURE.md §5 mandates a
領域問題 / 產品決策 / 工程要點 / Mini-Plan walkthrough **when entering
each capability**, not upfront in this file. This plan stays lean —
acceptance criteria, files, sub-unit sketch only.

## Task List

### P2-T1: Engine skeleton 🔜 NEXT

**Description**: Stand up the `engine/` module with the data structures
and helpers the loop will consume — but **not** the loop itself.
`QueryContext` carries immutable per-query config; `messages.py` exposes
helpers for building/extending conversation history; `query.py` exists
with a typed signature but `NotImplementedError` body. After this task
the loop's collaborators are all in place; only the body of `run_query`
is missing.

**Acceptance criteria**:
- [ ] `QueryContext` (frozen dataclass) carries: api_client, tool_registry, permission_checker, max_turns, system_prompt, cwd
- [ ] `messages.py` exposes helpers: `append_user_text`, `append_assistant_message`, `append_tool_results`, `extract_tool_uses`
- [ ] `query.py` exposes `run_query(initial_message, context) -> AsyncIterator[StreamEvent]` raising `NotImplementedError` with a clear message
- [ ] `from openharness.engine import QueryContext, run_query` works (top-level re-exports)
- [ ] Coverage on `engine/` ≥ 90% (the helpers are testable; the stub raises so it's trivially covered)
- [ ] `mypy --strict` clean; `ruff` clean

**Verification**:
```bash
uv run pytest tests/engine/ --cov=openharness.engine --cov-fail-under=90
uv run mypy --strict src/openharness/engine/ tests/engine/
```

**Files**: `src/openharness/engine/{__init__,context,messages,query}.py` + `tests/engine/`

**Sub-units sketch** (to be refined at task entry):
- 1a — `QueryContext` dataclass + tests
- 1b — `messages.py` helpers + tests (each helper one micro-cycle)
- 1c — `query.py` typed stub + integration test asserting `NotImplementedError`
- 1d — `__init__.py` re-exports + cross-module integration

---

### P2-T2: Tool system foundation

**Description**: The abstraction every concrete tool will plug into.
`BaseTool` is the ABC defining `name`, `description`, `input_model`,
and `async execute(args, context) -> ToolResult`. `ToolRegistry` holds
the registered tools and exposes `get(name)`, `list_tools()`, and
`to_api_schema() -> list[ToolSpec]`. `ToolResult` carries `output`,
`is_error`, `metadata`. `ToolExecutionContext` carries `cwd` and any
runtime metadata tools may need.

This task ships **no concrete tool** — that's P2-T3. We verify the
abstraction with a `_FakeTool` in tests.

**Acceptance criteria**:
- [ ] `BaseTool` ABC defined with the 4 attributes + 1 method, all enforced
- [ ] `ToolRegistry` supports register / get / list / to_api_schema; raises on duplicate names
- [ ] `ToolResult` is a frozen dataclass; `is_error=False` default; `metadata: dict[str, Any] = {}` default
- [ ] `ToolExecutionContext` is a frozen dataclass; cwd required
- [ ] A `_FakeTool` test fixture demonstrates registration → lookup → execution → result; covers happy path + error path
- [ ] Coverage on `tools/` ≥ 90%; `mypy --strict` clean; `ruff` clean

**Verification**:
```bash
uv run pytest tests/tools/test_base.py --cov=openharness.tools.base --cov-fail-under=90
```

**Files**: `src/openharness/tools/{__init__,base.py}` + `tests/tools/test_base.py`

**Sub-units sketch**:
- 2a — `ToolResult` + `ToolExecutionContext` (dataclasses) + tests
- 2b — `BaseTool` ABC + tests (using `_FakeTool` to verify enforcement)
- 2c — `ToolRegistry` + tests (register / get / list / duplicate detection)
- 2d — `to_api_schema()` translation (`BaseTool` → `ToolSpec`) + tests
- 2e — `__init__.py` re-exports + `_FakeTool` fixture promoted to `tests/tools/conftest.py`

---

### P2-T3: Five base tools — Read / Write / Edit / Bash / Grep

**Description**: The minimal viable action space. Each tool is a
concrete `BaseTool` with a Pydantic input model and an async execute
method. Per D6.4 they register under PascalCase names (`Read`, `Write`,
`Edit`, `Bash`, `Grep`).

**Tool-by-tool acceptance** (each is its own sub-unit):

- **Read**: file path → contents string; supports optional `offset` / `limit`; UTF-8 with `errors="replace"`; "(empty)" sentinel for empty files; ≤10 MB read limit (return error result if larger)
- **Write**: file path + content → creates/overwrites; reports bytes written; refuses to write if cwd outside the project root unless `--auto` set (P2-T6 plumbs the flag)
- **Edit**: file path + `old_str` + `new_str` + `replace_all: bool = False` → exact-string replacement; error if `old_str` not found; no implicit uniqueness check
- **Bash**: command string + optional `timeout_seconds` (default 600); merged stdout/stderr; SIGTERM → 2s wait → SIGKILL termination; output truncated at 12,000 chars
- **Grep**: pattern + optional `glob` / `-i` / `--hidden` flags; ripgrep-backed; 8MB stream limit; 200-line default cap, 2000 hard cap; "(no matches)" sentinel

**Acceptance criteria** (overall):
- [ ] All 5 tools register in a default `ToolRegistry` via `create_default_tool_registry()`
- [ ] Each tool has dedicated unit tests (happy path + at least 2 error paths)
- [ ] Bash test uses `tmp_path` for any side-effects; no real `rm` ever runs
- [ ] Grep test gracefully skips if `rg` is not on PATH (with a clear message)
- [ ] Coverage on `tools/` ≥ 90%; `mypy --strict` clean

**Verification**:
```bash
uv run pytest tests/tools/ --cov=openharness.tools --cov-fail-under=90
```

**Files**: `src/openharness/tools/{read,write,edit,bash,grep}_tool.py` + `tests/tools/test_*_tool.py` + factory in `tools/__init__.py`

**Sub-units**: one per tool (5 capabilities × 1 micro-cycle each).

---

### P2-T4: run_query core loop

**Description**: The heart. Replace the `NotImplementedError` body in
`engine/query.py` with the loop:

1. Build request from messages + tool registry + system prompt
2. Stream API response
3. Parse `tool_use` blocks from terminal `ApiMessageCompleteEvent`
4. For each block: permission check → execute tool → emit
   `ToolExecutionStarted` / `ToolExecutionCompleted` events → append
   `ToolResultBlock` to messages
5. If `stop_reason == "end_turn"` or `max_turns` reached → exit
6. Else → loop

Per D6.1: `stop_reason` primary + `max_turns=20` default hard cap.
Per D6.3: serial within a turn (`for block in tool_uses: await execute`).

**Acceptance criteria**:
- [ ] `run_query()` is an async generator yielding `StreamEvent`s in order
- [ ] Tool calls emit `ToolExecutionStarted(tool_name, tool_input)` then `ToolExecutionCompleted(tool_name, output, is_error)` per block
- [ ] Loop exits cleanly on `end_turn`; raises `LoopLimitExceeded` (subclass of `OpenHarnessApiError`) on `max_turns`
- [ ] Tool failure → `ToolResult(is_error=True)` fed back to LLM, loop continues (LLM gets to recover)
- [ ] Permission denial → emits `ToolExecutionCompleted(is_error=True, output="permission denied: ...")` fed back to LLM, loop continues
- [ ] End-to-end test using mocked client + 2 fake tools covers: 0-turn (immediate end_turn), 1-turn (one tool call then end_turn), max_turns reached
- [ ] Coverage on `engine/query.py` ≥ 90%; `mypy --strict` clean

**Verification**:
```bash
uv run pytest tests/engine/test_query.py --cov=openharness.engine.query --cov-fail-under=90
```

**Files**: `src/openharness/engine/query.py` (filled in) + `tests/engine/test_query.py` + new event types in `protocols/stream_events.py` + parallel tests

**Sub-units sketch**:
- 4a — Add `ToolExecutionStarted` / `ToolExecutionCompleted` to `ApiStreamEvent` discriminated union + tests
- 4b — Add `LoopLimitExceeded` to error hierarchy + tests
- 4c — `run_query` body for the **no-tool** path (`stop_reason == "end_turn"` from turn 1) + tests
- 4d — `run_query` body for the **tool-call** path (1 turn with 1 tool, then end_turn) + tests
- 4e — `run_query` body for the **multi-turn** path + max_turns boundary + tests

---

### P2-T5: System prompt assembly

**Description**: New file `src/openharness/prompts.py` exposing
`build_system_prompt(tools: list[ToolSpec], env: EnvironmentInfo) -> str`.
For Phase 2 the function assembles:

1. Base instructions ("you are OpenHarness, ...")
2. Tool catalog (name + 1-line description per tool)
3. Environment block (OS / shell / cwd / Python version)

Phase 3 will inject personalization rules into this same function;
Phase 4 will inject memory excerpts. The signature is the load-bearing
contract.

**Acceptance criteria**:
- [ ] `build_system_prompt(tools, env)` returns a non-empty string
- [ ] String contains every tool's name (smoke check that the catalog populates)
- [ ] String contains the cwd and OS name
- [ ] `EnvironmentInfo` is a frozen dataclass; populated by a sibling `detect_environment() -> EnvironmentInfo` helper
- [ ] Snapshot test asserts the prompt structure (not exact text — just section markers like "## Tools" / "## Environment")
- [ ] Coverage on `prompts.py` ≥ 90%; `mypy --strict` clean

**Verification**:
```bash
uv run pytest tests/test_prompts.py --cov=openharness.prompts --cov-fail-under=90
```

**Files**: `src/openharness/prompts.py` + `tests/test_prompts.py`

**Sub-units sketch**:
- 5a — `EnvironmentInfo` + `detect_environment()` + tests
- 5b — `build_system_prompt` + tests (catalog + env injection paths)

---

### P2-T6: Minimal permissions + CLI integration

**Description**: Two-part finishing capability.

**Part A (permissions)**: Implement `permissions/checker.py` with a
deny-list + the two CLI modes from D6.2.

- `PermissionChecker.evaluate(tool_name, args, context) -> PermissionDecision` (`Decision.ALLOW` / `Decision.DENY`)
- Hardcoded deny patterns: shell commands matching `rm -rf /`,
  `:(){ :|:& };:`, etc. (small, well-known list — full algorithm is
  Phase 3)
- `Settings`-level `permission_mode: Literal["default", "auto", "dry_run"]`
- `--auto` flag → permission_mode = "auto" (Phase 2 noop, reserved for Phase 3 confirmation skip)
- `--dry-run` flag → tools never execute; instead the loop emits
  `ToolExecutionCompleted(output="<would call X with Y>", is_error=False)`
  for each call

**Part B (CLI integration)**: Wire `ask` command into the loop.

- Replace single-call `_run_ask` with a loop-driven version
- Build `QueryContext` from settings + tool registry + permission checker
- Pass through to `run_query`; render the streamed events
- `_stream_render.py` extended to print tool-call markers (e.g., `[Bash] ls /tmp`)
  and tool results (truncated/abbreviated for readability)

**Acceptance criteria**:
- [ ] `oh ask "what files are in /tmp"` actually invokes `Bash` and prints both the tool call line and the model's final answer
- [ ] `oh ask --dry-run "rewrite my README"` lists tool calls without executing them; exit code 0
- [ ] `oh ask --auto ...` succeeds (no confirmation flow exists yet; flag is parsed and threaded but does nothing visible — Phase 3 plumbing)
- [ ] Deny-list test: a dangerous Bash command is rejected with `ToolExecutionCompleted(is_error=True)` and the LLM sees a `permission denied` message
- [ ] Render UI is readable: tool calls visually distinct from text deltas; results truncated when long
- [ ] Coverage overall stays ≥ 70% (Phase 1 baseline); `mypy --strict` clean

**Verification**:
```bash
uv run pytest tests/                                 # all green
uv run oh ask "list the files in $PWD"               # smoke
uv run oh ask --dry-run "rename README.md to docs.md"  # dry-run path
uv run pytest -m integration                         # integration unchanged
```

**Files**: `src/openharness/permissions/{__init__,checker.py}` + extensions to `cli.py` / `_stream_render.py` + parallel tests

**Sub-units sketch**:
- 6a — `PermissionChecker` + deny-list + tests
- 6b — `permission_mode` plumbed through `Settings` + `QueryContext` + tests
- 6c — `--auto` / `--dry-run` CLI flags + tests
- 6d — `_run_ask` rewritten to use the loop + render extension + tests
- 6e — Smoke tests (or a new integration test) for the end-to-end "list files" demo

---

## Checkpoints

### After P2-T1
- [ ] `engine/` module imports cleanly; QueryContext + messages helpers covered
- [ ] **Human review**: are the helpers ergonomic for the loop to use, or are we baking awkward patterns?

### After P2-T2
- [ ] Tool registry roundtrip works for a `_FakeTool`
- [ ] **Human review**: is `BaseTool` shaped right? once 5 real tools are written, this signature is hard to change.

### After P2-T3
- [ ] All 5 tools registered + tested
- [ ] **Human review**: do the tool input schemas read well from the LLM's perspective? (look at the JSON schema each Pydantic model emits)

### After P2-T4
- [ ] First multi-turn loop test passes
- [ ] **Human review**: is `run_query`'s event sequence what `_stream_render.py` will need? (any awkward asymmetries between text + tool events?)

### After P2-T5
- [ ] System prompt assembly works; tool catalog populated
- [ ] **Human review**: does the prompt feel coherent end-to-end? (read the generated string out loud)

### After P2-T6 (Phase 2 complete)
- [ ] `oh ask "what files are in /tmp"` actually works against real Qwen
- [ ] `--dry-run` and `--auto` plumbed
- [ ] **Decision point**: enter Phase 3 (Safety + Hardening) or pause to write Phase 1+2 retrospective first

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM ignores tool-use instructions and returns plain text | High | System prompt has explicit "use tools when appropriate" language; integration test verifies tool dispatch on a known-tool prompt |
| `Bash` tool exposes the dev machine to LLM-generated commands | Med-High | Deny-list catches known catastrophes; `--dry-run` for risky prompts; `--auto` reserved for Phase 3 to add interactive confirmation |
| Streaming `tool_use` chunks accumulate incorrectly | Med | `_StreamAssembler` (already in `api/translation.py`) is unit-tested; extend tests to cover multi-tool turn edge cases |
| Loop limit triggers on legitimate long tasks | Low | `--max-turns` flag overrides; default 20 is generous for typical "ask + 1-3 tool calls + answer" |
| Phase 2 capabilities overlap with Phase 3 (permissions) | Med | decisions/06 explicitly lists what Phase 2 ships vs Phase 3; review that doc when in doubt rather than expanding scope mid-build |

## Open Questions

- For tool **input schemas**: should each tool define its own Pydantic model
  (one per tool), or share a base `BaseToolInput`? **Tentative**: one
  Pydantic model per tool, no shared base — keeps tools independent;
  shared base would over-couple them. Revisit if 3+ tools share fields.
- For `--dry-run`: should the rendered "would call X with Y" line
  emit before or after the LLM's text reasoning that explains why it
  picked the tool? **Tentative**: before, immediately after the tool
  call appears in the response — matches the streamed order.
- For tool **failure granularity**: should `ToolResult.metadata` carry
  structured error info (errno, stderr, etc.) for the LLM to read,
  or just a flat `output` string? **Tentative**: flat string for
  Phase 2; revisit when a real recovery scenario surfaces.

## Pre-flight Cleanup (carried from Phase 1)

These remain in `tasks/todo.md` "Phase 2 Pre-flight Cleanup TODOs"
and should be batched in early Phase 2 (good first sub-units when
warming up):

- [ ] 显式定义 `class SupportsStreamingMessages(Protocol)`
- [ ] `_FAST_POLICY` 抽到 `tests/api/conftest.py`
- [ ] `_translate_openai_error` 单独 test file
- [ ] CI 显式加 `-m "not integration"` flag
- [ ] `decisions/00-env.md` 记录代理端口陷阱
- [ ] Pin `.pre-commit-config.yaml` ruff hook 版本

## Pointers

- Boundary contract: [decisions/06-phase-2-boundary.md](../decisions/06-phase-2-boundary.md)
- First-principles map: [learnings/openharness-first-principles.md](../learnings/openharness-first-principles.md)
- Phase 1 archive: [tasks/plan.md](./plan.md), [tasks/todo.md](./todo.md)
- Phase 1 retrospective: [learnings/phase-1.md](../learnings/phase-1.md)
- OpenHarness reference (analogous modules): REFERENCE.md §5 (engine), §6 (tools), §8 (permissions), §23 (prompts)
