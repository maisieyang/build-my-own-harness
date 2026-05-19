# Phase 7c Implementation Plan — gVisor Sandbox Runtime

> Phase 7c boundary: [`decisions/21-phase-7c-boundary.md`](../decisions/21-phase-7c-boundary.md).
> Builds on Phase 7b: [`decisions/16-phase-7b-boundary.md`](../decisions/16-phase-7b-boundary.md).

## Overview

Add `runtime` kwarg to `SandboxExecution` + Settings field +
CLI flag. Default `"runc"` preserves Phase 7b behavior; `"runsc"`
selects gVisor for user-space syscall isolation. Same Protocol,
same flag pathway, ~30 LoC production.

**Total scope**: ~4 hours, 3 capabilities, ~3 commits.

## Task list

### P7c-T1: `SandboxExecution.runtime` kwarg ✅

**Description**: Plumb runtime into container `HostConfig`. Mocked
unit tests cover runc and runsc kwarg values.

**Acceptance**:
- [ ] `execution/sandbox.py`:
  - `SandboxExecution.__init__` gains `runtime: str = "runc"` kwarg
  - `HostConfig` dict gains `"Runtime": self._runtime` field
  - No client-side validation (D23.2)
- [ ] Tests `tests/execution/test_sandbox.py`:
  - Default kwarg ("runc") is reflected in mocked HostConfig
  - Explicit "runsc" passes through to mocked HostConfig
  - Arbitrary string (e.g. "kata") passes through without rejection
  - Existing 7b tests pass unchanged

---

### P7c-T2: Settings field + CLI flag + bootstrap ✅

**Acceptance**:
- [ ] `config/settings.py`: `sandbox_runtime: str = "runc"` field
- [ ] `cli.py`: `--sandbox-runtime` Typer option + bootstrap wiring
- [ ] Tests:
  - `test_settings.py::TestSandboxFields` extended: default + env override
  - `test_cli.py`: flag override propagates to QueryContext.execution_env

---

### P7c-T3: Real-gVisor smoke + README + retro ✅

**Acceptance**:
- [ ] `tests/execution/test_sandbox_integration.py`: 1 new test
  gated on `docker info | grep runsc`. If runtime not installed,
  SKIP. Otherwise: SandboxExecution(runtime="runsc"), run
  `echo hello`, assert output == "hello\n" + exit_code == 0.
- [ ] README "Phase 7c — gVisor runtime" section
- [ ] `learnings/phase-7c.md` retro
- [ ] DoD checklist all green
