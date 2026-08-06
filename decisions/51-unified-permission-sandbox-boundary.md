# Unified Permission + Sandbox Boundary

> Status: locked before implementation, 2026-08-05.
>
> Scope: replace the assumption that `SandboxExecution` is a session-wide
> security boundary with one canonical runtime permission profile compiled
> into a verified platform boundary. Permission resolution may rely only on
> the verified boundary, never on configuration intent alone.

## Trigger

`/goal` can take over turn continuation and completion checking, but permission
requests still require a person in the middle of the loop. The first attempted
async-permission design treated `AUTO + shell_contained` as enough to resolve a
mutating Bash `ASK` automatically.

Code review invalidated that premise:

- `SandboxExecution` is a Docker-backed implementation of
  `ExecutionEnvironment.run_command`; only Bash consumes it.
- Read, Write, Edit, Grep, MCP, Web tools, and hooks still execute through
  host-authority paths.
- the Docker backend mounts the entire workspace read-write and has only a
  binary `none` / `bridge` network choice;
- the runtime has no verified description of which model-controlled effects
  are actually covered;
- raw command failures cannot reliably distinguish ordinary errors from
  filesystem, network, or sandbox-policy violations.

Therefore the current Docker backend can remain useful, but it cannot be the
trusted foundation for async permission by itself.

## Security claim

For every model-controlled effect, exactly one of these outcomes must hold:

1. the active runtime boundary is verified to enforce the effect;
2. one exact permission delta is approved for one retry;
3. the effect is denied or parked for later human handling.

There must be no unclassified fourth path that executes with the harness
process's ambient authority.

Formally:

```text
for every effect in ModelControlledEffects:
    Enforced(effect, EnforcedBoundary)
    OR ApprovedOnce(effect, exact_delta)
    OR DeniedOrParked(effect)
```

The protected assets are:

- host files outside declared roots;
- workspace protected paths such as `.git`, `.codex`, and `.agents`;
- project and user credentials, including deny-read files inside a workspace;
- outbound data and local/private services;
- persistent host configuration and cross-project state;
- host process, memory, CPU, PID, socket, and device capabilities.

The trusted control plane is narrower: harness code, explicitly installed
hooks/plugins, LLM API transport, snapshots, Goal judge calls, and fixed-path
internal bookkeeping. Model-authored arguments and model-selected tool calls
are data plane, even when a trusted harness component dispatches them.

## One source of truth

User-facing configuration has one canonical source:

```text
RuntimePermissionProfile
├── FilesystemPolicy
├── NetworkPolicy
├── EnvironmentPolicy
├── ProcessPolicy
└── ExternalToolPolicy
```

`PermissionPolicy` and `SandboxPolicy` are not independent user configurations.
The backend compiler lowers the canonical profile into platform-specific
enforcement and returns a verified fact:

```text
RuntimePermissionProfile
        │ compile + preflight
        ▼
EnforcedBoundary
├── profile_fingerprint
├── backend + backend_version
├── covered_effects
├── installed filesystem rules
├── installed network rules
├── installed environment/process rules
├── unsupported features
└── verification result
```

Permission resolution consumes `EnforcedBoundary`; it must not infer safety
from a configured profile, a backend class name, or booleans such as
`shell_contained`.

## Policy dimensions

### Filesystem

- read, write, and deny access;
- multiple workspace and writable roots;
- narrower protected/denied paths under broader writable roots;
- temp and cache roots;
- deny-read secret patterns;
- normalized path handling and explicit symlink/non-existent-path behavior.

### Network

- disabled by default;
- exact public-domain allow rules with deny precedence;
- local, private, link-local, and loopback destinations denied by default;
- Unix sockets denied unless explicitly allowed;
- proxy-mediated enforcement when hostname policy cannot be enforced directly.

### Environment and process

- minimal environment inheritance with include/exclude/set rules;
- credential-shaped variables excluded by default;
- non-login shell by default;
- UID/GID, capability, no-new-privileges, resource, timeout, and process-tree
  cleanup policy;
- child processes inherit the same boundary.

### External tools

Local filesystem sandbox claims do not extend to MCP, Web, connectors, browser,
or Computer Use. Those surfaces require independent effect declarations and
approval policy. A local stdio MCP server may itself be started in a sandbox,
but its remote side effects remain an external-tool concern.

## Backend contract

```python
class SandboxBackend(Protocol):
    def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport: ...
    async def open(self, profile: RuntimePermissionProfile) -> SandboxSession: ...

class SandboxSession(Protocol):
    @property
    def boundary(self) -> EnforcedBoundary: ...
    async def execute(self, operation: DataPlaneOperation) -> ExecutionResult: ...
```

`open()` either installs the requested policy and returns a verified boundary,
or fails closed. No backend may silently fall back to `HostExecution` when the
requested profile cannot be enforced.

`ExecutionResult` distinguishes at least:

```text
ProcessCompleted
BoundaryViolation       # only when the backend can identify it reliably
SandboxUnavailable
TimedOut
ExecutionFailed         # ordinary or unclassified tool failure
```

Raw stderr parsing is not an authorization oracle. Escalation has two valid
sources:

- a proactive `PermissionDeltaRequest` naming the required resource;
- a backend-generated `BoundaryViolation` with deterministic evidence.

## Tool coverage contract

Every registered tool declares one execution domain. Missing declarations fail
closed or require approval.

| Surface | Current path | Target domain |
|---|---|---|
| Bash | host or Docker command backend | local sandbox data plane |
| Grep | raw host `rg` subprocess | local sandbox data plane |
| Read | host `Path` calls | local sandbox filesystem worker |
| Write/Edit | host `Path` calls | local sandbox filesystem worker |
| SpawnAgent | inherits partial query context | inherits the same verified runtime |
| stdio MCP process | host process, parent environment | configurable sandboxed service process |
| MCP call | remote/server-defined effect | external-tool policy |
| WebSearch/WebFetch | host/provider network | external network-tool policy |
| hooks/plugins | host Python | explicitly trusted control plane |
| LLM API/snapshot/Goal judge | host harness | fixed-purpose control plane |

No async permission posture may ship until Bash, Grep, Read, Write, and Edit
are all covered by the same verified local boundary.

## Backend choices

### Docker command backend

The existing `SandboxExecution` is retained and repositioned as a command
backend. Its initial coverage is exactly `{command}`. It must not advertise
session-wide coverage.

Before being considered a trustworthy command backend it needs preflight,
fail-closed startup, protected paths, read-only rootfs where compatible,
non-root execution, capability drop, no-new-privileges, explicit temp storage,
complete process-tree cleanup, and reproducible image selection. `network=none`,
resource limits, and optional gVisor remain useful.

Docker is an explicit CI/dev-container/strong-isolation backend, not a silent
fallback for native local execution.

### macOS Seatbelt backend

macOS is the first native backend because it is the primary development
platform. It uses the host toolchain while enforcing filesystem and network
rules for the process and its descendants. Unsupported policies or profile
load failures fail closed. A sandbox doctor and negative integration suite are
part of the backend contract.

Linux bubblewrap + seccomp follows as a separate backend phase. Platform parity
is not faked: unsupported dimensions are reported in preflight.

## Local data-plane design

Model-controlled filesystem operations do not run directly in the ambient
harness process. Read, Write, Edit, and Grep are routed through a small worker
launched under the active native sandbox; Bash uses the same sandbox session.

```text
Read  ┐
Write │
Edit  ├── sandbox worker / command runner ── verified OS boundary
Grep  │
Bash  ┘
```

This avoids duplicating a path classifier in every tool and prevents Bash from
bypassing file-tool restrictions or file tools from bypassing Bash isolation.
Authorization is re-evaluated after any PreToolUse argument modification.

## Permission and async lifecycle

Only after the local boundary coverage gate is met does `ASK` become an async
boundary workflow:

```text
profile → compile + verify → boundary-contained execution
                            ↓ boundary delta required
PermissionDeltaRequest / deterministic BoundaryViolation
                            ↓
boundary-only auto-review
                            ↓
exact one-retry overlay
                            ↓ unresolved
typed park → Goal pauses without judge call or auto-turn consumption
```

Auto-review is a reviewer swap for an exact boundary request. It does not
persistently change the base profile. Reviewer input includes user authority,
the final validated tool arguments and fingerprint, active profile and backend
fingerprints, the smallest requested delta, data source/destination, and
whether the backend can enforce a one-shot overlay.

Denials instruct the worker not to pursue equivalent workarounds. Repeated
denials have a circuit breaker. Human approval is exact and retry-bounded.

## Session, Goal, and persistence

- the runtime profile belongs to the session, not Goal;
- Goal continues turns inside the session's verified runtime;
- snapshot state records the active profile id, effective fingerprint,
  backend identity, and parked request;
- resume refuses or surfaces a warning when the effective boundary differs;
- permission park occurs before Goal judging and consumes no auto-turn.

## Implementation phases and gates

### S0 — Remove the invalid premise

Keep final-argument reauthorization and context-cwd path normalization. Remove
or quarantine the experimental four-field boundary, `SHELL` auto-allow,
reviewer, typed events, grants, and `/approve` implementation until their data
contracts are rebuilt on the canonical profile.

### S1 — Canonical profile + coverage model

Land policy models, backend protocols, effective-boundary fingerprints,
execution-domain declarations, coverage inspection, and `/permissions` status.
No tool behavior changes and no automatic escalation.

### S2 — Honest and hardened Docker command backend

Reposition the existing implementation, add preflight/fail-closed behavior,
harden the container, and publish exact coverage.

### S3 — macOS Seatbelt backend

Compile filesystem/network/process rules for host-toolchain commands, add
doctor output, and prove negative isolation properties with integration tests.

### S4 — Unified local data plane

Move Read/Write/Edit/Grep to the sandbox worker, make Bash use the same runtime,
and prove there is no host-authority bypass. This is the gate for async
permission work.

### S5 — Network, environment, and process policy

Add domain proxying, private/local/socket controls, environment filtering,
login-shell policy, process-tree lifecycle, and typed deterministic violations.

### S6 — External tools

Model MCP, Web, and trusted hooks explicitly without overstating the local
sandbox boundary.

### S7 — Async permission

Rebuild exact requests, boundary-only auto-review, one-retry overlays,
denial circuit breakers, typed park/resume, snapshot persistence, and Goal
integration.

## Acceptance invariant

The project may expose an async/Auto permission posture only when all of the
following are true:

- every model-controlled local effect has an execution-domain declaration;
- all core local data-plane tools share one verified boundary;
- backend unsupported-policy paths fail closed;
- protected paths, deny-read secrets, network denial, child-process
  inheritance, environment filtering, symlink paths, and timeout cleanup have
  negative integration tests;
- `/permissions` reports the active profile, backend, installed boundary,
  coverage, uncovered external surfaces, and policy fingerprint;
- reviewer approval is exact, minimal, retry-bounded, and cannot override hard
  deny rules;
- a parked permission pauses Goal before judge invocation.

## References

- [Codex Permissions](https://learn.chatgpt.com/docs/permissions)
- [Codex Sandbox](https://learn.chatgpt.com/docs/sandboxing)
- [Codex Agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [Codex Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)
- [Codex core sandbox support matrix](https://github.com/openai/codex/blob/main/codex-rs/core/README.md)
- [Codex Linux sandbox](https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md)
