# Phase 7b Implementation Plan — `SandboxExecution` (Docker substrate)

> Phase 7a archive: [`tasks/phase-7-plan.md`](./phase-7-plan.md).
> Boundary contract: [`decisions/16-phase-7b-boundary.md`](../decisions/16-phase-7b-boundary.md).
> Preview source: [`tasks/phase-7-preview.md`](./phase-7-preview.md).

## Overview

**Phase 7b goal**: make `oh ask --sandbox "..."` actually isolate Bash
executions inside a Docker container. The abstraction (`ExecutionEnvironment`
Protocol + `BashTool` delegation) was landed in Phase 7a; Phase 7b is
**pure plug-in**: add a second substrate implementation, wire it via
Settings + CLI.

**Total scope**: ~1-2 days, 4 capabilities, ~8-12 commits, ~250 lines
of production code.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/16-phase-7b-boundary.md`](../decisions/16-phase-7b-boundary.md) | D18.1 `aiodocker` async SDK; D18.2 per-query lifecycle (async context manager); D18.3 host path → `/workspace` substrate translation; D18.4 cwd RW bind mount only; D18.5 `--network none` default; D18.6 cgroup defaults (1GB / 1 CPU / 256 pid); D18.7 stock `python:3.12-slim` image (Settings overridable); D18.8 unit (mocked) + integration (gated) test split |

## Task list

### P7b-T1: `SandboxExecution` class + aiodocker dependency ✅

**Description**: Foundation — the substrate class itself, with all
unit-test coverage. No Settings / CLI wire-up yet; T2 handles that.
Container lifecycle (`__aenter__` pulls image + spawns container,
`__aexit__` stops + removes), `run_command` execs into the container
+ translates output to `ProcessResult`.

**Acceptance**:
- [ ] `pyproject.toml` — `aiodocker>=0.21,<1.0` added to project deps
- [ ] `execution/sandbox.py` — `SandboxExecution` class implementing
  `ExecutionEnvironment` Protocol (per D17.1):
  - `__init__(cwd, image, network, memory, cpus, pids)` — captures
    config; doesn't touch Docker yet (deferred to `__aenter__`)
  - `async __aenter__()` — connects to Docker daemon via `aiodocker.Docker()`;
    pulls image if not present; spawns container with cwd bind mount
    + cgroup limits + network mode; stores `self._container`
  - `async __aexit__(...)` — stops + removes container; closes daemon
    connection
  - `async run_command(command, cwd, timeout)` — execs `["sh", "-c",
    command]` in the running container (cwd translated to `/workspace`
    per D18.3); reads stdout/stderr merged; returns `ProcessResult`
  - Timeout: `aiodocker` exec doesn't natively timeout; wrap in
    `asyncio.wait_for`; on timeout, kill the exec'd process via
    `container.exec(["kill", "-TERM", str(pid)])` then SIGKILL after
    grace period (mirrors HostExecution semantics)
- [ ] `execution/__init__.py` — export `SandboxExecution`
- [ ] Unit tests (`tests/execution/test_sandbox.py`, mocked `aiodocker`):
  - `__aenter__` pulls image if not present (mock `images.pull`)
  - `__aenter__` skips pull if image present (mock `images.inspect`
    succeeds)
  - `__aenter__` spawns container with correct HostConfig (Binds /
    NetworkMode / Memory / CpuQuota / PidsLimit)
  - `__aexit__` stops + removes container
  - `run_command` execs `["sh", "-c", command]` with WorkingDir
    `/workspace`
  - `run_command` translates exec output (stdout + exit_code) into
    `ProcessResult`
  - `run_command` timeout fires → SIGTERM then SIGKILL → returns
    `ProcessResult(timed_out=True, exit_code=-1)`
  - `run_command` non-zero exit propagates
  - Docker daemon connection failure → raises clear error in `__aenter__`
- [ ] mypy strict + ruff clean (with `types-aiodocker` if available;
  else `[tool.mypy.overrides]` for the package)

**Files**:
- `pyproject.toml` (+1 dep, possibly +1 dev dep)
- `src/openharness/execution/__init__.py` (+1 export)
- `src/openharness/execution/sandbox.py` (new)
- `tests/execution/test_sandbox.py` (new)

**Sub-units**:
- 1a — aiodocker dep + `SandboxExecution` skeleton + `__aenter__` mock tests
- 1b — `run_command` + output translation + mock tests
- 1c — Timeout SIGTERM/SIGKILL escalation + mock tests
- 1d — `__aexit__` cleanup + error path coverage

---

### P7b-T2: Settings + CLI flags + bootstrap 🔜 NEXT

**Description**: Wire `SandboxExecution` into the harness via Settings
+ CLI. `--sandbox` is off by default (zero behavior change); enabling
swaps in `SandboxExecution` for the `QueryContext.execution_env`.

**Acceptance**:
- [ ] `Settings` fields:
  - `sandbox_enabled: bool = False` (env `OPENHARNESS_SANDBOX`)
  - `sandbox_image: str = "python:3.12-slim"` (env `OPENHARNESS_SANDBOX_IMAGE`)
  - `sandbox_network: Literal["none", "bridge"] = "none"` (env
    `OPENHARNESS_SANDBOX_NETWORK`)
  - `sandbox_memory: str = "1g"` (env `OPENHARNESS_SANDBOX_MEMORY`)
  - `sandbox_cpus: float = 1.0` (env `OPENHARNESS_SANDBOX_CPUS`)
  - `sandbox_pids: int = 256` (env `OPENHARNESS_SANDBOX_PIDS`)
- [ ] CLI flags (Typer):
  - `--sandbox / --no-sandbox` (default off; aligns with Settings default)
  - `--sandbox-network [none|bridge]`
  - `--sandbox-memory <str>`
  - `--sandbox-cpus <float>`
  - `--sandbox-image <str>`
- [ ] `cli._run_ask` — if `sandbox_enabled`:
  - Enter `async with SandboxExecution(...) as env:` context
  - Build `QueryContext` with `execution_env=env`
  - Run query inside the context
  - Otherwise (default) — use existing host execution flow unchanged
- [ ] CLI tests (`tests/cli/test_cli.py::TestSandboxFlags`, mocked
  `aiodocker`):
  - `--sandbox` enables sandbox path (verify
    `QueryContext.execution_env` is a `SandboxExecution` instance)
  - `--no-sandbox` keeps host execution (default — backward compat
    smoke for existing CLI tests)
  - `--sandbox-network bridge` propagates to `SandboxExecution`
  - `--sandbox-memory 512m` propagates
  - Env vars work same as flags
  - `--sandbox` without docker daemon → clear error UX
- [ ] All 1023 existing tests continue to pass unchanged
  (`--sandbox` defaults to off, so no behavior change)

**Files**:
- `src/openharness/config/settings.py` (+5 fields)
- `src/openharness/cli.py` (+5 flags + bootstrap chain)
- `tests/cli/test_cli.py` (+`TestSandboxFlags`)
- `tests/config/test_settings.py` (+`TestSandboxFields`)

**Sub-units**:
- 2a — Settings fields + parsing tests
- 2b — CLI flags + propagation tests
- 2c — `_run_ask` substrate swap + tests

---

### P7b-T3: Integration smoke (real Docker, gated)

**Description**: Real-Docker tests behind `@pytest.mark.integration` +
`docker info` available check. Same gating pattern as Phase 1 / Phase 5
integration tests. Verifies the actual sandbox properties.

**Acceptance**:
- [ ] `tests/execution/test_sandbox_integration.py`:
  - `@pytest.fixture` checks `docker info` and `pytest.skip` if Docker
    not available (cross-platform safe)
  - Test: `Bash("echo hello")` in sandbox returns stdout
  - Test: `Bash("ls /workspace")` lists host cwd contents (bind mount
    works)
  - Test: `Bash("cat /etc/passwd")` FAILS — file doesn't exist in
    container's mount namespace (since /etc isn't bind-mounted)
  - Test: `Bash("curl -s -m 2 https://example.com")` FAILS — network
    none means no egress
  - Test: `Bash(":(){ :|:& };:")` (fork bomb) bounded by PidsLimit (no
    host harm)
  - Test: `--sandbox-network=bridge` allows external request
- [ ] All tests skipped cleanly on machines without Docker (`pytest -v`
  shows SKIPPED, no errors)

**Files**:
- `tests/execution/test_sandbox_integration.py` (new)

**Sub-units**:
- 3a — Docker-available fixture + happy-path tests
- 3b — Negative tests (no /etc, no network, fork bomb bounded)
- 3c — `--network bridge` opt-in test

---

### P7b-T4: README + retro + DoD closeout

**Description**: Docs + retrospective.

**Acceptance**:
- [ ] README "Phase 7b — Docker sandbox" section: how to enable, what
  it isolates, when to opt out, performance notes (macOS Docker
  Desktop vs Linux native)
- [ ] `learnings/phase-7b.md` retro focusing on:
  - "Pure plug-in" hypothesis (7a retro §3.6) empirically validated
  - aiodocker integration learnings
  - Cross-platform reality (macOS LinuxKit VM impact on test reliability)
  - Forward pointer: gVisor / Firecracker would be even cleaner
    plug-ins (already have the Protocol)
- [ ] `tests/execution/test_invariant.py` — verify P7b doesn't
  introduce new identifier leaks (same structural test, expanded
  forbidden set)
- [ ] Phase 7b DoD checklist all green in plan

**Files**:
- `README.md` (+section)
- `learnings/phase-7b.md` (new)
- `tests/execution/test_invariant.py` (extend forbidden set to include
  `SandboxExecution`)
- `tasks/phase-7b-plan.md` (DoD closeout block)

**Sub-units**:
- 4a — README + invariant test extension
- 4b — Retro
- 4c — DoD closeout

---

## Checkpoints

After each capability: **human review** of code ↔ acceptance walkthrough
per CLAUDE.md GREEN→review→commit pattern.

### After P7b-T1
- **Human review**: aiodocker abstraction shape — does `SandboxExecution`
  hide all aiodocker types from the outside, or do they leak via
  exceptions?

### After P7b-T2
- **Human review**: `--sandbox` UX feel — is the flag set + the 5
  cluster of `--sandbox-*` flags ergonomic? Should they be a single
  `--sandbox-config` JSON?

### After P7b-T3 (integration tests passing locally)
- **Real `oh ask` smoke** on a real prompt: `OPENHARNESS_SANDBOX=true
  oh ask "use bash to count files in this dir"` — does sandbox feel
  fast enough?

### After P7b-T4 (Phase 7b complete)
- **Decision point**: Phase 5d ModeBundle next, or Phase 8 Polish?
  Or pick up gVisor as Phase 7c (probably overkill)?

---

## Risks

| Risk | Mitigation |
|---|---|
| `aiodocker` API changes (it's pre-1.0) | Pin to `>=0.21,<1.0`. If breakage surfaces, switch to `docker` SDK + `asyncio.to_thread` is a < 1-day refactor (substrate is small) |
| Docker daemon not running → spawn fails mid-`oh ask` | T2 acceptance: `--sandbox` without daemon → clear error UX with hint ("is Docker running?"); fail BEFORE entering run_query so user sees the message |
| macOS Docker Desktop spawn latency (~5-10s warm-up) | First `oh ask --sandbox "..."` on macOS will feel slow. Retro flags this; users can `docker run -d busybox sleep infinity` to keep Docker warm. Not a code fix, a doc fix. |
| Container image pull on first use → unexpected delay | `__aenter__` calls `images.inspect` first; if not present, `images.pull(progress=...)` with a stderr log line so user sees what's happening |
| Bind mount perf on macOS (nested VM filesystem layer) | Doc note in retro + README. Linux CI is the true reference for perf |
| Image overrides (`OPENHARNESS_SANDBOX_IMAGE=...`) might not have `sh` | Document that the image needs POSIX `sh`. Most images do; pure-binary images (FROM scratch) won't work |

## Risks specifically NOT mitigated (Phase 8+)

- No Docker → no fallback to HostExecution silently. User explicitly
  enabled `--sandbox`, they get an error if Docker isn't available.
  Silent fallback would defeat the security intent.
- No GUI Docker auth flows (proxy auth / registry credentials).
  Documented as "use `docker login` first" in the README.
- No multi-container per query (e.g., one container per Bash call
  with finer isolation). Per-query is the unit; defer otherwise.

---

## Pointers

- Boundary: [`decisions/16-phase-7b-boundary.md`](../decisions/16-phase-7b-boundary.md)
- Phase 7a abstraction: [`decisions/15-phase-7-boundary.md`](../decisions/15-phase-7-boundary.md)
- Phase 7a retro (where "Phase 7b is plug-in" insight was named): [`learnings/phase-7a.md`](../learnings/phase-7a.md) §3.6
- aiodocker docs: <https://aiodocker.readthedocs.io/>
