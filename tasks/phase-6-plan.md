# Phase 6 Implementation Plan — Sub-agent(Recursive Tool Dispatch)

> Phase 1-5c archive: [`tasks/plan.md`](./plan.md) / [`phase-2-plan.md`](./phase-2-plan.md) /
> [`phase-3-plan.md`](./phase-3-plan.md) / [`phase-4-plan.md`](./phase-4-plan.md) /
> [`phase-5-plan.md`](./phase-5-plan.md) / [`phase-5c-skills-plan.md`](./phase-5c-skills-plan.md).
>
> Boundary contract: [`decisions/13-phase-6-boundary.md`](../decisions/13-phase-6-boundary.md).
> Sandbox preview(now Phase 7 candidate): [`tasks/phase-7-preview.md`](./phase-7-preview.md).

## Overview

**Phase 6 goal**: Make `oh ask` expose an `Agent` tool the LLM can call to
delegate a sub-task to a fresh agent loop. `SpawnAgent.execute` re-enters
`run_query` with an isolated `QueryContext`(its own `system_prompt`,
`max_turns`, `agent_depth+1`); collects the final assistant text and
returns it as a single `ToolResult` to the parent. The cross-cutting
**invariant**(third compounding test): `permissions/`, `hooks/`,
`engine/query.py` dispatch logic, `mcp/`, `compaction/`, `protocols/`
**stay unchanged**.

The conceptual lesson Phase 6 cashes: **tool dispatch is the LLM's syscall
interface, and the agent loop itself is one of the syscalls**. Sub-agent
isn't a new mechanism — it's the recursive application of the primitive
the harness already owns.

**Total scope**: ~3-5 days, 6 capabilities, ~10-15 commits, ~150-200
lines of production code.

## Architecture decisions(locked)

| Doc | What it locks |
|---|---|
| [`decisions/13-phase-6-boundary.md`](../decisions/13-phase-6-boundary.md) | D16.1 SpawnAgent = BaseTool subclass(invariant test #3);D16.2 QueryContext inheritance(override system_prompt + max_turns + agent_depth only);D16.3 full registry inheritance(no filter);D16.4 final-text → ToolResult.output, internal events not surfaced;D16.5 max_agent_depth=3 default;D16.6 serial only;D16.7 parent_run_id + agent_depth on every log event;D16.8 `ToolExecutionContext.parent_query` additive field |

## Task list

### P6-T1: Settings + QueryContext fields ✅

**Description**: Foundation — add `max_agent_depth` to Settings, add
`agent_depth` + `max_agent_depth` to QueryContext. No tool yet, no
recursion;just the depth-tracking data the next capabilities plug into.
Same shape as P5-T1(`McpServerConfig` + Settings fields).

**Acceptance**:
- [ ] `Settings.max_agent_depth: int = 3` — env `OPENHARNESS_MAX_AGENT_DEPTH`
  parses to int;validation rejects `< 0`(0 is valid → disables spawning
  by making any spawn immediately exceed the cap)
- [ ] `QueryContext.agent_depth: int = 0` — top-level CLI invocations
  default to 0;sub-agent's `dataclasses.replace` bumps to `parent+1`
- [ ] `QueryContext.max_agent_depth: int = 3` — propagates through
  sub-agent levels so every depth check uses same cap
- [ ] CLI wires Settings → QueryContext.max_agent_depth
- [ ] Tests: parametrized env-var parsing including edge cases(0 / 1 /
  large);Settings validation rejection on negative;QueryContext
  construction from CLI

**Files**:
- `src/openharness/config/settings.py`(+1 field + 1 field_validator)
- `src/openharness/engine/context.py`(+2 fields)
- `src/openharness/cli.py`(wire `max_agent_depth=settings.max_agent_depth`)
- `tests/config/test_settings.py`(+`TestMaxAgentDepth`)
- `tests/engine/test_context.py`(+depth field assertions)

**Sub-units**:
- 1a — `Settings.max_agent_depth` + parsing + tests
- 1b — `QueryContext` fields + CLI wiring + tests

---

### P6-T2: `ToolExecutionContext.parent_query` additive field ✅

**Description**: The mechanical question Phase 6 forces — how does
`SpawnAgent.execute` access the parent's `QueryContext`?Boundary D16.8
answers: additive optional field on `ToolExecutionContext`. Engine
constructs with `parent_query=context`. Existing tools(Read / Write /
Edit / Bash / Grep / MCP adapters)ignore the field and behave
identically.

**Acceptance**:
- [ ] `ToolExecutionContext.parent_query: QueryContext | None = None`
  added with forward-ref(`TYPE_CHECKING` import to avoid runtime cycle)
- [ ] `engine/query.py` line 261:
  `exec_context = ToolExecutionContext(cwd=context.cwd, parent_query=context)` —
  the ONLY engine dispatch change Phase 6 introduces
- [ ] All existing tool tests pass unchanged(invariant smoke: built-ins
  never look at `parent_query`)
- [ ] Tests: `ToolExecutionContext` constructs with default `None`;
  with explicit `QueryContext` passed;backward-compat assertion that
  positional construction `ToolExecutionContext(cwd=p)` still works
- [ ] `tools/base.py` docstring updated to reflect the new field +
  acknowledge the recursion enabling

**Files**:
- `src/openharness/tools/base.py`(+1 field + TYPE_CHECKING import)
- `src/openharness/engine/query.py`(+1 line on exec_context construction)
- `tests/tools/test_base.py`(+`TestParentQueryField`)

**Sub-units**: 1 sub-unit, single commit.

**Invariant check at commit**:
- [ ] `git diff <P5c-close> -- src/openharness/permissions/checker.py` empty
- [ ] `git diff <P5c-close> -- src/openharness/hooks/executor.py` empty
- [ ] `git diff <P5c-close> -- src/openharness/engine/query.py` shows
  exactly one line addition(the `parent_query=context` kwarg)

---

### P6-T3: `SpawnAgent` BaseTool subclass 🔜 NEXT

**Description**: The core capability. `SpawnAgent(BaseTool)`'s `execute`
body checks depth, constructs `sub_context` via `dataclasses.replace`,
consumes `run_query`'s event stream, collects final assistant text,
returns `ToolResult`. This is where the recursion lives — and where the
"tool dispatch is the syscall interface" insight becomes runnable code.

**Acceptance**:
- [ ] `tools/spawn_agent.py` — `SpawnAgent(BaseTool[SpawnAgentInput])`:
  - Constructor: `(name: str = "Agent", description: str = "...", system_prompt: str | None = None, max_turns: int = 20, tool_filter: set[str] | None = None)` — `tool_filter` accepted as forward-compat per boundary sub-decision but ignored in Phase 6
  - `SpawnAgentInput` Pydantic model: `description: str`(short label for trace), `prompt: str`(the actual task)
  - `is_read_only = False`;`trust_source = "local"`
- [ ] `execute(args, exec_ctx)`:
  1. If `exec_ctx.parent_query is None` → return `ToolResult(is_error=True, output="SpawnAgent invoked outside an active query context")`(defensive;should never happen if engine populates correctly)
  2. If `parent.agent_depth + 1 > parent.max_agent_depth` → return depth-exceeded `ToolResult(is_error=True)` per D16.5
  3. Build `sub_context = dataclasses.replace(parent, system_prompt=self._sub_prompt or parent.system_prompt, max_turns=self._max_turns, agent_depth=parent.agent_depth + 1)`
  4. Build `initial_messages = [ConversationMessage(role="user", content=[TextBlock(text=args.prompt)])]`
  5. `async for event in run_query(initial_messages, sub_context):` — consume all events;capture final `ApiMessageCompleteEvent.message`
  6. Extract text from final message's `TextBlock` content → `ToolResult.output`
  7. `LoopLimitExceeded` → `ToolResult(is_error=True, output="sub-agent exceeded max_turns=...")` per D16.4
  8. Other `OpenHarnessError` or unhandled exception → propagates(engine wraps as `ToolError`)
- [ ] Tests(via stub `api_client` injected through `QueryContext`):
  - Happy path: stub yields one `ApiMessageCompleteEvent` with `stop_reason=end_turn` → sub-agent returns text;parent's messages grow by one tool_use/tool_result pair only
  - Depth bound: parent at `agent_depth=3`, max=3 → invocation returns depth-exceeded
  - LoopLimitExceeded: stub keeps emitting `tool_use` past max_turns → sub-agent's loop raises → `SpawnAgent.execute` catches → `is_error=True`
  - Context isolation: sub-agent's internal `tool_use`(stubbed `Read` call)does NOT appear in parent's messages
  - Recursion: sub-agent at depth 1 spawning another sub-agent → depth=2 reachable;at depth 3 the 4th refuses

**Files**:
- `src/openharness/tools/spawn_agent.py`(new)
- `tests/tools/test_spawn_agent.py`(new)

**Sub-units**:
- 3a — `SpawnAgentInput` + class skeleton + depth check + tests
- 3b — `execute()` body(sub_context construction + run_query consumption + text extraction) + tests
- 3c — Failure paths(LoopLimitExceeded / parent_query None / programming error propagation) + tests

---

### P6-T4: Observability — `parent_run_id` + `agent_depth` binding

**Description**: D16.7 — every log event emitted inside sub-agent's
`run_query` carries `parent_run_id`(pointing at parent's run_id) and
`agent_depth=N`. No new event types;additive fields. `bind_run`
detects nested invocation;`bind_turn` or a new helper binds depth.

**Acceptance**:
- [ ] `observability/logging.bind_run()` reads the current contextvars
  before minting a new `run_id`. If `run_id` is already bound(nested
  call), stash the existing value as `parent_run_id` on the new bound
  context;else `parent_run_id` is absent from the bound context.
- [ ] `agent_depth` from the active `QueryContext` is bound to all
  events. Mechanism options(picked at build):
  - (a) `run_query` binds it explicitly via `structlog.contextvars.bind_contextvars(agent_depth=context.agent_depth)` before its main loop
  - (b) `bind_run` accepts `agent_depth` kwarg
  - **Tentative (a)** — simpler;keeps `bind_run` agnostic of QueryContext
- [ ] Top-level run: events have `run_id=R1`, `agent_depth=0`, no
  `parent_run_id` field
- [ ] Nested run(sub-agent): events have `run_id=R2`,
  `parent_run_id=R1`, `agent_depth=1`
- [ ] Tests:
  - Capture log events via `structlog.testing.capture_logs` from a
    sub-agent run;assert `parent_run_id` + `agent_depth` present and
    correct on `turn_start` / `tool_dispatch` / `tool_complete`
  - Top-level events have no `parent_run_id`
  - Three-level nesting: depth=2 sub-agent's events have
    `parent_run_id=R2`(immediate parent), `agent_depth=2`(transitive
    immediate-only chain confirmed by sub-decision in boundary)

**Files**:
- `src/openharness/observability/logging.py`(+`bind_run` parent
  detection;~10 lines)
- `src/openharness/engine/query.py`(+1 line: `bind_contextvars(agent_depth=context.agent_depth)` at top of `run_query` body or near `bind_run`)
- `tests/observability/test_logging.py`(+`TestParentRunIdChain`)

**Sub-units**:
- 4a — `bind_run` parent_run_id detection + tests
- 4b — `agent_depth` binding inside `run_query` + tests

**Invariant check at commit**:
- [ ] Same three diffs verified empty / +1 line as P6-T2;observability
  change is additive to logging module, not dispatch logic

---

### P6-T5: CLI bootstrap + INVARIANT VERIFICATION ⭐

**Description**: Register a default `SpawnAgent("Agent")` instance into
the registry in `cli._run_ask`. Wire `max_agent_depth` from Settings into
QueryContext. Run the cross-cutting invariant verification against
Phase 5c close — `permissions/`, `hooks/`, `engine/query.py` dispatch
logic, `mcp/`, `compaction/`, `protocols/` all zero diff.

**Acceptance**:
- [ ] `create_default_tool_registry()`(or `cli._run_ask`)registers a
  default `SpawnAgent("Agent", description="...")` instance
  - Placement TBD at build: pure-default in `tools.factory` keeps it
    parallel to Read/Write/Bash/Grep/Edit;CLI-level if Phase 6+ wants
    opt-out
- [ ] System prompt mentions the `Agent` tool naturally(rebuild prompt
  test passes;LLM sees the catalog)
- [ ] CLI passes `max_agent_depth=settings.max_agent_depth` to
  QueryContext(P6-T1 wiring confirmed)
- [ ] Tests: `CliRunner` invocation with stub api_client returning a
  `tool_use(name="Agent", ...)`;trace shows correct sub-agent activity;
  parent's `tool_result` carries the sub-agent's text

**Critical invariant verification(D16 cross-cutting)**:
- [ ] `git diff <P5c-close> -- src/openharness/permissions/checker.py` = empty
- [ ] `git diff <P5c-close> -- src/openharness/hooks/executor.py` = empty
- [ ] `git diff <P5c-close> -- src/openharness/engine/query.py` shows
  only the `parent_query=context` line(P6-T2)+ the
  `bind_contextvars(agent_depth=...)` line(P6-T4) — NO sub-agent
  branching, NO isinstance checks
- [ ] `git diff <P5c-close> -- src/openharness/mcp/` = empty
- [ ] `git diff <P5c-close> -- src/openharness/compaction/` = empty
- [ ] `git diff <P5c-close> -- src/openharness/protocols/` = empty
- [ ] `git diff <P5c-close> -- src/openharness/tools/base.py` shows
  only the `parent_query` field addition(P6-T2)

**Files**:
- `src/openharness/tools/factory.py`(or `cli.py`)— register
  SpawnAgent instance
- `src/openharness/cli.py`(wire `max_agent_depth`)
- `tests/cli/test_cli.py`(+`TestSubAgentBootstrap`)

**Sub-units**:
- 5a — Default SpawnAgent registration + system_prompt visibility tests
- 5b — End-to-end CLI test(stubbed api_client, real tool dispatch)
- 5c — Invariant `git diff` verification + recorded in commit message

---

### P6-T6: End-to-end smoke + retro

**Description**: Real Qwen run(opt-in via `OPENHARNESS_API_KEY` env,
same gate as Phase 4 / 5 smokes)— parent prompts "use the Agent tool to
read foo.txt and summarize", sub-agent runs, returns summary, parent
finishes. Trace inspected manually + asserted programmatically.
`learnings/phase-6.md` retro written focusing on:**did the recursion
land without dispatch-layer changes?**

**Acceptance**:
- [ ] Integration test(skipped without API key, same pattern as
  P1-T3 / P4 / P5):
  ```bash
  OPENHARNESS_API_KEY=... oh ask "Use the Agent tool to count words in foo.txt"
  ```
  Asserts:
  - Trace contains `tool_dispatch{tool=Agent, agent_depth=0}` →
    sub-agent's `turn_start{agent_depth=1, parent_run_id=R0}` →
    sub-agent's `tool_dispatch{tool=Read, agent_depth=1}` →
    sub-agent's `tool_complete{tool=Read}` →
    sub-agent's `turn_start{agent_depth=1}`(turn 2) →
    sub-agent ends → parent's `tool_complete{tool=Agent}`
  - Parent's `messages` length grows by exactly 2 entries(assistant
    with tool_use + user with tool_result), not by all sub-agent turns
- [ ] `learnings/phase-6.md` written, structured like Phase 5 retro:
  - 1. Data points(commits / tests / coverage delta / lines of code)
  - 2. Per-task takeaway(T1-T6 one-liners)
  - 3. ⭐ Invariant verification result(zero diff on permissions /
    hooks / mcp / compaction / protocols;additive minimal on
    engine + tools/base — third compounding pass of Phase 3 abstraction)
  - 4. Conceptual lesson: tool dispatch as LLM's syscall interface;
    sub-agent as its recursive application;how this informs Phase 7+
  - 5. Real踩坑(at least one expected: contextvar nesting semantics
    around `bind_run` may surprise)
  - 6. Phase 7+ contract predictions(Sandbox preview already exists)
- [ ] Phase 6 DoD checklist all green(decisions/13 §Acceptance)
- [ ] Coverage ≥ 95 % total, mcp/ untouched, new `tools/spawn_agent.py`
  ≥ 95 %

**Files**:
- `tests/integration/test_sub_agent_smoke.py`(new, opt-in via
  API key)
- `learnings/phase-6.md`(new)

**Sub-units**:
- 6a — Smoke test + manual real-run validation
- 6b — `learnings/phase-6.md`
- 6c — DoD closeout + plan checkboxes + boundary acceptance ticks

---

## Checkpoints

After each capability: **human review** of the resulting trace + the
"zero change" invariant — if any of `permissions/`, `hooks/`,
`engine/query.py` dispatch logic, `mcp/`, `compaction/`, `protocols/`
shifted, **stop and re-open the boundary doc**. That's the third
independent test of Phase 3's abstraction failing, not a Phase 6
implementation detail.

## Risks

| Risk | Mitigation |
|---|---|
| `ToolExecutionContext.parent_query` forward-ref triggers import cycle at runtime | `TYPE_CHECKING` guard on `QueryContext` import in `tools/base.py`;mypy verifies symmetric reference works |
| Sub-agent's `bind_run` contextvar collision with parent's | Stack semantics test: bind_run twice, exit inner, assert outer's run_id restored;structlog's contextvars are stack-safe but assert it |
| Sub-agent inherits parent's `hook_registry` and a hook spans both → mutates state | Hooks are stateless by P3 contract;if a stateful hook surfaces in build, surface in T5 as design discussion |
| LLM doesn't naturally invoke `Agent` tool(prompt design) | T5 smoke uses explicit prompt "use the Agent tool to ..."; T6 retro evaluates whether system prompt needs tuning |
| Depth check off-by-one | Test matrix: depth 0→1→2→3 with cap=3,assert exactly 4th spawn refused |
| Infinite recursion if depth field doesn't actually propagate | T3.3c test: sub-agent at depth=N → spawned grandchild's `parent_query.agent_depth=N+1`;regression test on inheritance |

## Risks specifically NOT mitigated(Phase 7+)

- Parallel sub-agent execution(D16.6)
- Per-SpawnAgent tool filtering / catalog subsetting(D16.3)
- Per-SpawnAgent model override
- Streaming sub-agent progress to parent's render layer
- Cycle detection beyond depth bound

## Pointers

- Boundary: [`decisions/13-phase-6-boundary.md`](../decisions/13-phase-6-boundary.md)
- Phase 5 plan(companion structure + invariant pattern): [`tasks/phase-5-plan.md`](./phase-5-plan.md)
- Phase 5 retro(framework input §5 for Phase 6): [`learnings/phase-5.md`](../learnings/phase-5.md)
- Phase 3 retro(predicted sub-agent observability shape): [`learnings/phase-3.md`](../learnings/phase-3.md) §5
- Phase 7 preview(deferred Sandbox): [`tasks/phase-7-preview.md`](./phase-7-preview.md)
- ARCHITECTURE.md §4 phase ordering
