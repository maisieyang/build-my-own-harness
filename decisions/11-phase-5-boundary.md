# Phase 5 Boundary — MCP (Federated Tool Registry)

> Status: locked at Phase 5 entry, 2026-05-15.
>
> Scope note: **this boundary covers MCP only**. Slash command (preview D15)
> stays in `tasks/phase-5-preview.md` until MCP retro reveals what
> `ModeBundle` actually needs — at which point a separate
> `decisions/12-phase-5b-boundary.md` opens.
>
> Rationale + framing: see `tasks/phase-5-preview.md` (D14 MCP section) +
> Three-Axis on `is_read_only` truth source captured in chat 2026-05-15.

## Triggering observation

Phase 1-3 built a tool dispatch path that is **agnostic to where the tool
runs**: `PermissionChecker` only reads `tool.is_read_only`; the hook chain
only sees `BaseTool` interface; `tool_dispatch` / `tool_complete` log
events fire from `engine/query.py` without knowing what's behind the
`BaseTool`. This was the implicit hypothesis of P3-T4 (hook) and P3-T3
(permission Tier 3): **the abstraction is uniform**.

Phase 5 MCP is the **first external consumer** of that hypothesis. If
landing MCP requires *any* change to `permissions/checker.py`,
`hooks/executor.py`, or `observability/logging.py` dispatch-side, the
Phase 3 abstraction was wrong and the harness pays interest now. The
boundary doc's job is to lock the contract that says: **only `tools/` +
`config/` + `cli.py` may change. Permission / hook / observability are
zero-change.**

---

## In scope

**D15.1 — Transport: stdio only.**

Phase 5 supports a single MCP transport: spawn subprocess, JSON-RPC over
stdin/stdout. Anthropic's official `mcp` Python SDK (`mcp.client.stdio`)
provides the transport — **we do not write our own JSON-RPC client**.
SSE / WebSocket / HTTP transports defer to Phase 6 hardening.

**D15.2 — Server registration: Settings + Python API.**

Two registration paths:

- `Settings.mcp_servers: list[McpServerConfig]` (env
  `OPENHARNESS_MCP_SERVERS=<TOML or JSON blob>`).
- Python API: `registry.register_mcp_server(McpServerConfig(...))` for
  testing and programmatic use.

`McpServerConfig` shape:

```python
@dataclass(frozen=True)
class McpServerConfig:
    name: str                       # "github" — used as namespace prefix
    command: list[str]              # ["npx", "@modelcontextprotocol/server-github"]
    env: dict[str, str] = {}        # API tokens etc.
```

CLI subcommand (`oh mcp add <name>`) defers to Phase 5 polish; Settings
+ API is sufficient for the MVP loop.

**D15.3 — Catalog merge: server-as-namespace.**

MCP tool exposed to LLM as `<ServerName>.<ToolName>` in PascalCase
(consistent with D6.4). Examples:

- `Filesystem.ReadFile`
- `GitHub.CreateIssue`

Local tool names stay un-namespaced (`Read`, `Bash`, …) — accepted
asymmetry. Reasoning: local 5 tools are framework baseline; adding
`Local.Read` adds noise without gain.

Duplicate detection in `ToolRegistry.register` continues to fire on
name collision (two MCP servers exposing identical namespaced names →
hard error at bootstrap).

**D15.4 — Failure handling: ToolError default + bounded auto-respawn.**

Three failure modes mapped explicitly:

| Failure | Behavior |
|---|---|
| **Logical** (server returned `tools/call` error) | `ToolResult(is_error=True, output=<server error message>)` — fed to LLM verbatim (errors-are-payload, per `learnings/phase-3-framing.md` §4.2) |
| **Transport** (pipe broken / subprocess died) | One bounded respawn attempt; if 2nd call also fails, `ToolResult(is_error=True, output="<MCP server <name> unavailable>")` — LLM sees and can pivot |
| **Init handshake** (server fails to start or refuses `initialize`) | Warning log, server marked dead, its tools not registered. Other servers continue. `oh ask` does NOT crash on one bad server. |

Bounded respawn is **once per query**, not per call. After one respawn,
the server stays dead for the rest of `oh ask`. Reasoning: if respawn
didn't fix it, infinite respawning hides bugs.

**D15.5 — MCP primitive scope: Tool only.**

Phase 5 implements `tools/list` + `tools/call`. The three other MCP
primitives defer:

| Primitive | Defer to | Why |
|---|---|---|
| Resource | Phase 6+ | "data → LLM" can wait; RAG handles it locally now |
| Prompt | Phase 7+ | Slash command (preview D15) covers most use cases |
| Sampling | Phase 6+ | Reverse LLM call, niche, complex |

**D15.6 — `is_read_only` truth source: per-server trust whitelist.** ⭐

The Three-Axis lock of this entry. MCP tool definitions may declare
`annotations.readOnlyHint: bool`, but harness cannot independently
verify the claim.

- `Settings.trusted_mcp_servers: list[str] = []` (env
  `OPENHARNESS_TRUSTED_MCP_SERVERS=github,filesystem` comma-separated —
  same parse shape as `deny_paths`).
- `McpToolAdapter.__init__(server_name, raw_tool_def, trust: bool)`:
  - `trust=True` → `is_read_only = raw_tool_def.annotations.readOnlyHint or False`
  - `trust=False` → `is_read_only = False` (force strict path through
    Tier 3)
- `PermissionChecker` is **unchanged** — it still reads `tool.is_read_only`
  and dispatches Tier 3 accordingly. This is the invariant verification:
  the question "is this tool safe?" is answered before the checker
  receives it, by the adapter's trust gating.
- `observability/logging.py` `tool_dispatch` event gains field
  `trust_source: str` ∈ `{"local", "trusted-server", "strict-default"}`
  — trace consumers see the decision path.

Rationale: same shape as Tier 2 `OPENHARNESS_DENY_PATHS` (P3-T3 D13.2) —
user-controlled trust boundary. Reversible: start with empty whitelist,
add servers as trust accrues. Avoids the all-or-nothing trap of
options A (everything strict) and B (everything trusted).

---

## Cross-cutting invariant

**Phase 5 MCP must not add a new dispatch path.** The following layers
must remain unchanged in `src/openharness/`:

- `permissions/checker.py` — no `isinstance(McpTool)` branches
- `hooks/executor.py` — PreToolUse/PostToolUse fires identically for
  local and MCP tools
- `engine/query.py` dispatch loop — `_dispatch_one` looks up tools by
  name and calls `BaseTool.call(...)`; **no MCP-aware branching**

Where change IS allowed:

- `tools/` — `McpToolAdapter(BaseTool)` is a new subclass
- `mcp/` — new package: SDK wrapper, lifecycle, JSON-RPC client state
- `config/settings.py` — +2 fields (`mcp_servers`, `trusted_mcp_servers`)
- `cli.py` — bootstrap step: connect servers, register their tools
- `observability/logging.py` — +`trust_source` field on `tool_dispatch`

If during build any "no change allowed" layer needs editing,
**stop and re-open the boundary doc**. That's the Phase 3 abstraction
failing, not a Phase 5 implementation detail.

---

## Out of scope (Phase 6+)

- **SSE / WebSocket / HTTP transports.** Phase 6 hardening when remote
  MCP servers (enterprise) become a real ask.
- **Hot reload of MCP catalog.** A server adding/removing tools mid-session
  is not supported; catalog is bootstrap-frozen.
- **MCP server health checks / metrics.** No periodic ping; only failure
  on actual call. Phase 6+ if needed.
- **Slash command / ModeBundle.** Separate boundary doc when MCP retro
  surfaces concrete `catalog_filter` requirements.
- **Resource / Prompt / Sampling primitives.** D15.5 defers.
- **Per-tool permission override beyond `is_read_only`.** If a user wants
  `GitHub.CreateIssue` ALLOW while `GitHub.DeleteRepo` DENY on the same
  server, today's answer is "add the server to deny_paths or split into
  two servers." Finer-grained tool-level deny defers.
- **MCP client connection pooling across `oh ask` invocations.** Each
  CLI invocation spawns and tears down servers. Phase 6+ persistent
  daemon if launch latency becomes a real bottleneck.

---

## Critical decisions (D15.x)

| ID | Decision | Why |
|---|---|---|
| **D15.1** | stdio only via official `mcp` SDK | 80 % of real-world MCP deployments; don't reinvent JSON-RPC |
| **D15.2** | Settings + Python API registration | Mirrors `deny_paths` env pattern; CLI subcommand is polish |
| **D15.3** | Server-as-namespace (`GitHub.CreateIssue`) | LLM sees method-call shape; duplicate detection works at registry layer |
| **D15.4** | ToolError default + once-per-query auto-respawn | Errors-are-payload (framing §4.2); bounded recovery avoids infinite respawn |
| **D15.5** | Tool primitive only | MVP scope; Resource/Prompt/Sampling all defer |
| **D15.6** | Per-server trust whitelist for `is_read_only` | Same shape as Tier 2 `deny_paths`; user-controlled trust boundary; PermissionChecker zero-change |

---

## Dependency direction

```
mcp/                              (new package)
   ├── config.py                  ← McpServerConfig dataclass
   ├── client.py                  ← wraps mcp.client.stdio; lifecycle
   ├── pool.py                    ← McpClientPool (N servers per query)
   └── adapter.py                 ← McpToolAdapter(BaseTool)
                                    ↓ depends on tools/base.py

config/settings.py                ← +mcp_servers, +trusted_mcp_servers
cli.py                            ← bootstrap: pool.connect_all() → register tools
observability/logging.py          ← +trust_source field on tool_dispatch

permissions/checker.py            ← ZERO CHANGE (invariant verification)
hooks/executor.py                 ← ZERO CHANGE (invariant verification)
engine/query.py                   ← ZERO CHANGE (invariant verification)
```

`mcp/` is downstream of `tools/` (adapter is a BaseTool subclass) and
upstream of `cli.py` (bootstrap registers). The three "zero change"
layers are the contract this phase verifies.

---

## Sub-decisions deferred

Three open questions surfaced during Three-Axis but not locked now —
answers depend on what build reveals:

- **Where does `McpToolAdapter` live: `tools/` or `mcp/`?** Tentative
  `mcp/adapter.py` so all MCP-coupled code clusters; revisit if
  `tools/` import becomes awkward.
- **Should MCP tool input go through Pydantic re-validation client-side?**
  Tentative **no** — trust server's reported schema, let it reject bad
  input. Cheap JSON-RPC round-trip; better error messages from source.
  Revisit if smoke tests reveal flaky cases.
- **Bootstrap timeout for `tools/list`?** Tentative **5 s per server**;
  exceeding → server marked dead. Revisit if real-world MCP servers
  have legitimately slow init.

---

## Acceptance for Phase 5 close-out

- [ ] `oh ask` connects to a real stdio MCP server
  (`@modelcontextprotocol/server-filesystem` as smoke target)
- [ ] MCP tools appear in LLM-visible catalog with `Server.Tool` names
- [ ] LLM-invoked MCP tool: dispatch → JSON-RPC `tools/call` → result
  fed back through unchanged hook + permission + observability path
- [ ] `tool_dispatch` log carries `trust_source` field with correct
  value per server trust status
- [ ] PermissionChecker rejects non-trusted server's tool when
  `is_read_only=False` triggers strict path on a sensitive cwd
- [ ] Hook chain (PreToolUse + PostToolUse) fires on MCP tool call with
  no MCP-aware code in `hooks/`
- [ ] Server crash mid-query → one auto-respawn → if respawn fails,
  `ToolError` fed to LLM, `oh ask` completes
- [ ] Two MCP servers exposing colliding names → bootstrap hard error
- [ ] `permissions/checker.py`, `hooks/executor.py`, `engine/query.py`
  show **zero diff** vs Phase 4 close (the invariant)
- [ ] mypy strict + ruff clean + coverage ≥ 95 % retained

---

## Pointers

- Phase 5 preview (D14 source, D15 deferred): [`tasks/phase-5-preview.md`](../tasks/phase-5-preview.md)
- Phase 3 hook contract that MCP reuses: [`decisions/08-phase-3-boundary.md`](./08-phase-3-boundary.md) D13.1
- Phase 3 permission Tier 3 (where `is_read_only` is consumed): [`decisions/08-phase-3-boundary.md`](./08-phase-3-boundary.md) D13.2 + D13.3
- Phase 3 retro (framework-as-RPC + framing §4.2 errors-as-payload): [`learnings/phase-3.md`](../learnings/phase-3.md)
- Tier 2 `deny_paths` pattern that D15.6 mirrors: [`decisions/08-phase-3-boundary.md`](./08-phase-3-boundary.md) D13.2
- MCP spec: <https://spec.modelcontextprotocol.io/>
- Official Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
