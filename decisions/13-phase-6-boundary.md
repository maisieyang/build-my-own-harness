# Phase 6 Boundary — Sub-agent (Recursive Tool Dispatch)

> Status: locked at Phase 6 entry, 2026-05-16.
>
> Scope note: **this boundary covers Sub-agent only**. Sandbox(execution
> substrate abstraction)defers to Phase 7 — see
> [`tasks/phase-7-preview.md`](../tasks/phase-7-preview.md) for the
> preserved framing. Rationale for ordering in §"Why Sub-agent before
> Sandbox" below.
>
> Conceptual basis: the realization at Phase 5 close that **tool dispatch
> is the LLM's syscall interface** — Read/Write, Bash, MCP, Sub-agent
> and (later) Skills all share the same `BaseTool → ToolRegistry →
> dispatch loop` primitive. Sub-agent is the recursive application of
> that primitive: `run_query` invokes itself through a tool, with an
> isolated `QueryContext` slice.

## Triggering observation

Phase 5 (MCP) verified the cross-cutting invariant — `permissions/`,
`hooks/`, `engine/query.py` dispatch logic stayed unchanged when a
completely new tool source landed. Phase 5c (Skills) is the second
independent test of the same abstraction.

Phase 6 Sub-agent is the **third compounding test**, and the most
interesting one: it's not a new *tool source* but a new *control flow
shape*. Sub-agent ≠ "wrap an external thing as BaseTool"; sub-agent =
"the agent loop itself becomes a tool". If this lands clean — `run_query`
calling `run_query` through a single `SpawnAgent` BaseTool, with no
dispatch-side code knowing it's recursion — then Phase 3's abstraction is
load-bearing and we own a primitive (the dispatch loop) sufficient for
**every** capability extension the harness will ever need.

**The boundary doc's job**: lock the contract that says — sub-agent
implementation lives entirely in (a) a new `BaseTool` subclass, (b) a
small additive expansion of `ToolExecutionContext`, (c) two new
`QueryContext` fields for depth tracking. **Engine dispatch, permissions,
hooks, observability dispatch path, MCP, compaction — all zero diff.**

---

## In scope

**D16.1 — `SpawnAgent` is a `BaseTool` subclass (invariant test #3).**

Sub-agent enters the LLM's catalog as any other tool:

```python
class SpawnAgent(BaseTool[SpawnAgentInput]):
    name = "Agent"                   # PascalCase per D6.4
    description = "Delegate a sub-task to a fresh agent loop with its own context."
    input_model = SpawnAgentInput    # {description: str, prompt: str}
    is_read_only = False             # AuthZ Tier 3 strict path
    trust_source = "local"           # P5-T5 provenance

    async def execute(self, args, exec_ctx) -> ToolResult:
        # Construct sub_context from exec_ctx.parent_query, call run_query,
        # collect final assistant text into ToolResult.output.
```

This is the invariant statement: a sub-agent is a function the LLM calls,
nothing more. The dispatch loop, permission checker, hook chain all see
`Agent` exactly the way they see `Read` or `GitHub.CreateIssue`. The fact
that `execute` internally re-enters `run_query` is an implementation
detail of one specific `BaseTool` — same shape as `BashTool` internally
spawning a subprocess or `McpToolAdapter` internally doing JSON-RPC.

**D16.2 — `QueryContext` inheritance: share most, override three.**

The sub-agent's `QueryContext` is built from the parent's by
`dataclasses.replace(...)` overriding exactly three fields:

| Field | Sub-agent value | Why |
|---|---|---|
| `system_prompt` | per-SpawnAgent-instance, fallback to parent's | Sub-agent role differs from supervisor (e.g., "you are a research sub-agent") |
| `max_turns` | per-SpawnAgent-instance (default 20) | Independent budget — sub-agent burning turns must not deplete parent |
| `agent_depth` | `parent.agent_depth + 1` | Recursion bound (D16.5) |

Inherited as-is(NOT touched):

- `api_client` — same provider, same auth
- `tool_registry` — full inheritance(D16.3, see below)
- `permission_checker` — Tier 1-3 baseline applies to sub-agent identically
- `hook_registry` — Pre/Post hooks fire inside sub-agent same as parent
- `cwd` — sub-agent operates in the same workspace
- `model` — same model(future enhancement: per-SpawnAgent override for Haiku-class economy tasks; deferred)
- `max_tokens` — per-API-call budget unchanged
- `permission_mode` — DEFAULT / AUTO / DRY_RUN propagates(sub-agent in DRY_RUN does not silently mutate)
- `max_agent_depth` — propagates so depth check uses same cap at every level

**D16.3 — Tool registry: full inheritance, no filtering(Phase 6).**

The sub-agent sees the same `ToolRegistry` as the parent — including
other `SpawnAgent` instances. Tool subsetting("research sub-agent can
only use Read/Grep") defers to Phase 7+ when:

- Multiple `SpawnAgent` instances exist with differentiated capabilities
- A real prompt-injection scenario surfaces where catalog narrowing is
  load-bearing(today's strict-default + permission Tier 3 already
  guards mutating tools)

Phase 6 ships the simple, recursion-bounded model. Filtering is an
additive enhancement that does not break this contract.

**D16.4 — Result extraction: final assistant text → `ToolResult.output`.**

`SpawnAgent.execute` consumes the sub-agent's event stream and concatenates
text content from the final `ApiMessageCompleteEvent.message` into
`ToolResult.output`. Internal events(tool dispatches, partial deltas,
nested sub-agent activity)are **not surfaced into the parent's stream
and are not appended to the parent's `messages` list** — that's the
whole point of context isolation:

- Parent's conversation history grows by exactly one `tool_use` +
  `tool_result` pair, regardless of how many turns the sub-agent took
- Token budget hygiene: sub-agent doing 50 turns does not balloon
  parent's context to 50 turns' worth of tool traffic
- Same compaction story as MCP tools(P5): parent sees only the final
  digest;internal noise stays at the sub-agent layer

`is_error` semantics:

- Sub-agent reaches `end_turn` cleanly → `ToolResult(output=<text>, is_error=False)`
- Sub-agent hits `LoopLimitExceeded`(max_turns budget) → `ToolResult(output="sub-agent exceeded max_turns=N without completing", is_error=True)` — parent's LLM sees the failure and can pivot
- Sub-agent raises any other `OpenHarnessError`(API failure / hook crash / tool crash) → propagates as `ToolError` from `SpawnAgent.execute`, parent's dispatch wraps it normally(errors-as-payload, framing §4.2)
- Programming error inside sub-agent's `run_query` → propagates(D8.5 carry-over)

**D16.5 — Depth bound: `max_agent_depth = 3` default.**

`Settings.max_agent_depth: int = 3`(env `OPENHARNESS_MAX_AGENT_DEPTH`).
Propagated into every `QueryContext` so every level of nesting reads
the same cap.

`SpawnAgent.execute` checks **before** building the sub-context:

```python
if exec_ctx.parent_query.agent_depth + 1 > exec_ctx.parent_query.max_agent_depth:
    return ToolResult(
        is_error=True,
        output=f"max agent depth ({max_depth}) reached; cannot spawn further sub-agents",
    )
```

Top-level `oh ask` runs at `agent_depth=0`. With default cap of 3, the
chain is: parent(0) → spawn child(1) → spawn grandchild(2) → spawn
great-grandchild(3) → 4th spawn refused. Reasoning: 3 is enough for
realistic delegation patterns(supervisor → research → leaf tool calls);
deeper structures usually indicate prompt design problems or fork-bomb
shape — fail loudly, surface to parent LLM, let it adapt.

No special engine handling — the check lives entirely inside
`SpawnAgent.execute`. Engine dispatch loop is depth-agnostic.

**D16.6 — Serial dispatch only(parallel deferred).**

Parent dispatches sub-agents serially, same as D6.3 serial tool
dispatch. If the LLM emits two `Agent` tool_use blocks in one turn, they
run one after the other(matching every other tool's behavior). Parallel
sub-agent execution defers to Phase 7+ where:

- A real latency-bound use case surfaces
- `asyncio.gather(...)` orchestration semantics can be designed against
  observability + hook ordering(parallel sub-agents trampling each
  other's logs is the open problem)

**D16.7 — Observability: `parent_run_id` + `agent_depth` on every event.**

Phase 3 retro predicted this(`learnings/phase-3.md` §5). Phase 6
cashes it:

- `bind_run()` detects existing `run_id` in contextvars; if present,
  the new run_id stashes the old as `parent_run_id` in the bound
  context. Stack semantics — nested `bind_run` calls correctly chain.
- `agent_depth` from the active `QueryContext` is bound to all log
  events emitted by the sub-agent's `run_query`(turn_start /
  tool_dispatch / tool_complete / etc.).
- Trace consumers see: top-level events have `run_id=R1`, `agent_depth=0`,
  no `parent_run_id`. Sub-agent's events have `run_id=R2`,
  `parent_run_id=R1`, `agent_depth=1`. Stitching is a self-join on
  `run_id ↔ parent_run_id`.

No new log event types. Existing 13 events(Phase 5 close)gain
`parent_run_id`(optional)and `agent_depth`(int)fields. Same
additive shape as Phase 5's `trust_source` field on `tool_dispatch`.

**D16.8 — `ToolExecutionContext.parent_query` additive field.**

The mechanical question Phase 6 forces: how does `SpawnAgent.execute`
get hold of the parent's `QueryContext`?

The answer is the smallest possible expansion:

```python
@dataclass(frozen=True)
class ToolExecutionContext:
    cwd: Path
    parent_query: QueryContext | None = None   # P6-T2: sub-agent access
```

`engine/query.py` constructs `ToolExecutionContext(cwd=context.cwd,
parent_query=context)` — a one-line change to the existing
constructor. Tools that don't read `parent_query`(every existing tool
except `SpawnAgent`)behave identically. `SpawnAgent.execute` reads
`exec_ctx.parent_query` and builds `sub_context` via
`dataclasses.replace(...)`.

This is the **only structural change Phase 6 introduces to the dispatch
machinery**, and it's strictly additive(default `None`, optional
forward-ref to avoid import cycle). The invariant from Phase 5 — "no
isinstance branching on tool type" — holds: the engine treats `Agent`
exactly like `Read`. It just happens to fill in one more field of the
execution context the loop already constructs.

**Rejected alternatives**:

| Alternative | Why rejected |
|---|---|
| Pass `QueryContext` via `BaseTool.execute(args, exec_ctx, query_ctx)` | Breaks the `BaseTool.execute` signature — every existing tool changes |
| `SpawnAgent` constructor captures `QueryContext` | Circular dependency — `QueryContext.tool_registry` holds `SpawnAgent`, which would hold `QueryContext` |
| Module-level contextvar for active `QueryContext` | Hidden coupling; harder to test; rejected in Phase 2 for same reasons |
| Special-case `SpawnAgent` in `engine._dispatch_one` | Direct invariant violation |

---

## Cross-cutting invariant

**Phase 6 Sub-agent must not add a new dispatch path.** The following
layers stay unchanged in `src/openharness/`:

- `permissions/checker.py` — no `isinstance(SpawnAgent)` branches; sub-agent
  enters AuthZ identically to any other write tool
- `hooks/executor.py` — PreToolUse / PostToolUse / PreApiCall / PostApiCall /
  OnError fire identically for sub-agent dispatch and for sub-agent's
  own internal turns(hooks are oblivious to depth)
- `engine/query.py` dispatch loop — `_dispatch_one` looks up tools by
  name and calls `BaseTool.execute(...)`; no sub-agent-aware branching
- `mcp/` package — zero change; MCP and sub-agent are orthogonal extension
  axes meeting at the BaseTool interface
- `compaction/` — Phase 4 truncation hooks fire inside sub-agent's
  `run_query` automatically because hook_registry inherits

Where change IS allowed(all additive):

- `tools/spawn_agent.py`(new) — `SpawnAgent(BaseTool[...])` class
- `tools/base.py` — `ToolExecutionContext.parent_query: QueryContext | None = None`
- `engine/context.py` — `QueryContext.agent_depth: int = 0`,
  `QueryContext.max_agent_depth: int = 3`
- `engine/query.py` — exactly one line: `ToolExecutionContext(cwd=..., parent_query=context)`
- `config/settings.py` — `Settings.max_agent_depth: int = 3` field
- `cli.py` — register default `SpawnAgent` instance into the registry
- `observability/logging.py` — `bind_run` detects existing run_id,
  binds `parent_run_id`;`bind_turn` or a new helper binds `agent_depth`

If during build any "no change allowed" layer needs editing, **stop and
re-open the boundary doc**. That's the third independent test of
Phase 3's abstraction failing, not a Phase 6 implementation detail.

---

## Out of scope(Phase 7+)

- **Sandbox / `ExecutionEnvironment` abstraction.** Preserved in
  [`tasks/phase-7-preview.md`](../tasks/phase-7-preview.md);Phase 7 entry.
- **Per-SpawnAgent tool filtering / catalog subsetting.** Phase 6
  inherits full registry. Filtering at Phase 7+ when use case surfaces.
- **Per-SpawnAgent model override.** All sub-agents use parent's model
  in Phase 6. Haiku-class economy delegation is a Phase 7+ tuning lever.
- **Parallel sub-agent execution.** D16.6 defers. Serial only.
- **Sub-agent result streaming back to parent's render layer.** Phase 6:
  parent sees a single `ToolResult.output`. No intermediate progress
  events surfaced(would require new protocol event types and conflicts
  with context isolation). Phase 7+ if interactive UX demands it.
- **Settings-driven sub-agent declarations**(YAML / TOML defining
  custom SpawnAgent types). Phase 6 ships one default `Agent`;extra
  types are programmatic API only. Settings-driven catalog defers to
  Phase 7+ alongside Skills-style discovery.
- **Sub-agent suspend / resume across CLI invocations.** Phase 6 is
  per-query lifecycle, matching MCP pool model.
- **Cycle detection beyond depth bound.** Two sub-agents calling each
  other in a flat loop without exceeding depth bound is technically
  possible(A spawns B spawns A spawns B at depth 0→1→2→3). The depth
  cap is the only guard. Topological cycle detection deferred.

---

## Critical decisions(D16.x)

| ID | Decision | Why |
|---|---|---|
| **D16.1** | `SpawnAgent` is a `BaseTool` subclass | Recursive application of Phase 3 dispatch primitive;invariant test #3 |
| **D16.2** | Sub-agent inherits `QueryContext`,overrides only `system_prompt` / `max_turns` / `agent_depth` | Minimal slice;sub-agent shares the harness's collaborator graph, differs only on prompt + budget + depth |
| **D16.3** | Full tool registry inheritance(no filtering) | Phase 6 simplicity;filtering is additive, defer until use case |
| **D16.4** | Result = final assistant text → `ToolResult.output`;internal events not surfaced | Context isolation;parent's conversation grows by one tool_use/tool_result pair regardless of sub-agent length |
| **D16.5** | `max_agent_depth = 3` default(env override) | Realistic delegation patterns are ≤ 3 levels;deeper indicates design problem or fork-bomb shape |
| **D16.6** | Serial sub-agent dispatch | D6.3 carryover;parallel deferred to Phase 7+ |
| **D16.7** | `parent_run_id` + `agent_depth` on every log event(additive) | Phase 3 retro prediction;trace stitching via self-join |
| **D16.8** | `ToolExecutionContext.parent_query` additive field for sub-agent context access | Smallest possible expansion;preserves `BaseTool.execute` signature and dispatch loop |

---

## Dependency direction

```
tools/spawn_agent.py              ← new: SpawnAgent(BaseTool)
   ↓ imports
engine/context.py                 ← +agent_depth, +max_agent_depth fields
engine/query.py                   ← run_query is reused (recursion); +1 line on exec_context construction
tools/base.py                     ← +ToolExecutionContext.parent_query field
config/settings.py                ← +max_agent_depth field
cli.py                            ← bootstrap: register default SpawnAgent("Agent")
observability/logging.py          ← bind_run detects nested run_id, binds parent_run_id + agent_depth

permissions/checker.py            ← ZERO CHANGE (invariant verification)
hooks/executor.py                 ← ZERO CHANGE (invariant verification)
engine/query.py dispatch logic    ← ZERO CHANGE (only the exec_context construction additive line)
mcp/                              ← ZERO CHANGE
compaction/                       ← ZERO CHANGE
protocols/                        ← ZERO CHANGE (no new event types)
```

`SpawnAgent` is downstream of `tools/base.py`(it's a BaseTool subclass)
and upstream of `cli.py`(bootstrap registers a default instance). The
recursion target — `run_query` — sits at the same layer as the caller;
no special "engine knows about sub-agents" wiring exists. The five
"zero change" layers are the contract this phase verifies.

---

## Sub-decisions deferred to build

Three questions tentatively answered now, locked at build time:

- **Should `SpawnAgent` accept a `tool_filter: set[str] | None` constructor
  kwarg as forward-compat for D16.3 future filtering?** Tentative
  **yes** — defaults to `None`(full inheritance), so the future
  enhancement is a no-op constructor argument. Revisit at build if
  signature gets noisy.
- **Where does `SpawnAgent` live: `tools/spawn_agent.py` or new
  `agents/` package?** Tentative `tools/spawn_agent.py` — it's a
  BaseTool subclass, lives where its parent lives. New `agents/`
  package is unnecessary infrastructure for one class. Revisit if
  Phase 7+ adds related types(supervisor pattern, planner pattern).
- **Should `parent_run_id` chain transitively in nested sub-agents
  (grandchild knows parent + grandparent), or only point at immediate
  parent?** Tentative **immediate parent only** — trace consumers can
  reconstruct the chain via repeated self-joins. Storing the full
  ancestor chain on every event is redundant.

---

## Why Sub-agent before Sandbox(reordering rationale)

`tasks/phase-7-preview.md`(originally phase-6-preview.md)framed
Sandbox as Phase 6. The reorder reasoning:

1. **Invariant compounding**. MCP(P5)was the first invariant test,
   Skills(P5c)the second. Sub-agent is the cheapest, cleanest third
   test — pure framework recursion, no new substrate. Locking the
   abstraction with three independent passes before introducing
   Docker(a substrate with platform variance, image management, real
   ops complexity)gives the framework a stronger foundation.

2. **Sandbox needs a use case to design `ExecutionEnvironment`
   against**. Sub-agent + MCP + Skills together exercise enough of the
   tool dispatch surface to surface what `ExecutionEnvironment`
   actually needs(`run_command` only? `read_file` too? networking
   policy shape?). Designing Phase 7 Sandbox after Phase 6 Sub-agent
   means the interface absorbs real consumption patterns rather than
   speculative ones.

3. **Engineering risk profile**. Sub-agent is ~100-200 lines of pure
   Python with strong test coverage from the existing harness(mock
   api_client, sub-agent runs deterministically). Sandbox is Docker
   lifecycle + cross-platform(macOS LinuxKit VM)+ image mgmt + cgroup
   policy — at least 5× the engineering and 10× the platform-specific
   surface area. Sub-agent first respects the project's "compounding
   on a stable framework" thesis.

4. **Sub-agent is the strongest demonstration of the project's central
   insight**: the LLM's tool-dispatch contract is the syscall interface
   for everything an agent can do. Sandbox is a confinement substrate;
   sub-agent is the *primitive* the substrate confines. Locking the
   primitive first is correct precedence.

`tasks/phase-7-preview.md` preserves the Sandbox framing intact for
Phase 7 entry.

---

## Acceptance for Phase 6 close-out(template)

- [ ] `oh ask` exposes an `Agent` tool the LLM can invoke
- [ ] Real run: parent prompts LLM "use the Agent tool to summarize X",
  sub-agent runs, returns text, parent's LLM consumes the result and
  finishes — observable as ONE `tool_use`/`tool_result` pair in
  parent's `messages` list
- [ ] `agent_depth=3` cap enforced: 4th nested spawn returns
  `is_error=True` ToolResult with depth-exceeded message
- [ ] Logs from sub-agent's `run_query` carry `parent_run_id` and
  `agent_depth=N` fields;parent's logs carry no `parent_run_id`
- [ ] Hook chain fires inside sub-agent's run — PreToolUse / PostToolUse
  / PreApiCall / PostApiCall / OnError all observable at sub-agent level
- [ ] PermissionChecker enforces Tier 1-3 inside sub-agent — sub-agent
  attempting `Write` on ~/.ssh/id_rsa denied identically to parent
- [ ] `permissions/checker.py`, `hooks/executor.py`, `mcp/`,
  `compaction/`, `protocols/` show **zero diff** vs Phase 5c close
- [ ] `engine/query.py` shows only the `parent_query=context` addition
  on `ToolExecutionContext` construction(one line) — no sub-agent
  branching
- [ ] `tools/base.py` shows only the additive `parent_query` field on
  `ToolExecutionContext`
- [ ] LoopLimitExceeded inside sub-agent surfaces as
  `ToolResult(is_error=True)` to parent — parent's LLM sees and can adapt
- [ ] `Settings.max_agent_depth` env override works
  (`OPENHARNESS_MAX_AGENT_DEPTH=1` reduces cap;`=0` disables spawning)
- [ ] mypy strict + ruff clean + coverage ≥ 95 % retained

---

## Pointers

- Phase 5 boundary(invariant template + first compounding test):
  [`decisions/11-phase-5-boundary.md`](./11-phase-5-boundary.md)
- Phase 5c Skills boundary(second compounding test):
  [`decisions/12-phase-5c-skills-boundary.md`](./12-phase-5c-skills-boundary.md)
- Phase 5 retro(framework-level confirmation + Phase 6 input §5):
  [`learnings/phase-5.md`](../learnings/phase-5.md)
- Phase 3 retro(predicted Phase 6 sub-agent observability shape):
  [`learnings/phase-3.md`](../learnings/phase-3.md) §5
- Phase 7 preview(deferred Sandbox framing):
  [`tasks/phase-7-preview.md`](../tasks/phase-7-preview.md)
- Phase 2 boundary(D6.3 serial dispatch carryover):
  [`decisions/06-phase-2-boundary.md`](./06-phase-2-boundary.md)
- Phase 2 boundary(D8.5 error semantics for sub-agent failure modes):
  [`decisions/07-base-tools.md`](./07-base-tools.md)
