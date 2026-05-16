# Phase 7 Boundary — ExecutionEnvironment Abstraction (Sandbox 7a)

> Status: locked at Phase 7 entry, 2026-05-16.
>
> Scope note:**this boundary covers the abstraction layer only**. Real
> Docker substrate(``SandboxExecution`` + Docker SDK + lifecycle +
> Settings/CLI flags + cross-platform CI strategy)defers to **Phase 7b**
> as an independent ~250-LoC phase. Rationale in §"Why abstraction-first"
> below. Sandbox preview that mixed both:
> [`tasks/phase-7-preview.md`](../tasks/phase-7-preview.md).
>
> Rationale + framing:Phase 6 Sub-agent retro `learnings/phase-6.md` §3.1
> validated the third tenant test of Phase 3 abstractions. Phase 7a is the
> **fourth** — an even cleaner one because``HostExecution`` is functionally
> identical to current behavior, making behavior parity the load-bearing
> assertion.

## Triggering observation

Phase 6 Sub-agent landed with 3 additive code lines in engine/query.py
and zero diff on permissions / hooks / mcp / compaction / protocols.
The pattern it established —``ToolExecutionContext.parent_query`` as an
**additive optional field**, consumed only by``SpawnAgent.execute`` —
is exactly the shape ``ExecutionEnvironment`` needs:

- ``QueryContext.execution_env: ExecutionEnvironment`` carries the
  substrate (analogous to ``parent_query``)
- ``ToolExecutionContext.execution_env`` is the additive field engine
  populates(analogous to``parent_query=context``)
- Only``BashTool.execute`` reads it(analogous to``SpawnAgent.execute``
  reading ``parent_query``); Read/Write/Edit/Grep continue using
  ``pathlib`` directly

Phase 7a's job: prove this works **without** the Docker complexity.
``HostExecution`` wraps current``asyncio.create_subprocess_exec`` so
``BashTool`` behavior is byte-identical pre/post refactor. The invariant
becomes:**injecting an alternative substrate** (any structurally
compatible``ExecutionEnvironment``)**works without touching
permission / hook / engine / observability**.

Phase 7b later swaps in real Docker as one specific
``ExecutionEnvironment`` implementation — the abstraction having been
proven correct in 7a means 7b is purely engineering.

---

## In scope

**D17.1 — `ExecutionEnvironment` is a Protocol with `run_command`.**

```python
class ExecutionEnvironment(Protocol):
    async def run_command(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> ProcessResult:
        ...

@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
```

Single method,五 keyword args. Sufficient for Bash's needs(the only
consumer). The shape is intentionally close to ``asyncio.create_subprocess_exec``'s
output so wrapping is a thin layer.

``ProcessResult`` is a small frozen dataclass — separate from
``ToolResult``(which is the LLM-facing result). The substrate returns
the raw process output; ``BashTool.execute`` builds the LLM-facing
``ToolResult`` from it.

**D17.2 — `HostExecution` is the default substrate; wraps current behavior.**

``HostExecution.run_command`` body **is exactly the code currently in
``BashTool.execute``** — ``asyncio.create_subprocess_exec`` + stdout/stderr
collection + timeout handling. **Behavior parity is the load-bearing
assertion** — ``BashTool`` tests pass unchanged after the refactor.

``HostExecution`` is stateless and side-effect-free, so a module-level
singleton is fine:

```python
_HOST_EXECUTION = HostExecution()
```

This singleton becomes the default for ``QueryContext.execution_env``.

**D17.3 — `QueryContext.execution_env` + `ToolExecutionContext.execution_env` (additive).**

Two additive fields, both defaulting to ``HostExecution`` singleton so
**every existing test passes unchanged**:

```python
@dataclass(frozen=True)
class QueryContext:
    ...
    execution_env: ExecutionEnvironment = field(default_factory=lambda: _HOST_EXECUTION)

@dataclass(frozen=True)
class ToolExecutionContext:
    cwd: Path
    parent_query: QueryContext | None = None      # P6-T2
    execution_env: ExecutionEnvironment | None = None  # P7-T2
```

Engine populates ``ToolExecutionContext.execution_env=context.execution_env``
on construction — same pattern as ``parent_query=context``. Tools that
don't read the field(Read/Write/Edit/Grep/SpawnAgent/LoadSkill/MCP)
behave identically.

**D17.4 — `BashTool` is the only consumer; other tools ignore the field.**

``BashTool.execute`` body refactors from inline ``asyncio.create_subprocess_exec``
to ``await ctx.execution_env.run_command(...)``. All other tools'
``execute`` bodies stay untouched — they read filesystem directly via
``pathlib`` or use their own delegation (MCP / SpawnAgent / LoadSkill).

This preserves the **layered extension model** from
``learnings/phase-5b-commands.md`` §3.1:not every tool pays the cost
of the substrate abstraction;only tools that genuinely need it
(arbitrary code execution) do.

**D17.5 — Phase 7b (real Docker) is OUT of scope.**

Phase 7a explicitly does NOT ship:

- ``SandboxExecution`` class with Docker spawn
- ``docker`` Python SDK or shell-out to ``docker`` CLI
- ``Settings.sandbox_*`` fields
- ``--sandbox`` / ``--sandbox-network`` / ``--sandbox-memory`` CLI flags
- Container image management
- Cross-platform CI tests for Docker

These all defer to **Phase 7b** as an independent ~250-LoC phase. Phase
7a's contribution is the abstraction layer + its proof; Phase 7b's is
the substrate engineering.

**Phase 7a verifies the abstraction by injecting a test-only
``FakeExecutionEnvironment``** (in-memory ``ProcessResult`` returns)
and asserting that swapping it in doesn't change anything in
permissions / hooks / engine / observability.

---

## Cross-cutting invariant

**Phase 7a is the FOURTH tenant test of Phase 3's abstraction.** The
following layers must stay unchanged vs Phase 6 close:

- ``permissions/checker.py`` + ``permissions/tier_based.py``
- ``hooks/executor.py`` + ``hooks/registry.py``
- ``engine/query.py`` dispatch loop — EXACTLY ONE additive change
  allowed:``ToolExecutionContext(cwd=..., parent_query=...,
  execution_env=context.execution_env)`` — one extra kwarg on the
  existing constructor call
- ``observability/logging.py``
- ``mcp/`` + ``compaction/`` + ``skills/`` + ``commands/``
- ``protocols/``

Where change IS allowed(all additive):

- ``execution/``(new package):``ExecutionEnvironment`` Protocol +
  ``HostExecution`` + ``ProcessResult``
- ``engine/context.py``:+1 field
- ``tools/base.py``:+1 field on ``ToolExecutionContext``
- ``engine/query.py``:+1 kwarg on the existing
  ``ToolExecutionContext(...)`` call
- ``tools/bash.py``:body refactor only — ``name`` / ``description`` /
  ``input_model`` / ``is_read_only`` all unchanged; ``execute`` body
  delegates to ``ctx.execution_env.run_command``

If during build any "no change allowed" layer needs editing,
**stop and re-open the boundary doc**. Fourth tenant test is more
informative than the previous three because``HostExecution`` is the
identity transform — any behavioral drift in BashTool tests is **direct
evidence** the abstraction shape is wrong.

---

## Out of scope (Phase 7b+)

- **`SandboxExecution`(Docker substrate)** — Phase 7b. Includes
  ``docker`` SDK, container spawn/teardown, bind mount, cgroup limits,
  network policy.
- **Settings + CLI flags for sandbox** — Phase 7b alongside the
  substrate.
- **Per-tool execution_env override** — currently inherited from
  ``QueryContext`` by every tool. If Phase 8+ ever wants e.g.
  "Bash always sandboxed but Edit always host", that's an additive
  enhancement on top of 7a's abstraction.
- **`run_query` / Sub-agent uses execution_env** — sub-agent inherits
  parent's ``execution_env`` via ``dataclasses.replace`` for free; no
  Phase 7 code change needed. The substrate flows through context
  the same way ``api_client`` does.
- **MCP / LoadSkill / SpawnAgent reading execution_env** — these tools
  don't execute OS subprocesses, so the field is irrelevant to them.
- **Streaming process output** — Phase 7 ``ProcessResult`` is a single
  ``(stdout, stderr, exit_code)`` after completion. Streaming partial
  output to the LLM defers if a use case surfaces.
- **Process pools / reuse** — every ``run_command`` call spawns a
  fresh subprocess(host) or container(7b). Reuse defers.
- **Non-shell ExecutionEnvironment** — Phase 7a assumes substrate runs
  shell commands. Other ExecutionEnvironments(e.g., a "remote worker
  pool" that takes structured RPCs) would need a different abstraction;
  not in scope.

---

## Critical decisions (D17.x)

| ID | Decision | Why |
|---|---|---|
| **D17.1** | `ExecutionEnvironment` Protocol with `run_command` + `ProcessResult` dataclass | Single-method protocol matches the only consumer (Bash); ProcessResult separates raw subprocess output from LLM-facing ToolResult |
| **D17.2** | `HostExecution` wraps current `asyncio.create_subprocess_exec` behavior identically | Behavior parity is the load-bearing assertion — all BashTool tests pass unchanged after refactor |
| **D17.3** | `QueryContext.execution_env` + `ToolExecutionContext.execution_env` as additive defaults | Same pattern as Phase 6 `parent_query` — every existing test passes unchanged |
| **D17.4** | Only `BashTool` consumes `execution_env`; other tools ignore | Layered extension model (5b §3.1) — only tools that need it pay the cost |
| **D17.5** | Phase 7b (Docker substrate) deferred to its own ~250-LoC phase | Abstraction is the framework learning; Docker is engineering |

---

## Dependency direction

```
execution/                        (new package)
   ├── base.py                    ← ExecutionEnvironment Protocol + ProcessResult
   └── host.py                    ← HostExecution (wraps current Bash subprocess)
                                    ↓ depends on stdlib asyncio only

engine/context.py                 ← +execution_env field (default HostExecution singleton)
tools/base.py                     ← +ToolExecutionContext.execution_env field
engine/query.py                   ← +1 kwarg on existing ToolExecutionContext(...) call
tools/bash.py                     ← execute() body refactored to use ctx.execution_env

permissions/                      ← ZERO CHANGE (invariant)
hooks/                            ← ZERO CHANGE (invariant)
engine/query.py dispatch loop     ← ZERO CHANGE (only the kwarg additive line)
observability/                    ← ZERO CHANGE (invariant)
mcp/                              ← ZERO CHANGE
compaction/                       ← ZERO CHANGE
skills/                           ← ZERO CHANGE
commands/                         ← ZERO CHANGE
protocols/                        ← ZERO CHANGE
```

`execution/` is downstream of stdlib only;upstream of
``engine/context.py``(consumes ``HostExecution``)and ``tools/bash.py``
(consumes ``ExecutionEnvironment`` via context).

---

## Why abstraction-first (Phase 7a) instead of full Docker (Phase 7)

Three load-bearing reasons:

1. **Framework invariant validation is the bigger learning.** Phase 6
   retro identified that the abstraction has reached a stable plateau.
   Phase 7a is the fourth independent tenant test — it confirms the
   plateau is real by adding a new substrate layer **without** the
   Docker engineering noise.

2. **Docker brings dependency + cross-platform complexity that doesn't
   validate the framework.** `docker` SDK, daemon detection, macOS
   nested VM testing strategy, CI Linux gating — none of these prove
   anything about the harness's abstraction; they're substrate
   engineering. Mixing them in obscures the invariant test.

3. **`HostExecution` is the identity transform**, making it the cleanest
   possible invariant proof. If ``BashTool`` tests pass unchanged
   after the refactor — and they will, because ``HostExecution`` body
   IS the current ``BashTool.execute`` body — then the abstraction is
   correct. Once that's proven, Phase 7b's Docker substrate is purely
   plug-in work.

The preview document itself anticipated this split:

> A 是契约决策，B 是工程实现。两者可以分开 ship。

Phase 7a takes that recommendation. Phase 7b is a future ~250-LoC
phase that will:

- Add `SandboxExecution(ExecutionEnvironment)` with Docker spawn
- Add Settings fields and CLI flags
- Address cross-platform testing strategy
- Verify the fourth tenant invariant holds with a real substrate swap

---

## Acceptance for Phase 7a close-out

- [ ] `ExecutionEnvironment` Protocol + `ProcessResult` dataclass live
  in `src/openharness/execution/base.py`
- [ ] `HostExecution(ExecutionEnvironment)` lives in
  `src/openharness/execution/host.py` and reproduces the current
  `BashTool.execute` subprocess semantics byte-identically
- [ ] `QueryContext.execution_env: ExecutionEnvironment` field added
  with default `HostExecution()` singleton — existing tests pass
  unchanged
- [ ] `ToolExecutionContext.execution_env: ExecutionEnvironment | None
  = None` additive field added — existing tool tests pass unchanged
- [ ] `engine/query.py` constructs `ToolExecutionContext(cwd=...,
  parent_query=..., execution_env=context.execution_env)` — one
  additive kwarg
- [ ] `BashTool.execute` body delegates to
  `ctx.execution_env.run_command(...)`; tests pass unchanged
- [ ] Test harness injects a `FakeExecutionEnvironment` returning a
  canned `ProcessResult`, and BashTool dispatches through it correctly
  — proves the substrate swap works
- [ ] **Structural invariant verification**: `git diff` against Phase
  6 close commit shows:
  - permissions/ → 0 lines
  - hooks/ → 0 lines
  - observability/ → 0 lines
  - mcp/ → 0 lines
  - compaction/ → 0 lines
  - skills/ → 0 lines
  - commands/ → 0 lines
  - protocols/ → 0 lines
  - engine/query.py → 1 additive kwarg only
- [ ] mypy strict + ruff clean + coverage ≥ 95% retained
- [ ] README "Phase 7a — ExecutionEnvironment" section
- [ ] `learnings/phase-7a.md` retro

---

## Pointers

- Preview source (had both 7a and 7b mixed): [`tasks/phase-7-preview.md`](../tasks/phase-7-preview.md)
- Phase 6 boundary (the additive-field template Phase 7 mirrors): [`decisions/13-phase-6-boundary.md`](./13-phase-6-boundary.md)
- Phase 6 retro (where the framework "stable plateau" insight was named): [`learnings/phase-6.md`](../learnings/phase-6.md) §3
- Phase 5b retro (layered extension model): [`learnings/phase-5b-commands.md`](../learnings/phase-5b-commands.md) §3.1
