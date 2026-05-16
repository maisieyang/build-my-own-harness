# Phase 5b Implementation Plan — Slash Commands

> Phase 5a (MCP) plan: [`tasks/phase-5-plan.md`](./phase-5-plan.md).
> Phase 5c (Skills) plan: [`tasks/phase-5c-skills-plan.md`](./phase-5c-skills-plan.md).
>
> Boundary contract: [`decisions/14-phase-5b-boundary.md`](../decisions/14-phase-5b-boundary.md).
> Framing basis: [`tasks/phase-5-preview.md`](./phase-5-preview.md) D15 +
> Phase 5c retro `learnings/phase-5c-skills.md` §3.2 (pure templating).

## Overview

**Phase 5b goal**: `oh ask "/review last commit"` → CLI looks up
`review.md` in `~/.openharness/commands/` or `<cwd>/.openharness/commands/`,
substitutes `{args}` → `last commit`, the resolved body becomes the user
message. From `run_query`'s perspective, no slash command exists. **Fourth
test** of the cross-cutting invariant — even cleaner than 5a/5c because
commands never reach the LLM-facing infrastructure.

**Total scope**: ~2-3 days, 5 capabilities, ~10-15 commits, ~150 lines of
production code.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/14-phase-5b-boundary.md`](../decisions/14-phase-5b-boundary.md) | C1 pure template only (no ModeBundle); C2 global+project storage with project overrides; C3 `{args}` placeholder + tail append fallback; C4 all one-shot (no stateful mode); C5 NO hook integration (defer to Phase 5d) |

## Task list

### P5b-T1: `commands/` package foundation ✅

**Description**: Mirror `skills/` (Phase 5c P5c-T1) structurally. Pure
data + parsing layer. `Command` dataclass + `parse_command` + filesystem
discovery. No CLI wire-up yet — that's T3.

**Acceptance**:
- [ ] `commands/model.py` — `Command` frozen dataclass (`name: str`,
  `description: str`, `body: str`, `source_path: Path`)
- [ ] `commands/model.py` — `parse_command(path) -> Command | None`:
  reads file, splits YAML frontmatter, validates required fields
  (`name` + `description`); returns `None` + warning log on malformed
  (NEVER raises — same never-raise discipline as Skills)
- [ ] `commands/store.py` — `CommandStore` Protocol + `FilesystemCommandStore`
  (global+project, project overrides) + `EmptyCommandStore` sentinel
- [ ] `name` regex `^[A-Za-z][A-Za-z0-9_-]*$` (same shape as
  `McpServerConfig.name` and `Skill.name`)
- [ ] Tests: dataclass shape / parse happy / parse 7+ error paths /
  store discovery (global only / project only / both / project
  overrides global / collision / malformed skipped)

**Files**:
- `src/openharness/commands/__init__.py` (new)
- `src/openharness/commands/model.py` (new)
- `src/openharness/commands/store.py` (new)
- `tests/commands/__init__.py`, `tests/commands/test_model.py`,
  `tests/commands/test_store.py` (new)

**Sub-units**:
- 1a — `Command` dataclass + happy-path parse + tests
- 1b — `parse_command` error paths + warning logs + tests
- 1c — `FilesystemCommandStore` + Protocol + Sentinel + tests

**Refactor opportunity**: significant overlap with `skills/model.py` and
`skills/store.py` — same YAML frontmatter parser, same two-layer storage.
**Phase 5b explicitly does NOT refactor** (would inflate scope); a
shared `markdown_store` helper can land later as a Phase 7 polish task
once Slash Command + Skills patterns prove they share the abstraction.

---

### P5b-T2: `expand_command` — prompt resolution ✅

**Description**: The single function that turns `/cmd args` into a
resolved user message. Pure function — takes a raw prompt + a
`CommandStore`, returns either the expanded body (slash prefix found
and resolved) or the original prompt verbatim (no slash prefix).

**Acceptance**:
- [ ] `commands/expand.py` — `expand_command(prompt: str, store: CommandStore) -> str`:
  - No leading `/` → return prompt unchanged
  - `/cmd args` → look up `cmd` in store; substitute `{args}`; return body
  - `/cmd` (no args) → substitute `{args}` with empty string
  - Body without `{args}` placeholder + non-empty args → append args on
    a new line at end of body
  - Unknown command name → raise `UnknownCommandError` (caught by `cli.py`
    for user-facing error UX)
- [ ] `commands/errors.py` — `UnknownCommandError(OpenHarnessError)`
  subclass with `name` + `available: list[str]` attributes so `cli.py`
  can format the error message with the catalog
- [ ] Tests: pass-through (no slash) / arg substitution / empty args /
  tail append fallback / unknown command raises

**Files**:
- `src/openharness/commands/expand.py` (new)
- `src/openharness/commands/errors.py` (new)
- `tests/commands/test_expand.py` (new)

**Sub-units**:
- 2a — Pass-through + happy substitution + tests
- 2b — Empty-args + tail-append-fallback + tests
- 2c — `UnknownCommandError` + raise path + tests

---

### P5b-T3: CLI integration + `--no-commands` flag ✅

**Description**: Wire `expand_command` into `cli._run_ask`. Mirror the
Phase 5c `--no-skills` flag and the bootstrap chain. The slash expansion
runs **after** Settings load + log config but **before** the user message
construction.

**Acceptance**:
- [ ] `cli._run_ask`:
  - Resolves global dir (`~/.openharness/commands/`) and project dir
    (`cwd/.openharness/commands/`)
  - Instantiates `FilesystemCommandStore(global_dir, project_dir)`
  - Calls `expand_command(prompt, store)` to resolve the user input
  - `UnknownCommandError` → caught at top level, emits "Unknown command"
    UX with the catalog; exit code 1
- [ ] `--no-commands` CLI flag swaps in `EmptyCommandStore` → slash
  prefix passes through verbatim to LLM (escape hatch for testing /
  raw prompts)
- [ ] CLI unit tests:
  - Slash command resolves and is sent to LLM as user message (verified
    via captured request from stub client)
  - Unknown command → exit code 1 with available list in stderr
  - `--no-commands` → slash prefix in prompt reaches LLM unchanged
  - No slash prefix → prompt unchanged
  - Project command overrides global

**Files**:
- `src/openharness/cli.py` (+bootstrap + flag + error UX)
- `tests/cli/test_cli.py` (+`TestSlashCommands` class)

**Sub-units**:
- 3a — `_run_ask` expand integration + happy-path tests
- 3b — `--no-commands` flag + tests
- 3c — `UnknownCommandError` error UX + tests

---

### P5b-T4: End-to-end smoke + invariant verification 🔜 NEXT

**Description**: Mirror P5c-T4 (Skills e2e). Stub LLM,real `expand_command`,
real (empty) hook chain,confirm the resolved prompt reaches the LLM.
Plus the **structural invariant verification** (the fourth tenant test).

**Acceptance**:
- [ ] `tests/commands/test_e2e.py` — end-to-end smoke:
  - Sample command on disk → CLI invocation → LLM receives resolved
    user message (captured via stub client)
  - Args substitution verified at the wire level (`{args}` →
    actual args in the request body)
- [ ] **Structural invariant** (`TestCrossCuttingInvariant`):
  - Read source of `permissions/checker.py`, `permissions/tier_based.py`,
    `hooks/executor.py`, `hooks/registry.py`, `engine/query.py`,
    `engine/context.py`, `observability/logging.py`, `prompts.py`,
    `tools/__init__.py`
  - Strip comments + docstrings
  - Assert no `Command` / `CommandStore` / `FilesystemCommandStore` /
    `parse_command` / `expand_command` / `UnknownCommandError`
    identifier appears anywhere
  - If any does → fourth tenant test failed → re-open boundary

**Files**:
- `tests/commands/test_e2e.py` (new)

**Sub-units**:
- 4a — E2E test with stub LLM + sample command fixture
- 4b — Structural invariant verification test

---

### P5b-T5: README + Coverage + retro

**Description**: README section, coverage close-out,
`learnings/phase-5b-commands.md`.

**Acceptance**:
- [ ] `commands/` module ≥ 95 % coverage
- [ ] Total coverage stays ≥ 95 %
- [ ] README "Phase 5b features — Slash Commands" section: how to
  author a command, where to put it, how `{args}` substitution works,
  the Commands vs Skills distinction (user-facing vs LLM-facing)
- [ ] `learnings/phase-5b-commands.md` — focus:fourth tenant
  validation; "pre-LLM" tenant is structurally even cleaner than
  Skills (commands vanish before reaching ANY LLM-facing layer);
  Commands ≠ Skills role split (user-facing UX vs LLM-facing
  knowledge); pattern recognition: anything resolved before
  `run_query` ≡ pre-LLM tenant ≡ zero invariant impact
- [ ] Phase 5b DoD checklist all green

**Files**:
- `README.md` (+section)
- `learnings/phase-5b-commands.md` (new)

**Sub-units**:
- 5a — Coverage gap audit + close
- 5b — README update
- 5c — `learnings/phase-5b-commands.md`
- 5d — DoD closeout

---

## Checkpoints

After each capability: **human review** of code ↔ acceptance walkthrough
per CLAUDE.md GREEN→review→commit pattern. Phase 5b is small enough that
cadence is per-task.

### After P5b-T1 + T2
- **Human review**: `expand_command` behavior on edge cases:
  - Trailing whitespace in args
  - Args containing `{args}` literal (Python format escaping)
  - Body with multiple `{args}` placeholders

### After P5b-T3
- **Human review**: error UX for unknown command — does the catalog
  format help LLM / user pivot? Compare to `LoadSkill` unknown name
  format.

### After P5b-T4 (Phase 5b complete)
- **Decision point**: enter Phase 6 (Sandbox — boundary + plan already
  on disk) or Phase 5d (ModeBundle — combine slash + skill + hook +
  permission overlay into the unified "mode" abstraction)?

---

## Risks

| Risk | Mitigation |
|---|---|
| `{args}` placeholder collides with Python `str.format` curly-brace conventions | Use a single placeholder name; document escaping (`{{` → `{`). Tests cover the curly-brace edge case. |
| User confuses slash commands (CLI shortcut) with skills (LLM knowledge) | README section explicitly contrasts the two; `learnings/phase-5b-commands.md` §3 makes the role split a load-bearing claim. |
| Project + global commands with the same name silently override → user surprise | Bootstrap warning log fires on override (same as Skills L2). |
| Phase 5d ModeBundle work later finds C1 too narrow (commands needed to declare hooks/permission overlays) | Phase 5d will own that scope. C1 is a DELIBERATE narrowing — the abstraction is "5b = pure template + 5d = mode bundle". If 5d wants to extend the same `Command` dataclass with `hooks: list[str]`, the model is forward-compatible (frontmatter fields are tolerated; we just emit no behavior for them yet). |

## Risks specifically NOT mitigated (Phase 5d+)

- A slash command can't (yet) restrict the LLM's tool catalog or
  inject hooks. Users wanting `/security-review` to actually constrain
  behavior must orthogonally use `--dry-run` / `OPENHARNESS_DENY_PATHS` /
  Python-registered hooks. This is the explicit C1+C5 narrowing.

---

## Pointers

- Boundary: [`decisions/14-phase-5b-boundary.md`](../decisions/14-phase-5b-boundary.md)
- Preview source: [`tasks/phase-5-preview.md`](./phase-5-preview.md) D15 section
- Phase 5c plan (parallel structure — borrow shape verbatim): [`tasks/phase-5c-skills-plan.md`](./phase-5c-skills-plan.md)
- Phase 5c retro §3.2 (the framing that justifies pure templating): [`learnings/phase-5c-skills.md`](../learnings/phase-5c-skills.md)
