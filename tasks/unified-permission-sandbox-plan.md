# Unified Permission + Sandbox Plan

> Written before implementation on 2026-08-05. The boundary and invariants are
> fixed in `decisions/51-unified-permission-sandbox-boundary.md`; this file
> sequences independently verifiable capabilities.

## Outcome

OpenHarness has one session-level permission profile compiled into a verified
runtime boundary. All model-controlled local effects either execute inside
that boundary, receive one exact approved overlay, or park. Goal remains a
consumer of the session runtime and never owns permissions.

## Implementation status — 2026-08-05

- S0 complete: the premature permission resolver/reviewer/park experiment was
  removed; final-argument reauthorization and context-cwd normalization remain.
- S1 complete: canonical profile, deterministic fingerprint, verified-boundary
  contracts, explicit tool execution domains, coverage report, and honest
  `/permissions` status are implemented.
- S2 remains a command-only compatibility backend. It has a read-only rootfs,
  dropped capabilities, no-new-privileges, non-root UID/GID support, tmpfs,
  protected workspace mounts, explicit unsupported-feature reporting, and no
  host fallback. Image digest pinning, a separate daemon/runtime doctor, and a
  real-runc process-tree acceptance run remain before S2 is complete; it is not
  presented as a session-wide boundary.
- S3 complete for the macOS production posture: the Seatbelt compiler performs
  a real startup probe, reports a verified boundary, defaults to no network,
  and is selected by the production CLI when sandboxing is enabled. Profile
  installation failure aborts the sandbox posture instead of falling back to
  host execution.
- S4 complete: Bash, Read, Write, Edit, and Grep share one verified
  `SandboxSession`; file operations use the sandbox worker, Grep and Bash use
  the same command boundary, and SpawnAgent inherits the same runtime. The
  production CLI retains both the active profile and verified boundary, while
  `/permissions` reports configured intent separately from installed facts.
- S5 complete: enabled network access is mediated by a loopback proxy with
  public-domain allow/deny evaluation; private, loopback, link-local, and
  undeclared Unix-socket access fail closed. The sandbox receives a minimal,
  credential-filtered environment, non-login shells, bounded output and
  timeouts, and whole-process-group termination. Only proxy-recorded policy
  denials become typed violations; DNS, upstream, and ordinary command failures
  remain ordinary failures.
- S6 complete: MCP, Web, Browser, and Computer Use are independent external
  policy surfaces and never inherit local-sandbox trust. MCP adapters are
  classified as external effects, optional stdio MCP sandboxing fails closed,
  and hook-modified final arguments are reauthorized before the external policy
  path runs. `/permissions` names registered external surfaces as outside the
  local sandbox.
- S7 complete: exact final-argument/profile/boundary fingerprints drive
  permission requests; a tool-disabled reviewer can grant one exact overlay,
  while hard denies, reviewer failures, unsupported overlays, and repeated
  violations fail closed or park. Typed park/approve/deny/resume state is
  durable in snapshots, resume rejects boundary drift, multi-tool parked turns
  persist well-formed tool-result pairs, and Goal pauses before its judge
  without consuming an automatic turn.
- Verification for S3-S7 on 2026-08-05: `2732 passed, 11 deselected`, total
  coverage `95.04%`, strict mypy clean, Ruff clean, and format check clean.

## S0 — Correctness floor and experiment cleanup

Capabilities:

- final validated arguments are the arguments authorized immediately before
  execution, including after PreToolUse modification;
- all permission paths resolve relative to `ToolExecutionContext.cwd`;
- the incomplete resolver/boundary/reviewer/park experiment is removed without
  removing those two correctness fixes;
- the existing git commit/push human handoff red line remains unchanged.

Acceptance:

- focused regression tests are green;
- no `AUTO` path claims that Docker covers non-Bash tools;
- production behavior otherwise matches HEAD before the experiment.

## S1 — Canonical profile and boundary contracts

Capabilities:

- typed filesystem, network, environment, process, and external-tool policy;
- deterministic profile normalization and fingerprinting;
- `SandboxBackend` preflight/open contract and `EnforcedBoundary` result;
- explicit tool execution domains and a registry coverage report;
- `/permissions` displays configured intent separately from installed facts.

Acceptance:

- semantically identical profiles have identical fingerprints;
- contradictory or unrepresentable rules fail validation;
- registering an undeclared model-callable tool fails closed;
- no backend or tool execution behavior changes in this phase.

## S2 — Docker command backend

Capabilities:

- existing Docker implementation is exposed as a command-only backend;
- preflight reports daemon/runtime/image/policy support;
- container hardening and protected workspace paths;
- deterministic cleanup and explicit unavailable/unsupported results;
- exact coverage and limitations visible through `/permissions` and doctor.

Acceptance:

- no Docker/runtime support produces no host fallback;
- commands cannot write protected paths or see host paths outside declared
  mounts;
- default network denial and resource limits remain effective;
- timeout terminates the whole command process tree;
- integration tests exercise runc; gVisor remains separately gated.

## S3 — macOS Seatbelt backend

Capabilities:

- compile canonical filesystem rules into a Seatbelt profile;
- execute the host toolchain with child-process inheritance;
- protect nested paths under writable roots;
- default network denial and backend diagnostics;
- `sandbox doctor` checks availability and a minimal isolation probe.

Acceptance:

- workspace writes succeed while outside writes and protected writes fail;
- deny-read files cannot be read by direct or child processes;
- network denial is enforced;
- unsupported rules or missing `/usr/bin/sandbox-exec` fail closed;
- no command silently runs through HostExecution.

## S4 — Unified local data plane

Capabilities:

- sandbox worker operations for Read, Write, and Edit;
- Grep runs through the sandbox command runner;
- Bash and filesystem worker share one active `SandboxSession`;
- SpawnAgent inherits the same verified runtime;
- structured execution results reach tool and controller layers.

Acceptance:

- core local tools have no ambient-host execution path in sandboxed posture;
- Bash and file tools observe identical filesystem denies;
- symlink, parent traversal, non-existent path, and replacement-race tests fail
  safely;
- the coverage matrix proves all core local effects are covered.

## S5 — Network, environment, and process boundary

Capabilities:

- proxy-mediated public-domain allow/deny policy;
- local/private/link-local and Unix-socket protection;
- minimal environment construction with credential exclusions;
- non-login shell and bounded process lifecycle;
- deterministic boundary violations where the backend can prove the cause.

Acceptance:

- allowed public domains work and non-allowed domains fail;
- local/private targets and unlisted sockets fail;
- credential variables do not reach model-controlled processes by default;
- children cannot escape the parent boundary;
- ordinary network/tool failures are not mislabeled as permission violations.

## S6 — External effect policy

Capabilities:

- stdio MCP process sandbox option and environment policy;
- MCP/app side-effect classification independent from local sandbox coverage;
- WebSearch/WebFetch network policy;
- explicit trusted-control-plane declaration for hooks/plugins;
- status output names every external surface not covered by local sandbox.

Acceptance:

- local sandbox status never implies remote MCP/Web safety;
- untrusted or mutating MCP calls enter the external approval path;
- trusted hooks remain able to enforce policy, and modified arguments are
  reauthorized afterward.

## S7 — Async permission lifecycle

Capabilities:

- exact `PermissionDeltaRequest` and deterministic `BoundaryViolation` events;
- boundary-only reviewer with minimal one-retry overlays;
- hard-deny preservation and denial circuit breaker;
- durable typed park/approve/deny/resume;
- Goal pauses before judge and consumes no auto-turn while parked;
- snapshots persist profile/backend/request fingerprints and detect resume
  drift.

Acceptance:

- contained actions make zero reviewer calls;
- reviewer input identifies the exact final arguments and requested delta;
- changing arguments invalidates a one-shot approval;
- reviewer failure/defer parks rather than broadening access;
- resume under a different effective boundary warns or refuses;
- full non-integration test, strict mypy, Ruff check, and format check pass.
