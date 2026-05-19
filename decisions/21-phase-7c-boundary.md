# Phase 7c Boundary — gVisor Sandbox Substrate

> Status: locked at Phase 7c entry, 2026-05-19.
>
> Scope note: **third `ExecutionEnvironment` implementation**. Phase
> 7a established the substrate abstraction (Protocol + `HostExecution`
> identity transform). Phase 7b shipped `SandboxExecution` (aiodocker
> with `runc` default runtime). Phase 7c adds gVisor (`runsc`) as a
> selectable runtime for stronger user-space syscall interposition.
> Same `ExecutionEnvironment` Protocol;same `--sandbox` flag pathway;
> the new surface is `--sandbox-runtime [runc|runsc]` (and the
> equivalent Settings + env-var).
>
> Rationale: 7b's runc sandbox shares the host kernel. Modern threat
> models (untrusted code execution, multi-tenant evaluation services)
> increasingly demand user-space syscall filtering — gVisor (Google's
> open-source runtime) is the standard answer that DOESN'T require
> a VM (vs Firecracker, which does and adds ~125ms boot latency).
> gVisor swap is a one-flag change at the Docker layer.

## Triggering observation

Phase 7b's `SandboxExecution` already passes the runtime through
its aiodocker `HostConfig`:

```python
host_config = {
    "Binds": [f"{cwd}:{cwd}:rw"],
    "NetworkMode": network,
    "Memory": memory_bytes,
    "PidsLimit": pids,
    "NanoCpus": int(cpus * 1e9),
}
```

The Docker daemon's `--default-runtime` (or per-container `Runtime`)
field accepts any registered OCI runtime. Adding gVisor support is:

1. Install `runsc` on the host (out-of-band; the framework can't
   install a kernel-adjacent binary). Document this prereq.
2. Add `"Runtime": "runsc"` (or `"runc"`) to the `HostConfig`.
3. Surface as `--sandbox-runtime` flag + `Settings.sandbox_runtime`.

That's the entire diff. No new substrate class, no new aiodocker
calls, no engine changes. Same `SandboxExecution` constructor gains
one optional kwarg.

## Decisions

### D23.1 — gVisor runtime as a kwarg to `SandboxExecution`, not a new class

`SandboxExecution.__init__` gains `runtime: str = "runc"` kwarg.
Default keeps Phase 7b behavior byte-identical (`runc` is Docker's
default). When the user passes `runtime="runsc"`, the container's
`HostConfig.Runtime` is set accordingly.

**Why not a separate `GVisorExecution` class**: the substrate
behavior (Protocol contract, image pull, exec, cleanup, error
mapping) is IDENTICAL between runc and runsc. The container creation
RPC differs by one field. Subclassing would force code duplication
of every method to override one line — overkill.

**Why a kwarg, not a global config**: tests need to drive both
runtimes through the same instance API. Settings/CLI flag still
exists for user UX, but the underlying mechanism is a per-instance
field.

### D23.2 — Runtime not validated at `SandboxExecution` construction time

The framework accepts any string for `runtime` (`"runc"` / `"runsc"`
/ `"kata"` / `"sysbox"` / etc.). The Docker daemon validates and
rejects unknown runtimes when the container is created.

**Why no client-side allowlist**: users may install custom OCI
runtimes (kata, sysbox-runc, gvisor-systrap, future). Hardcoding
`Literal["runc", "runsc"]` would lock out future runtimes without
a framework release. Skip-not-fail at the framework layer; the
Docker error surfaces with a sensible message.

The CLI flag does NOT use Typer's `Choice` constraint either — same
reason. Free-form string with a doc hint that "runc" and "runsc"
are the documented options.

### D23.3 — Runtime mismatch error surfaces as standard sandbox setup failure

When the Docker daemon rejects an unknown runtime (or the runtime
isn't installed), aiodocker raises during container create. Phase
7b's `SandboxExecution.__aenter__` already catches this in the
"setup failure" path (`SandboxSetupError`). Phase 7c reuses it —
the user sees the underlying Docker message:

```
sandbox setup failed: container create: 400: Unknown runtime: runsc
```

No new error class. Same UX as "missing image" or "Docker daemon
not running."

### D23.4 — Settings field + CLI flag

- `Settings.sandbox_runtime: str = "runc"` (env:
  `OPENHARNESS_SANDBOX_RUNTIME`)
- `--sandbox-runtime <runtime>` Typer option (no Choice constraint per D23.2)
- Flag overrides env var; env var overrides default — same precedence
  chain as the existing 5 sandbox fields.

Both default to `"runc"` so Phase 7b's existing behavior is
preserved byte-identical for users not opting into gVisor.

### D23.5 — Integration test pattern: gVisor available → real container; not available → skip

Phase 7b ships `test_sandbox_integration.py` with 6 real-Docker
tests gated by `docker info` availability. Phase 7c adds 1 more:
test the runsc runtime with a benign command (`echo "hello from
gvisor"`) and assert exit_code == 0 + output matches.

The new test ALSO checks `docker info | grep -q "runsc"` — if
gVisor isn't installed, the test SKIPs (not fails). Same discipline
as the Docker daemon check.

This mirrors Phase 7b's pattern: framework code is testable with
mocks (unit tests stay green without Docker), real-environment
integration smokes gate on the environment being set up correctly.

---

## Cross-cutting invariant

Phase 7c is **purely additive within `execution/sandbox.py`**. Zero
diff vs Phase 5f close on:

- `permissions/`, `hooks/`, `engine/`, `observability/`, `mcp/`,
  `compaction/`, `skills/`, `commands/`, `protocols/`, `tools/`
- `bundles/`, `markdown_store/`
- `execution/base.py` (Protocol unchanged), `execution/host.py`
  (HostExecution unchanged)
- `prompts.py`

**Allowed diffs**:

- `execution/sandbox.py` — add `runtime` kwarg + plumb to
  `HostConfig`. ~5 LoC.
- `config/settings.py` — `sandbox_runtime: str = "runc"` field.
  ~10 LoC.
- `cli.py` — `--sandbox-runtime` flag + bootstrap wiring. ~15 LoC.
- `tests/execution/test_sandbox.py` — extend with runtime kwarg
  tests (mocked aiodocker).
- `tests/execution/test_sandbox_integration.py` — 1 new gated test
  for real runsc.
- `tests/cli/test_cli.py` — 2-3 tests for the new flag.

## Risks specifically NOT mitigated

- **Firecracker** (kernel VM isolation, ~125ms boot, ~5MB memory
  overhead per microVM) — different shape (no Docker daemon
  involvement); defer to Phase 7d if demand surfaces. Would need
  a new substrate implementation (not a runtime swap).
- **gVisor performance** — runsc has ~3x syscall overhead vs runc
  in microbenchmarks. Users tolerate this for the safety tradeoff;
  framework documents but doesn't mitigate.
- **gVisor compatibility gaps** — some syscalls runsc doesn't
  implement (e.g., specific ioctl variants). Test image
  (python:3.12-slim) works in 7c's smoke test; framework can't
  guarantee compatibility for arbitrary user images. Document.
- **Default runtime change to runsc** — staying default `runc` per
  D23.4 because (a) backward-compat with 7b deployments, (b) runsc
  not universally installed, (c) performance tradeoff is user's
  call.

---

## Pointers

- Phase 7a boundary (Protocol shape, identity transform): `decisions/15-phase-7-boundary.md`
- Phase 7b boundary (real Docker substrate this extends): `decisions/16-phase-7b-boundary.md`
- Phase 7b retro (the abstraction this builds on): `learnings/phase-7b.md`
- gVisor docs: https://gvisor.dev/docs/user_guide/install/ (out of band; not framework's job to install)
