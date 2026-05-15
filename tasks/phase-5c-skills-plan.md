# Phase 5c Implementation Plan — Skills

> Phase 5a (MCP) plan: [`tasks/phase-5-plan.md`](./phase-5-plan.md).
> Phase 5b (Slash Command) preview only: [`tasks/phase-5-preview.md`](./phase-5-preview.md).
>
> Boundary contract: [`decisions/12-phase-5c-skills-boundary.md`](../decisions/12-phase-5c-skills-boundary.md).
> Framing basis: [`tasks/phase-5c-skills-preview.md`](./phase-5c-skills-preview.md) —
> the deep unification: Skill loading = LLM-as-RPC + tool dispatch, NOT a new mechanism.

## Overview

**Phase 5c goal**: `oh ask` discovers `.md` skill files at bootstrap, surfaces
their catalog in the system prompt, exposes a `LoadSkill(name)` tool, and lets
the LLM lazy-load skill bodies on demand. The cross-cutting **invariant** to
verify (third time): `permissions/`, `hooks/`, `engine/query.py`,
`observability/logging.py` **stay unchanged** — Skills are a tenant of the
existing tool dispatch pattern.

**Total scope**: ~3-5 days, 5 capabilities, ~10-15 commits, ~170 lines of
production code.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/12-phase-5c-skills-boundary.md`](../decisions/12-phase-5c-skills-boundary.md) | L1 markdown+YAML frontmatter; L2 global+project storage (project overrides); L3 always-on catalog injection; L4 lazy body via LoadSkill tool; L5 `is_read_only=True`; L6 minimal frontmatter schema (name + description required); L7 no skill-level size limit (Phase 4 compaction handles) |

## Task list

### P5c-T1: `skills/` package foundation ✅

**Description**: Pure data + parsing layer. `Skill` dataclass + frontmatter
parse + filesystem discovery. No tool, no prompt, no CLI — just the data model
the next capabilities plug into. Same scope shape as P5-T1 (`McpServerConfig`).

**Acceptance**:
- [x] `skills/model.py` — `Skill` frozen dataclass (`name: str`,
  `description: str`, `body: str`, `version: str | None`, `source_path: Path`)
- [x] `skills/model.py` — `parse_skill(path) -> Skill | None` — reads file,
  splits YAML frontmatter from body, validates required fields; returns
  `None` + emits warning log on malformed input (NOT raise — bootstrap
  must not crash on one bad skill)
- [x] `skills/store.py` — `FilesystemSkillStore(global_dir, project_dir)`:
  - `discover() -> dict[str, Skill]` — scans both dirs, parses each `.md`,
    returns `name → Skill` map; **project entries override global** on
    same `name`
  - Pure sync (discovery happens once at bootstrap)
- [x] Also landed: `SkillStore` Protocol + `EmptySkillStore` sentinel
  (T2 needs both; cheap to land now)
- [x] `name` validation: matches `^[A-Za-z][A-Za-z0-9_-]*$` (same regex as
  `McpServerConfig.name`; reused for tool-argument safety)
- [x] Tests (53 tests):
  - `Skill` dataclass frozen + happy path + 7 valid + 8 invalid name regex
  - `parse_skill` happy: minimal / with version / string version / body verbatim / extra fields
  - `parse_skill` errors (11): file missing / no frontmatter / unclosed / malformed YAML /
    not-a-mapping / missing name / missing description / empty name / invalid regex /
    empty description / name-not-string / open-fence-no-newline
  - Line-ending edge cases: CRLF + closing-fence-at-EOF
  - `FilesystemSkillStore`: both-dirs-none / nonexistent / empty / global only /
    project only / project overrides global / unrelated coexist / malformed
    file skipped / `.md` only / cache 4 surfaces / same-layer collision
- [x] mypy strict + ruff clean; coverage skills/ 100%; full-suite 96.74% (≥ 95%)

**Verification**:
```bash
uv run pytest tests/skills/ --cov=openharness.skills --cov-fail-under=95
uv run mypy --strict src/openharness/skills/ tests/skills/
```

**Files**:
- `src/openharness/skills/__init__.py` (new package; exports `Skill`,
  `FilesystemSkillStore`, `parse_skill`)
- `src/openharness/skills/model.py` (new)
- `src/openharness/skills/store.py` (new)
- `tests/skills/__init__.py`, `tests/skills/test_model.py`,
  `tests/skills/test_store.py` (new)

**Sub-units**:
- 1a — `Skill` dataclass + `parse_skill` happy + tests
- 1b — `parse_skill` error paths (malformed YAML, missing required) + warning log + tests
- 1c — `FilesystemSkillStore.discover()` + project-overrides-global + tests

**Dependency**: PyYAML (likely already transitive via pydantic-settings, verify;
otherwise add to pyproject). NOT adding a frontmatter-specific library —
splitting on `---` delimiter + `yaml.safe_load` is ~10 lines.

---

### P5c-T2: `LoadSkillTool` — BaseTool subclass ✅

**Description**: The single tool that exposes skills to the LLM. Pydantic
input model (`name: str`); `execute()` body resolves `name` against the
store, returns body as `ToolResult`. `is_read_only=True` per L5.

**Acceptance**:
- [x] `tools/load_skill.py` — `LoadSkillTool(BaseTool[LoadSkillInput])`:
  - `name = "LoadSkill"` (PascalCase per D6.4)
  - `is_read_only = True`
  - `description` references the 'Available Skills' catalog
  - `input_model: name: str` — validation deferred to execute (uniform
    `is_error` flow whether name is invalid or just unknown)
  - `execute(args, ctx)`: constructor-injected `SkillStore` (same pattern
    as `McpToolAdapter`); known → body; unknown → catalog surfaced
- [x] `QueryContext.skill_store: SkillStore` field; default `EmptySkillStore`
  so all 855 existing tests pass unchanged
- [x] Tests (15):
  - Round-trip: 2 happy cases (two distinct skills)
  - Unknown name: is_error + catalog surfaced + empty store "(none)"
  - **Invariant** (3 tests):
    - `TierBasedPermissionChecker.evaluate(LoadSkill, ...)` → `Decision.ALLOW`
    - Allow holds even for unknown skill names (permission cares about
      `is_read_only`, not the particular `name`)
    - `permissions/checker.py` + `tier_based.py` introspected: no
      `LoadSkillTool` reference (abstraction not leaked)
  - Static: name / is_read_only / input_model / description / to_api_schema

**Verification**: hook + permission tests prove the invariant.

**Files**:
- `src/openharness/skills/store.py` — add `SkillStore` Protocol + `EmptySkillStore`
- `src/openharness/tools/load_skill.py` (new)
- `src/openharness/engine/context.py` (+`skill_store` field)
- `src/openharness/tools/__init__.py` (export LoadSkillTool)
- `tests/tools/test_load_skill.py` (new)

**Sub-units**:
- 2a — `SkillStore` Protocol + `EmptySkillStore` + `QueryContext.skill_store` + tests (engine context unchanged shape)
- 2b — `LoadSkillTool` + Pydantic input model + happy path + tests
- 2c — Error paths (unknown name) + hook/permission invariant verification tests

---

### P5c-T3: `prompts.py` catalog injection + CLI bootstrap ✅

**Description**: Bridge layer. `prompts.py` takes an optional `SkillStore`
and injects the "Available Skills" section into the system prompt. `cli.py`
at bootstrap: instantiates `FilesystemSkillStore`, passes to QueryContext,
registers `LoadSkillTool` iff skills are discovered.

**Acceptance**:
- [x] `prompts.py` `build_system_prompt(tools, env, *, skill_store=None)`
  adds an optional `## Available Skills (call LoadSkill to expand)` section
  between Tools and Environment, sorted-by-name bullet list
- [x] Empty store / `None` store → section omitted entirely (no header
  noise; mirrors how MCP doesn't emit "(no servers)")
- [x] Backward compat: `build_system_prompt(t, e) == build_system_prompt(t, e, skill_store=None)`
- [x] `cli.py _run_ask`:
  - Resolves global dir (`~/.openharness/skills/`) via `Path.home()` and
    project dir (`env.cwd / .openharness / skills`)
  - Instantiates `FilesystemSkillStore(global_dir, project_dir)`
  - Discovers skills; if ≥ 1 found, registers `LoadSkillTool(store)`
  - Passes store to `QueryContext.skill_store` (default `EmptySkillStore`
    from T1; threaded through)
- [x] `--no-skills` CLI flag swaps in `EmptySkillStore` and skips
  registration, regardless of filesystem state
- [x] Tests (11 new):
  - `TestSkillsCatalogSection` (6): None / empty / populated / section
    order / sort determinism / byte-identical backward compat
  - `TestSkillsBootstrap` (5): empty dirs → no LoadSkill; project skill
    registers LoadSkill + catalog in prompt; global-only visible;
    project overrides global; `--no-skills` short-circuits everything

**Files**:
- `src/openharness/prompts.py` (+catalog section)
- `src/openharness/cli.py` (+bootstrap)
- `tests/test_prompts.py` (+catalog tests)
- `tests/cli/test_cli.py` (+`TestSkillsBootstrap`)

**Sub-units**:
- 3a — `prompts.py` catalog injection + tests
- 3b — `cli.py` bootstrap wire + `--no-skills` flag + tests

---

### P5c-T4: End-to-end smoke + invariant verification ✅

**Description**: Mirror P5-T5 invariant verification — run a real `oh ask`
flow that loads a skill, assert the trace shows the full lifecycle, and
formally verify the four "zero diff" files.

**Acceptance**:
- [x] `tests/skills/test_e2e.py` — end-to-end test with stub LLM (5 tests):
  - Full load cycle: skill on disk → catalog in system prompt → LLM emits
    `LoadSkill(name="test-helper")` → real engine dispatch → body in
    next-turn `tool_result` → end_turn
  - Unknown name returns `is_error=True` + catalog (errors-as-payload)
  - LoadSkill appears in `to_api_schema()` (visible to LLM)
- [x] **Invariant verification** (formal source introspection, not just
  git diff):
  - `TestCrossCuttingInvariant.test_protected_modules_do_not_reference_skills`
    reads `permissions/checker.py` + `permissions/tier_based.py` +
    `hooks/executor.py` + `engine/query.py` + `observability/logging.py`
    source code (strips comments / docstrings) and asserts NO
    `LoadSkill` / `LoadSkillTool` / `SkillStore` / `FilesystemSkillStore` /
    `parse_skill` identifier appears anywhere
  - `test_load_skill_satisfies_BaseTool_only` walks `LoadSkillTool.__mro__`
    and asserts no `Permission` / `Hook` / `Engine` / `QueryContext` class
    in the chain (Skills are a tenant of BaseTool only)
- [x] README "Phase 5c features — Skills" section: how to write a skill,
  where to put it, how the LLM uses it via LoadSkill + the Index/Lookup/
  Content/Recurse pattern reference

**Files**:
- `tests/skills/test_e2e.py` (new)
- `README.md` (+section)
- `tasks/phase-5c-skills-plan.md` (DoD checklist closeout)

**Sub-units**:
- 4a — E2E test with stub LLM + skill fixture
- 4b — Invariant `git diff` verification script + manual gate
- 4c — README update

---

### P5c-T5: Coverage + retro 🔜 NEXT

**Description**: Coverage gap audit; `learnings/phase-5c-skills.md`.

**Acceptance**:
- [ ] `skills/` module ≥ 95 % coverage
- [ ] `tools/load_skill.py` ≥ 95 %
- [ ] Total coverage stays ≥ 95 %
- [ ] `learnings/phase-5c-skills.md` — focus: third invariant tenant
  validation; the unifying pattern (Index/Lookup/Content/Recurse) as
  Phase 5+ rosetta stone; why "Skills" needed only ~170 lines vs Phase 5a
  MCP's much larger surface
- [ ] Phase 5c DoD checklist all green

**Sub-units**:
- 5a — Coverage gap audit + close
- 5b — `learnings/phase-5c-skills.md`
- 5c — DoD closeout

---

## Checkpoints

After each capability: **human review** of code ↔ acceptance walkthrough
(per CLAUDE.md GREEN→review→commit pattern). Phase 5c is small enough that
review cadence is per-task.

### After P5c-T1 + T2
- **Human review**: `QueryContext.skill_store` field shape — `Protocol` or
  concrete? Default `EmptySkillStore` semantics correct?

### After P5c-T3
- **Human review**: catalog format in system prompt — should LLM see
  `### name` / `- name: desc` / something more structured?
- **Real `oh ask` smoke**: write a real skill, run a real prompt, see
  catalog injected + LoadSkill triggered

### After P5c-T4 (Phase 5c complete)
- **Decision point**: Phase 5b slash command next, or Phase 6 sandbox, or
  Phase 5 MCP close-out continues (T6/T7)?
- **Three invariant tenants validated**: MCP (5a) + Skills (5c) + Sandbox
  (preview 6) — sufficient evidence that Phase 3 abstractions are stable.
  Worth writing a cross-phase synthesis in `learnings/`.

---

## Risks

| Risk | Mitigation |
|---|---|
| YAML frontmatter parse edge cases (escaping, multi-line strings) | Use `yaml.safe_load`; tests parametrize common shapes; malformed → skip + warning, never crash |
| Skill name collision across global / project | L2 says project wins; bootstrap logs warning when override happens; test covers |
| Catalog grows past sensible size (> 20 skills) | Frontmatter description should stay 1-line; Phase 6+ may add LLM-gated filtering when justified by real usage |
| LLM doesn't learn to call LoadSkill | `LoadSkillTool.description` is the contract; iterate based on real `oh ask` traces in T4 |
| Skill body > `tool_result_cap` (Phase 4 default 10K tokens) | L7 — same compaction as any oversized tool result; head/tail truncate with marker; LLM sees `[truncated N tokens]` and can pivot |

## Risks specifically NOT mitigated (Phase 6+)

- A skill that requires loading multiple other files (e.g.,
  "see fixtures/auth.json") — current LoadSkill only loads the .md body.
  LLM can use `Read` tool to follow references manually.
- Cross-session skill cache / persistence — each `oh ask` re-scans
  filesystem; trivial latency at < 50 skills.

---

## Pointers

- Boundary: [`decisions/12-phase-5c-skills-boundary.md`](../decisions/12-phase-5c-skills-boundary.md)
- Preview (deep framing): [`tasks/phase-5c-skills-preview.md`](./phase-5c-skills-preview.md)
- Phase 5a MCP plan (T1-T7 archive — P5c borrows the "new package + Settings + tool adapter" rhythm): [`tasks/phase-5-plan.md`](./phase-5-plan.md)
- Phase 1+2 retro (LLM-as-RPC framing — Skills is the third tenant): [`learnings/phase-1-and-2.md`](../learnings/phase-1-and-2.md) §6
