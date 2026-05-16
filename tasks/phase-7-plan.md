# Phase 7a Implementation Plan — ExecutionEnvironment Abstraction

> Phase 1-6 archive: [`tasks/plan.md`](./plan.md) /
> [`phase-2-plan.md`](./phase-2-plan.md) / [`phase-3-plan.md`](./phase-3-plan.md) /
> [`phase-4-plan.md`](./phase-4-plan.md) / [`phase-5-plan.md`](./phase-5-plan.md) /
> [`phase-5b-plan.md`](./phase-5b-plan.md) /
> [`phase-5c-skills-plan.md`](./phase-5c-skills-plan.md) /
> [`phase-6-plan.md`](./phase-6-plan.md).
>
> Boundary contract: [`decisions/15-phase-7-boundary.md`](../decisions/15-phase-7-boundary.md).
> Preview source (mixed 7a + 7b): [`tasks/phase-7-preview.md`](./phase-7-preview.md).

## Overview

**Phase 7a goal**: extract the substrate-execution logic out of
`BashTool` into a swappable `ExecutionEnvironment` abstraction. The
default `HostExecution` wraps the current `asyncio.create_subprocess_exec`
behavior identically — **byte-identical BashTool behavior pre/post
refactor**. The fourth tenant test of Phase 3's abstraction: injecting
an alternative substrate works without touching permission / hook /
engine / observability / mcp / compaction / skills / commands /
protocols layers.

Phase 7b (real Docker `SandboxExecution`) is a future ~250-LoC phase
that consumes the abstraction Phase 7a establishes.

**Total scope**: ~1-2 days, 4 capabilities, ~10 commits, ~150 lines of
production code.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/15-phase-7-boundary.md`](../decisions/15-phase-7-boundary.md) | D17.1 `ExecutionEnvironment` Protocol + `ProcessResult` shape; D17.2 `HostExecution` wraps current behavior identically; D17.3 additive `execution_env` field on QueryContext + ToolExecutionContext (default HostExecution singleton); D17.4 `BashTool` is the only consumer; D17.5 Phase 7b (Docker) deferred |

## Task list

### P7-T1: `execution/` package + `HostExecution` ✅

**Description**: Foundation — the abstraction layer + the identity
substrate. `ExecutionEnvironment` Protocol + `ProcessResult` dataclass
+ `HostExecution` concrete implementation that wraps current Bash
subprocess semantics. No engine wiring yet, no BashTool refactor yet —
just the data + Protocol.

**Acceptance**:
- [ ] `src/openharness/execution/__init__.py` exports
  `ExecutionEnvironment`, `HostExecution`, `ProcessResult`
- [ ] `src/openharness/execution/base.py`:
  - `ExecutionEnvironment(Protocol)` with `async run_command(command,
    cwd, timeout) -> ProcessResult`
  - `ProcessResult` frozen dataclass: `output: str` (merged
    stdout+stderr), `exit_code: int`, `timed_out: bool = False`
- [ ] `src/openharness/execution/host.py`:
  - `HostExecution(ExecutionEnvironment)` — body extracted from
    current `BashTool.execute`'s `asyncio.create_subprocess_shell`
    + merged stdout/stderr pipe + timeout + SIGTERM→SIGKILL escalation
  - Module-level `_HOST_EXECUTION = HostExecution()` singleton
- [ ] Tests (`tests/execution/test_host.py`):
  - `HostExecution.run_command` against `echo hello` → output text,
    exit 0
  - Against a non-existent command → exit code non-zero
  - With a `cwd` arg → subprocess runs in that directory
  - Stderr merged into output (chronological order preserved)
  - With `timeout=0.05` against a slow command → `timed_out=True`,
    `exit_code` sentinel
  - SIGTERM→SIGKILL escalation: subprocess that ignores SIGTERM still
    gets killed after grace period
  - Singleton: `_HOST_EXECUTION` is one shared instance

**Files**:
- `src/openharness/execution/__init__.py` (new)
- `src/openharness/execution/base.py` (new)
- `src/openharness/execution/host.py` (new)
- `tests/execution/__init__.py`, `tests/execution/test_host.py` (new)

**Sub-units**:
- 1a — `ProcessResult` + `ExecutionEnvironment` Protocol + tests
- 1b — `HostExecution.run_command` + tests (extract current Bash logic)

---

### P7-T2: Additive fields on `QueryContext` + `ToolExecutionContext` 🔜 NEXT

**Description**: Wire the substrate into the engine's existing
data structures. Both fields default to `HostExecution()` (or `None`
for ToolExecutionContext, with engine populating from QueryContext) so
**every existing test passes unchanged**. Same shape as Phase 6
`parent_query` additive field — engine constructs with one extra kwarg.

**Acceptance**:
- [ ] `engine/context.py`:
  - `QueryContext.execution_env: ExecutionEnvironment = field(default_factory=...)`
    defaulting to the `HostExecution` singleton
  - TYPE_CHECKING import of `ExecutionEnvironment` Protocol; runtime
    import of `_HOST_EXECUTION` for the default factory
- [ ] `tools/base.py`:
  - `ToolExecutionContext.execution_env: ExecutionEnvironment | None
    = None` additive field
  - TYPE_CHECKING import of Protocol
- [ ] `engine/query.py`:
  - One additive kwarg on the existing `ToolExecutionContext(cwd=...,
    parent_query=context)` call → `execution_env=context.execution_env`
- [ ] All existing tool tests pass unchanged (Read / Write / Edit /
  Grep / MCP adapters / LoadSkill / SpawnAgent)
- [ ] All existing engine tests pass unchanged
- [ ] Tests:
  - QueryContext default `execution_env` is a HostExecution instance
  - QueryContext custom `execution_env` injected at construction
  - `dataclasses.replace(parent, ...)` preserves `execution_env`
    (sub-agent inheritance for Phase 6 SpawnAgent)
  - ToolExecutionContext default `execution_env=None`
  - ToolExecutionContext explicit `execution_env` round-trips

**Files**:
- `src/openharness/engine/context.py` (+1 field)
- `src/openharness/tools/base.py` (+1 field)
- `src/openharness/engine/query.py` (+1 kwarg)
- `tests/engine/test_context.py` (+`TestExecutionEnvField`)
- `tests/tools/test_base.py` (+`TestExecutionEnvOnExecCtx`)

**Sub-units**:
- 2a — Context field additions + tests
- 2b — Engine wire + invariant smoke (all existing tests pass)

---

### P7-T3: `BashTool` refactor + behavior parity

**Description**: Replace the inline `asyncio.create_subprocess_exec`
logic in `BashTool.execute` with `await ctx.execution_env.run_command(...)`.
The interesting work is in the **substrate** (HostExecution from T1);
BashTool's responsibility shrinks to building `BashInput` → `cmd /
cwd / env / timeout / stdin` arg packing + `ProcessResult` →
`ToolResult` translation.

**Acceptance**:
- [ ] `tools/bash.py` `Bash.execute` refactored:
  - Resolves `cmd = ["bash", "-c", args.command]` (or shell, depending
    on current shape — preserve existing semantics)
  - Calls `result = await ctx.execution_env.run_command(...)` — falls
    back to `_HOST_EXECUTION` if `ctx.execution_env is None`
  - Wraps `ProcessResult` into `ToolResult`:
    - `result.exit_code == 0` → `output=result.stdout` (or `(no output)`
      sentinel if empty)
    - `result.exit_code != 0` → `output=stderr or stdout` +
      `is_error=True`
    - `result.timed_out` → `is_error=True`, output mentions timeout
- [ ] **All 24 existing BashTool tests pass unchanged** — this is the
  load-bearing assertion that proves HostExecution's behavior parity
- [ ] New test: BashTool with an injected `FakeExecutionEnvironment`
  returning a canned `ProcessResult` — the substrate swap works
- [ ] No new BashTool fields, no signature changes to `BashInput`
- [ ] mypy --strict + ruff clean

**Files**:
- `src/openharness/tools/bash.py` (`execute` body refactor only)
- `tests/tools/test_bash.py` (+`TestBashWithInjectedExecutionEnv`)

**Sub-units**:
- 3a — Refactor `Bash.execute` body to delegate; existing tests must pass
- 3b — `FakeExecutionEnvironment` injection test — proves swap works

---

### P7-T4: INVARIANT VERIFICATION + retro + README

**Description**: The structural invariant verification (the fourth
tenant test) + `learnings/phase-7a.md` + README section + DoD closeout.

**Acceptance**:
- [ ] Structural invariant test (`tests/execution/test_invariant.py`):
  - Reads source of `permissions/checker.py`,
    `permissions/tier_based.py`, `hooks/executor.py`,
    `hooks/registry.py`, `engine/context.py` (only the field
    annotation, no behavior), `engine/query.py`,
    `observability/logging.py`, `mcp/*.py`, `compaction/*.py`,
    `skills/*.py`, `commands/*.py`, `protocols/*.py`
  - Strips comments + docstrings
  - Asserts no `ExecutionEnvironment` / `HostExecution` / `ProcessResult`
    identifier appears EXCEPT in the explicitly allowed touch points
    (`engine/context.py`, `engine/query.py`, `tools/base.py`,
    `tools/bash.py`)
- [ ] **Formal git-diff invariant verification** in the retro:
  - `git diff <P6-close> -- src/openharness/permissions/` = empty
  - `git diff <P6-close> -- src/openharness/hooks/` = empty
  - `git diff <P6-close> -- src/openharness/observability/` = empty
  - `git diff <P6-close> -- src/openharness/mcp/` = empty
  - `git diff <P6-close> -- src/openharness/compaction/` = empty
  - `git diff <P6-close> -- src/openharness/skills/` = empty
  - `git diff <P6-close> -- src/openharness/commands/` = empty
  - `git diff <P6-close> -- src/openharness/protocols/` = empty
  - `git diff <P6-close> -- src/openharness/engine/query.py` shows
    exactly 1 additive kwarg (`execution_env=context.execution_env`)
- [ ] `execution/` module ≥ 95 % coverage
- [ ] Total coverage stays ≥ 95 %
- [ ] README "Phase 7a — ExecutionEnvironment abstraction" section:
  what it is (substrate abstraction), how BashTool consumes it,
  forward pointer to Phase 7b (Docker substrate)
- [ ] `learnings/phase-7a.md` — focus:
  - Fourth tenant invariant validation (3 additive code lines in
    engine after Phase 6's 3 → ~6 total since Phase 5c close)
  - Why abstraction-first beats Docker-first (preview's own §"两层
    结构" reasoning vindicated)
  - HostExecution as identity transform → behavior parity is the
    cleanest possible invariant proof
  - Forward pointer: Phase 7b's job is just to add SandboxExecution +
    glue; abstraction already proven
- [ ] Phase 7a DoD checklist all green

**Files**:
- `tests/execution/test_invariant.py` (new — structural)
- `README.md` (+ Phase 7a section)
- `learnings/phase-7a.md` (new)
- `tasks/phase-7-plan.md` (DoD closeout)

**Sub-units**:
- 4a — Structural invariant test
- 4b — README + `learnings/phase-7a.md` + closeout

---

## Checkpoints

After each capability: **human review** of code ↔ acceptance walkthrough
per CLAUDE.md GREEN→review→commit pattern.

### After P7-T1 + T2
- **Human review**: existing engine/tool tests pass without
  modification — confirms the additive-field discipline works
  (same pattern as Phase 6 `parent_query`)

### After P7-T3
- **Human review**: BashTool behavior parity is the load-bearing
  assertion. All 24 existing BashTool tests must pass with zero
  modification beyond the refactor itself

### After P7-T4 (Phase 7a complete)
- **Decision point**: Phase 7b (Docker) immediately, or shelve and
  pick up Phase 5d ModeBundle / Phase 8 Polish first?

---

## Risks

| Risk | Mitigation |
|---|---|
| `HostExecution` body subtly differs from current `BashTool.execute` → behavior drift in tests | T3 acceptance: all 24 existing BashTool tests pass unchanged. If any fails, HostExecution body is wrong; revert + fix. |
| `ExecutionEnvironment` Protocol shape too narrow for Phase 7b Docker needs | Phase 7b can extend the Protocol (e.g., add lifecycle methods); 7a's Protocol is a starting contract, not a permanent one. The retro flags this explicitly. |
| Sub-agent inheriting `execution_env` from parent via `dataclasses.replace` not tested explicitly | T2 acceptance includes a test for this — `dataclasses.replace(parent, agent_depth=N)` preserves `execution_env`. SpawnAgent doesn't need code change because it inherits the field automatically. |
| `ToolExecutionContext.execution_env` default `None` confusing for tool authors | Inline docstring on the field explains: tools that need substrate access read `ctx.execution_env or _HOST_EXECUTION`. BashTool sets the convention. |

## Risks specifically NOT mitigated (Phase 7b+)

- No real isolation — `HostExecution` runs commands on the host. This
  is intentional for 7a (it's a no-op identity substrate). Real
  isolation is Phase 7b's job.
- Streaming process output not supported — `ProcessResult` is a single
  final triple. Streaming defers if a real use case surfaces.

---

## Pointers

- Boundary: [`decisions/15-phase-7-boundary.md`](../decisions/15-phase-7-boundary.md)
- Preview source: [`tasks/phase-7-preview.md`](./phase-7-preview.md)
- Phase 6 plan (the additive-field pattern this phase mirrors): [`tasks/phase-6-plan.md`](./phase-6-plan.md)
- Phase 6 retro (where "stable plateau" insight was named): [`learnings/phase-6.md`](../learnings/phase-6.md) §3.1
- Phase 5b retro (layered extension model: only consumers pay the cost): [`learnings/phase-5b-commands.md`](../learnings/phase-5b-commands.md) §3.1
