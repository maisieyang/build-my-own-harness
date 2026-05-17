# Phase 5d Implementation Plan — ModeBundle (cross-layer composition)

> Phase 5b plan: [`tasks/phase-5b-plan.md`](./phase-5b-plan.md).
> Phase 5c plan: [`tasks/phase-5c-skills-plan.md`](./phase-5c-skills-plan.md).
> Boundary contract: [`decisions/17-phase-5d-boundary.md`](../decisions/17-phase-5d-boundary.md).

## Overview

**Phase 5d goal**: a slash command can reference a "mode bundle" by name;
the bundle's metadata (system_prompt / tool whitelist / extra deny_paths /
named hooks) is applied to the `QueryContext` at CLI bootstrap, and the
engine runs unchanged. This is the **first cross-layer tenant** —
ModeBundle composes Layer 0 (slash command trigger) + Layer 1 (system
prompt override) + Layer 2 (tool catalog filter) + Layer 3 (deny_paths
overlay + hook injection by name) without touching any of those layers.

**Total scope**: ~2-3 days, 5 capabilities, ~12-15 commits, ~400 lines
of production code.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/17-phase-5d-boundary.md`](../decisions/17-phase-5d-boundary.md) | D19.1 4-layer ModeBundle (system_prompt / tools.whitelist / deny_paths / hooks); D19.2 `bundles/` independent package, global + project storage; D19.3 trigger via slash command `mode:` frontmatter field; D19.4 built-in named hooks only (`audit_log` + `deny_writes`; plugin discovery defers to 5e); D19.5 pre-LLM resolution in cli.py; D19.6 `WhitelistRegistry` wraps `ToolRegistry` (no upstream change) |

## Task list

### P5d-T1: `bundles/` package foundation ✅

**Description**: Pure data + parsing. `Bundle` dataclass + frontmatter
parse + `FilesystemBundleStore` (global+project two-layer). Same shape
as `commands/` (Phase 5b) and `skills/` (Phase 5c).

**Acceptance**:
- [ ] `bundles/model.py` — `Bundle` frozen dataclass:
  - `name: str` (regex `^[A-Za-z][A-Za-z0-9_-]*$`)
  - `description: str`
  - `system_prompt: str | None = None`
  - `tools_whitelist: tuple[str, ...] | None = None`
  - `deny_paths: tuple[str, ...] = ()`
  - `hook_names: tuple[str, ...] = ()`
  - `source_path: Path`
- [ ] `bundles/model.py` — `parse_bundle(path) -> Bundle | None`:
  YAML frontmatter; never-raises; warning log on malformed (same
  discipline as `parse_skill` / `parse_command`)
- [ ] `bundles/store.py` — `BundleStore` Protocol + `FilesystemBundleStore`
  (global + project; project wins on collision) + `EmptyBundleStore`
  sentinel
- [ ] `bundles/errors.py` — `UnknownBundleError(OpenHarnessError)`
  for "command references nonexistent bundle"
- [ ] Tests:
  - Bundle dataclass frozen / name regex / required fields
  - `parse_bundle` happy: minimal (only name + description),
    full (all 4 override fields), `tools.whitelist` as YAML list
  - `parse_bundle` error paths (8+): file missing / no frontmatter /
    malformed YAML / missing name / missing description / invalid
    name regex / tools.whitelist not a list / hooks not a list
  - Store discovery (global only / project only / both / project
    overrides global / malformed skipped)

**Files**:
- `src/openharness/bundles/__init__.py` (new package)
- `src/openharness/bundles/model.py` (new)
- `src/openharness/bundles/store.py` (new)
- `src/openharness/bundles/errors.py` (new)
- `tests/bundles/__init__.py`, `tests/bundles/test_model.py`,
  `tests/bundles/test_store.py` (new)

**Sub-units**:
- 1a — `Bundle` dataclass + happy parse + tests
- 1b — `parse_bundle` error paths + warning + tests
- 1c — `FilesystemBundleStore` + Protocol + sentinel + tests

---

### P5d-T2: `BUILTIN_HOOKS` registry — `audit_log` + `deny_writes` ✅

**Description**: Phase 5d's 2 framework-provided hooks. Each is a small
async callable (matches `Hook` Protocol from P3-T4) registered into a
module-level dict so bundle frontmatter can reference them by name.

**Acceptance**:
- [ ] `bundles/hooks.py` — `BUILTIN_HOOKS: dict[str, tuple[HookEvent, Hook]]`
- [ ] `audit_log` hook:
  - Event: `PostToolUse`
  - Behavior: emits a structured `audit` log event with
    `tool_name` / `is_error` / `output_len` fields (same shape as
    existing `tool_complete` log but tagged `event=audit` for `jq`
    filtering)
  - Returns `None` (passthrough — doesn't modify ToolResult)
- [ ] `deny_writes` hook:
  - Event: `PreToolUse`
  - Behavior: looks up the tool by name in the registry; if
    `tool.is_read_only is False`, returns `HookResult(decision="deny",
    message=f"deny_writes: tool {name} is not read-only")`; else
    passthrough
  - Note: relies on `tool.is_read_only` attribute from P3-T1 + P5/6/7
    (Read/Grep/LoadSkill are True; Write/Edit/Bash/SpawnAgent/MCP-untrusted
    are False)
- [ ] `resolve_hook(name) -> tuple[HookEvent, Hook]` lookup; raises
  `UnknownBundleError` (re-using same error type) on miss
- [ ] Tests:
  - `audit_log` fires on PostToolUse with correct fields
  - `deny_writes` denies a Write call, allows a Read call
  - `resolve_hook` raises on unknown name
  - `BUILTIN_HOOKS` keys: at minimum `audit_log` and `deny_writes`

**Files**:
- `src/openharness/bundles/hooks.py` (new)
- `tests/bundles/test_hooks.py` (new)

**Sub-units**:
- 2a — `audit_log` hook + tests
- 2b — `deny_writes` hook + tests (uses TierBasedPermissionChecker
  patterns from P3-T3 for tool name → is_read_only lookup via context)
- 2c — `resolve_hook` + lookup error + tests

---

### P5d-T3: `WhitelistRegistry` + bundle application logic ✅

**Description**: The bridge layer — wrapping ToolRegistry for tool
filter (D19.6); helper function that takes a bundle and produces the
QueryContext modifications.

**Acceptance**:
- [ ] `bundles/registry.py` — `WhitelistRegistry`:
  - Satisfies same shape as `ToolRegistry` (`get` / `list_tools` /
    `to_api_schema`) so engine treats it transparently
  - `__init__(base, whitelist: set[str])` — wraps base; only
    whitelisted names exposed
  - `get(name)`: raises `KeyError` if not in whitelist; else delegates
  - `list_tools()`: filtered subset
  - `to_api_schema()`: filtered subset
  - If whitelist contains a name not present in base → warning log at
    bootstrap (not raise — same discipline)
- [ ] `bundles/__init__.py` — `apply_bundle_to_context(...)` helper
  function: takes (base_tool_registry, base_hook_registry, base_settings,
  base_system_prompt, bundle) returns (effective_tool_registry,
  effective_hook_registry, effective_settings, effective_system_prompt).
  Pure function — no I/O — easy to test.
- [ ] Tests:
  - `WhitelistRegistry` exposes only whitelisted tools (4 surfaces:
    `get` / `list_tools` / `to_api_schema` / unknown raises KeyError)
  - `apply_bundle_to_context` with empty bundle = no-op
  - `apply_bundle_to_context` with system_prompt-only bundle = only
    that field changes
  - `apply_bundle_to_context` with full bundle = all 4 layers applied
  - Hook resolution: bundle's `hook_names` → hooks register into
    `effective_hook_registry`; unknown name raises

**Files**:
- `src/openharness/bundles/registry.py` (new)
- `src/openharness/bundles/apply.py` (new — function lives here)
- `tests/bundles/test_registry.py` (new)
- `tests/bundles/test_apply.py` (new)

**Sub-units**:
- 3a — `WhitelistRegistry` + tests
- 3b — `apply_bundle_to_context` helper + tests

---

### P5d-T4: CLI integration + Command.mode field ✅

**Description**: `commands/model.py` gains optional `mode: str | None
= None` field. `cli._run_ask` loads the bundle when present + applies
overrides to `QueryContext`. `UnknownBundleError` → user-facing
error UX similar to `UnknownCommandError` (P5b-T3).

**Acceptance**:
- [ ] `commands/model.py`:
  - `Command` dataclass gains `mode: str | None = None`
  - `parse_command` reads `mode:` from frontmatter, validates as
    safe-identifier regex (same as `name`); None if absent
- [ ] `cli._run_ask` bootstrap chain extension:
  - After `expand_command` (returns expanded prompt), also returns
    the resolved `Command` object (or just its `mode` value) — needs
    a small refactor to `expand_command` (return `(prompt, mode)`
    tuple or new helper)
  - If `mode` is set:
    - Load bundle from `FilesystemBundleStore(global, project)`
    - Apply bundle to context construction via
      `apply_bundle_to_context`
  - If bundle loaded but unknown: `UnknownBundleError` raised; `ask`
    command catches it → "Unknown bundle: ..." stderr; exit code 1
- [ ] Catalog injection (`prompts.py`): bundle's `system_prompt`
  REPLACES the base prompt entirely (skips the base instructions);
  if bundle.system_prompt is None, base prompt used. The tool catalog
  section reflects the WhitelistRegistry (already handled by passing
  the wrapped registry to `build_system_prompt`).
- [ ] CLI tests:
  - Slash command without `mode:` works as in 5b (regression check)
  - Slash command with `mode: code-review` loads bundle, applies all
    4 layers — captured QueryContext reflects:
    - `system_prompt` = bundle's prompt
    - `tool_registry` is `WhitelistRegistry` instance
    - `permission_checker._deny_paths` includes bundle's deny_paths
    - `hook_registry` has bundle's hooks registered
  - `mode: nonexistent` → exit 1 + "Unknown bundle" stderr

**Files**:
- `src/openharness/commands/model.py` (+1 field)
- `src/openharness/commands/expand.py` (potentially refactor return
  shape to surface `mode`)
- `src/openharness/cli.py` (+bootstrap + new error arm)
- `tests/commands/test_model.py` (+`mode` tests)
- `tests/cli/test_cli.py` (+`TestBundles`)

**Sub-units**:
- 4a — Command.mode field + parse + tests
- 4b — CLI bootstrap chain extension + tests
- 4c — UnknownBundleError error UX + tests

---

### P5d-T5: Cross-cutting invariant + README + retro ✅

**Description**: Extended structural invariant + git-diff verification +
docs + DoD closeout.

**Acceptance**:
- [x] `tests/execution/test_invariant.py` extended forbidden set: added
  `TestPhase5dCrossCuttingInvariant` with 46 protected modules + 12
  forbidden identifiers (`Bundle`, `WhitelistRegistry`, `BUILTIN_HOOKS`,
  `parse_bundle`, `apply_bundle_to_context`, `UnknownBundleError`,
  `BundleApplication`, `FilesystemBundleStore`, `EmptyBundleStore`,
  `BundleStore`, `resolve_hook`, `openharness.bundles`).
- [x] **Formal git-diff invariant verification** (in retro): vs Phase
  7b close (`57f273b`):
  - permissions/ → **0 lines** ✓
  - hooks/ → **0 lines** ✓
  - engine/ → **0 lines** ✓
  - observability/ → **0 lines** ✓
  - mcp/ → **0 lines** ✓
  - compaction/ → **0 lines** ✓
  - skills/ → **0 lines** ✓
  - protocols/ → **0 lines** ✓
  - tools/ → **0 lines** ✓
  - execution/ → **0 lines** ✓
  - commands/model.py → 1 additive field (`mode`) ✓
- [x] `bundles/` module ≥ 95% coverage (96%+ on all files)
- [x] Total coverage ≥ 95% (97.06%)
- [x] README "Phase 5d — ModeBundle" section authored with full
  authoring example, slash-command trigger explanation, built-in
  hooks documentation, cross-layer invariant claim.
- [x] `learnings/phase-5d.md` retro authored covering: first
  cross-layer tenant; Bundle-as-QueryContext-factory pattern;
  subclass-vs-Protocol decision driven by invariant; built-in hooks
  vs plugin discovery node; rule-of-three triggering for
  markdown_store/ (deferred to Phase 8); `deny_writes` passthrough
  semantics; "what 5d didn't do and why".
- [x] Phase 5d DoD checklist all green

**Files**:
- `tests/execution/test_invariant.py` (extend forbidden set)
- `tests/bundles/test_e2e.py` (new — full chain CLI test)
- `README.md` (+section)
- `learnings/phase-5d.md` (new)
- `tasks/phase-5d-plan.md` (DoD closeout)

**Sub-units**:
- 5a — Invariant extension + git-diff verification
- 5b — Full-chain e2e test (stub LLM, real bundle load + apply)
- 5c — README + retro

---

## Checkpoints

After each capability: **human review** of code ↔ acceptance per
CLAUDE.md GREEN→review→commit pattern.

### After P5d-T1 + T2
- **Human review**: `audit_log` log shape — does it look right for
  `jq` filtering? Should `event` name be `audit_tool_complete` to
  avoid collision with `tool_complete`?

### After P5d-T3
- **Human review**: `WhitelistRegistry` — does the engine see it
  exactly like `ToolRegistry`? Test by feeding it into a real
  `_dispatch_one` flow.

### After P5d-T4
- **Real `oh ask` smoke** if Docker not needed: write a sample
  bundle + command, run them, verify the LLM gets the bundle's
  system prompt + only whitelisted tools.

### After P5d-T5 (Phase 5d complete)
- **Decision point**: Phase 5e (plugin hooks?) / Phase 8 (Polish) /
  Phase 7c (gVisor)?

---

## Risks

| Risk | Mitigation |
|---|---|
| Hook event semantics ambiguity (PreToolUse fires before AuthZ? after?) | Re-read P3-T4 boundary to confirm order; document in `bundles/hooks.py` |
| `WhitelistRegistry.get(name)` raising KeyError might fire in unexpected dispatch paths | T3 acceptance: integration smoke runs a full dispatch with whitelist active to catch any path |
| `audit_log` log shape collides with existing `tool_complete` log → trace consumers confused | Use distinct `event` name (e.g., `audit_log` or `mode_audit`); document in retro |
| User confuses `mode` (bundle ref) with `mode` (Phase 5b deferred `stateful` field) | Keep field name; document in retro that 5b's `mode:` placeholder is now defined by 5d |
| `deny_paths` merge semantics: bundle augments OR replaces Settings.deny_paths? | Lock in retro: AUGMENTS (bundle's deny_paths added to Settings.deny_paths, never replaces) — safer default |

## Risks specifically NOT mitigated (Phase 5e+)

- User-supplied custom hooks via plugin discovery — Phase 5e
- Bundle inheritance — deferred
- `tools.blacklist` (vs whitelist) — deferred
- Mid-conversation bundle switching — Phase 7+ `oh chat` mode

---

## Pointers

- Boundary: [`decisions/17-phase-5d-boundary.md`](../decisions/17-phase-5d-boundary.md)
- Phase 5b commands (trigger source): [`tasks/phase-5b-plan.md`](./phase-5b-plan.md)
- Phase 5c skills (storage shape mirrored): [`tasks/phase-5c-skills-plan.md`](./phase-5c-skills-plan.md)
- Phase 3 hook boundary (named hook composition basis): [`decisions/08-phase-3-boundary.md`](../decisions/08-phase-3-boundary.md) D13.1
- Phase 7a/7b retro (where "stable plateau" + "abstraction-first" insights live): [`learnings/phase-7a.md`](../learnings/phase-7a.md), [`learnings/phase-7b.md`](../learnings/phase-7b.md)
