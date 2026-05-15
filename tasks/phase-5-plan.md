# Phase 5 Implementation Plan — MCP Client (Federated Tool Registry)

> Phase 1-4 archive: [`tasks/plan.md`](./plan.md) / [`phase-2-plan.md`](./phase-2-plan.md) /
> [`phase-3-plan.md`](./phase-3-plan.md) / [`phase-4-plan.md`](./phase-4-plan.md).
>
> Boundary contract: [`decisions/11-phase-5-boundary.md`](../decisions/11-phase-5-boundary.md).
> Framing basis: [`tasks/phase-5-preview.md`](./phase-5-preview.md) (D14 source).

## Overview

**Phase 5 goal**: Make `oh ask` consume tools from **external MCP servers**
in addition to the 5 built-in tools. Spawn stdio MCP server subprocesses,
discover their tool catalogs, adapt each MCP tool into a `BaseTool` subclass,
register them in the `ToolRegistry`. The cross-cutting **invariant** to
verify: `permissions/`, `hooks/`, `engine/query.py` **stay unchanged** —
Phase 5 is the test of Phase 3's abstraction.

**Total scope**: ~7-10 days, 6-7 capabilities, ~15-20 commits.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/11-phase-5-boundary.md`](../decisions/11-phase-5-boundary.md) | D15.1 stdio only via official `mcp` SDK; D15.2 Settings + Python API registration; D15.3 `Server.Tool` namespacing (PascalCase); D15.4 failure handling (logical / transport / init); D15.5 tools only (resources/prompts/sampling defer); D15.6 ⭐ per-server trust whitelist for `is_read_only` |

## Task list

### P5-T1: McpServerConfig + Settings + trust whitelist ✅

**Description**: Foundation layer — config dataclass, two new Settings
fields (`mcp_servers`, `trusted_mcp_servers`), env-var parsing, validation.
No subprocess management yet; just the data model the next capabilities
plug into.

**Acceptance**:
- [x] `mcp/config.py` — `McpServerConfig` frozen dataclass (`name`,
  `command: tuple[str, ...]`, `env: dict[str, str]`) — tuple instead of
  list to align with frozen-dataclass immutability intent
- [x] `Settings.mcp_servers: tuple[McpServerConfig, ...] = ()` —
  `OPENHARNESS_MCP_SERVERS` env parses JSON blob into tuple (TOML
  deferred — JSON suffices for Phase 5 MVP)
- [x] `Settings.trusted_mcp_servers: tuple[str, ...] = ()` —
  `OPENHARNESS_TRUSTED_MCP_SERVERS=github,filesystem` comma-separated
  (same parsing shape as `deny_paths` from P3-T3.3b)
- [x] Validation: `name` matches `^[A-Za-z][A-Za-z0-9_-]*$` (used in
  namespacing, must be safe identifier); `command` non-empty
- [x] Tests: parametrized env-var parsing; validation rejection;
  programmatic construction

**Files**:
- `src/openharness/mcp/__init__.py` (new package)
- `src/openharness/mcp/config.py` (new)
- `src/openharness/config/settings.py` (+2 fields + 2 field_validators)
- `tests/mcp/__init__.py`, `tests/mcp/test_config.py` (new)
- `tests/config/test_settings.py` (existing — add Phase 5 field tests)

**Sub-units**:
- 1a — `McpServerConfig` dataclass + tests
- 1b — `Settings.mcp_servers` field + parsing + tests
- 1c — `Settings.trusted_mcp_servers` field + parsing + tests

---

### P5-T2: MCP SDK integration + single-server lifecycle 🔜 NEXT

**Description**: Wrap `mcp.client.stdio` to spawn one MCP server,
complete the `initialize` handshake, expose `list_tools()` + `call_tool()`
async methods, support graceful shutdown. Add the 3 new log events
(`mcp_server_start` / `_stop` / `_error`).

**Acceptance**:
- [ ] `mcp/client.py` — `McpClient(config)` async context manager:
  ```python
  async with McpClient(cfg) as client:
      tools = await client.list_tools()
      result = await client.call_tool(name, arguments)
  ```
- [ ] Init handshake: 5 s timeout per D15 sub-decision (configurable
  via class kwarg)
- [ ] Graceful shutdown: SIGTERM → wait → SIGKILL on shutdown timeout
- [ ] 3 log events emit at correct points; `mcp_server_error.phase` ∈
  `{"init", "call", "shutdown"}`
- [ ] `mcp` Python SDK added as dependency (≥ 1.2.0 per D15.1)
- [ ] Tests: real `@modelcontextprotocol/server-everything` (smoke
  reference server) — list_tools returns something; call_tool round-trip
- [ ] Failure tests: init timeout / subprocess dies mid-call

**Files**:
- `src/openharness/mcp/client.py` (new)
- `pyproject.toml` (+`mcp>=1.2.0`)
- `tests/mcp/test_client.py` (new)

**Sub-units**:
- 2a — Add `mcp` SDK + `McpClient.__aenter__/__aexit__` skeleton + tests
- 2b — `list_tools()` + `call_tool()` + 3 log events + tests
- 2c — Failure paths (init timeout, mid-call subprocess death) + tests

---

### P5-T3: McpToolAdapter — BaseTool subclass

**Description**: Bridge layer — each MCP tool definition becomes a
`BaseTool[InputT]` subclass dynamically. `inputSchema` (JSON Schema) →
`pydantic.create_model()` synthesized input model. `execute()` body
calls `client.call_tool()`. `is_read_only` gated by D15.6 trust whitelist.

**Acceptance**:
- [ ] `mcp/adapter.py` — `McpToolAdapter(BaseTool[Any])`:
  - `__init__(server_name, raw_tool_def, client, trust: bool)`
  - `name = f"{server_name}.{tool_name}"` (PascalCase per D6.4)
  - `is_read_only = raw_tool_def.annotations.readOnlyHint if trust else False`
  - `input_model = _synth_input_model(raw_tool_def.inputSchema)`
  - `execute(args, ctx)` → `client.call_tool(...)` → `ToolResult`
- [ ] `_synth_input_model` supports JSON Schema subset (string/number/
  integer/boolean/array/object, `required`, `enum`); unsupported types
  fall back to `Any`
- [ ] MCP `tools/call` error → `ToolResult(is_error=True, output=<msg>)`
  (errors-as-payload per framing §4.2; P3 D10.4 carryover)
- [ ] Tests:
  - Adapter construction from real MCP tool definitions
  - `_synth_input_model` parametrized on JSON Schema subset
  - Trust on/off → correct `is_read_only`
  - `execute()` round-trip via stub client
  - Server-side error becomes `is_error` ToolResult

**Files**:
- `src/openharness/mcp/adapter.py` (new)
- `tests/mcp/test_adapter.py` (new)

**Sub-units**:
- 3a — `_synth_input_model` (JSON Schema → Pydantic) + tests
- 3b — `McpToolAdapter` class + trust gating + tests
- 3c — `execute()` integration + error → ToolResult + tests

---

### P5-T4: McpClientPool — N-server orchestration

**Description**: Manage N MCP servers per `oh ask` invocation: start all
in parallel, collect their tool catalogs, hand off the merged adapter
list to `ToolRegistry`, shut down on exit. Implements the once-per-query
bounded auto-respawn from D15.4.

**Acceptance**:
- [ ] `mcp/pool.py` — `McpClientPool(configs, trusted_set)`:
  - `async with McpClientPool(...) as pool: adapters = pool.adapters`
  - Parallel startup via `asyncio.gather` (failures isolated — one bad
    server doesn't kill the pool)
  - `pool.adapters` is `list[McpToolAdapter]` ready for registry merge
- [ ] One bad server's init failure → warning log, server marked dead,
  pool continues with remaining servers' tools
- [ ] Mid-call subprocess death → bounded once-per-query respawn; second
  failure → server marked dead for remainder of `oh ask`
- [ ] Tests:
  - 2 servers happy path → 2 adapter sets
  - 1 good + 1 bad init → pool yields good's tools, bad's marked dead
  - Mid-call respawn happens once, second fails permanently

**Files**:
- `src/openharness/mcp/pool.py` (new)
- `tests/mcp/test_pool.py` (new)

**Sub-units**:
- 4a — Pool startup (parallel, isolated failures) + tests
- 4b — Once-per-query respawn logic + tests

---

### P5-T5: CLI bootstrap + trust_source log field

**Description**: Wire `McpClientPool` into `cli._run_ask`:bootstrap
servers before constructing `QueryContext`, merge MCP adapters into the
default ToolRegistry, add `trust_source` field to the `tool_dispatch`
log event so trace consumers see the trust decision path.

**Acceptance**:
- [ ] `cli._run_ask` opens `McpClientPool` as async context before
  building `registry`; merges MCP adapters into the registry built by
  `create_default_tool_registry()`
- [ ] `engine/query.py` `tool_dispatch` log gains `trust_source` field
  ∈ `{"local", "trusted-server", "strict-default"}`. Computed at
  dispatch time from `tool.is_read_only` + (new) `tool.trust_source`
  attribute that `McpToolAdapter` sets;`local` for built-in tools
- [ ] Bootstrap log sequence visible:
  `mcp_server_start` × N → existing `turn_start`
- [ ] Shutdown happens on `oh ask` exit (clean SIGTERM)
- [ ] Duplicate namespaced names across 2 servers → bootstrap hard error
  (early; better than mid-query surprise)
- [ ] Tests: CLI integration via `CliRunner` + stub MCP servers

**Critical invariant verification (D15 cross-cutting)**:
- [ ] `permissions/checker.py` `git diff` vs Phase 4 close = **empty**
- [ ] `hooks/executor.py` `git diff` vs Phase 4 close = **empty**
- [ ] `engine/query.py` only adds the `trust_source` log field — no MCP
  imports, no `isinstance` branching

**Files**:
- `src/openharness/cli.py` (+pool bootstrap + adapter merge)
- `src/openharness/engine/query.py` (+`trust_source` log field; one line)
- `src/openharness/tools/base.py` (+`trust_source: str = "local"` class
  attr on `BaseTool` — defaults make built-ins "local")
- `tests/cli/test_cli.py` (+`TestMcpBootstrap`)

**Sub-units**:
- 5a — `BaseTool.trust_source` attr + tests on built-ins
- 5b — CLI bootstrap wiring + adapter merge + tests
- 5c — `tool_dispatch` log gets `trust_source` field + tests

---

### P5-T6: End-to-end smoke

**Description**: Real MCP server (`@modelcontextprotocol/server-
filesystem` via `npx`) connected via JSON config; `oh ask` invokes
a filesystem tool; trace shows the full flow.

**Acceptance**:
- [ ] Integration test (skipped if `npx` not on PATH):
  ```bash
  OPENHARNESS_MCP_SERVERS='[{"name":"Filesystem","command":["npx","-y","@modelcontextprotocol/server-filesystem","/tmp"]}]'
  oh ask "Use Filesystem.ReadFile on /tmp/test.txt"
  ```
- [ ] stderr JSONL contains: `mcp_server_start (Filesystem)` →
  `turn_start` → `tool_dispatch (Filesystem.ReadFile, trust_source=...)`
  → `tool_complete` → `mcp_server_stop` on exit
- [ ] LLM-visible tool catalog includes `Filesystem.ReadFile` /
  `Filesystem.ListDirectory` / etc.
- [ ] Trust whitelist works: `OPENHARNESS_TRUSTED_MCP_SERVERS=Filesystem`
  → `trust_source=trusted-server`; absent → `strict-default`

**Files**:
- `tests/mcp/test_smoke.py` (new — opt-in via npx availability check)

---

### P5-T7: Coverage + retro

**Description**: Coverage stays ≥ 95 % total, ≥ 90 % per module.
`learnings/phase-5.md` written — focus on whether Phase 3's "uniform
dispatch path" abstraction survived (the boundary doc's invariant).

**Acceptance**:
- [ ] `mcp/` package ≥ 95 % coverage
- [ ] Total coverage ≥ 95 %
- [ ] `learnings/phase-5.md` written:
  - Phase 3 invariant verification result (zero changes to
    permissions / hooks / engine dispatch — true/false?)
  - Trust whitelist design retrospective vs alternatives A/B
  - JSON-RPC subprocess lifecycle pitfalls discovered
  - Phase 6 contract predictions (HTTP transport / resources /
    prompts / hot reload / etc.)
- [ ] Phase 5 DoD checklist all green (decisions/11 §Acceptance)

**Sub-units**:
- 7a — Coverage audit + gap close (if any)
- 7b — `learnings/phase-5.md`
- 7c — DoD closeout + plan checkboxes

---

## Checkpoints

After each capability:**human review** of the resulting trace + the
"zero change" invariant — if any of `permissions/`, `hooks/`,
`engine/query.py` dispatch logic shifted, **stop and re-open the
boundary doc**. That's the Phase 3 abstraction failing, not a Phase 5
implementation detail.

## Risks

| Risk | Mitigation |
|---|---|
| MCP SDK API surface changes mid-Phase | Pin `mcp>=1.2.0,<2.0` to one major version;Phase 6 can bump |
| MCP server output garbage to stdout breaks JSON-RPC framing | Trust SDK's framing implementation; if breaks surface, surface in T2 failure tests |
| `pydantic.create_model` blows up on uncommon JSON Schema features | Document supported subset (D15.8 in boundary draft);fall back to `Any` for unsupported features |
| Phase 3 abstraction violation discovered mid-build (some `isinstance(McpTool)` needed in dispatch) | Boundary doc's invariant verification triggers — stop, re-open boundary, decide whether to amend Phase 3 or evolve MCP design |
| Server crash recovery loops infinitely | D15.4 once-per-query bounded respawn locked at boundary;test asserts the bound |

## Risks specifically NOT mitigated (Phase 6+)

- MCP server hot-reload requires shared state between `oh ask` runs —
  deferred to REPL mode in Phase 6
- Network MCP servers (SSE / HTTP) — deferred (D15.1)
- Multi-process MCP server orchestration (e.g., parent/child servers) —
  Phase 6+ if pattern surfaces

## Pointers

- Boundary: [`decisions/11-phase-5-boundary.md`](../decisions/11-phase-5-boundary.md)
- Phase 5 preview (D14 source): [`tasks/phase-5-preview.md`](./phase-5-preview.md)
- Phase 3 retro predictions §5 (relevant Phase 5 input): [`learnings/phase-3.md`](../learnings/phase-3.md)
- Phase 4 plan (companion structure): [`tasks/phase-4-plan.md`](./phase-4-plan.md)
- MCP spec: <https://spec.modelcontextprotocol.io/>
- Official Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
