# Phase 5e Implementation Plan — Plugin Hook Discovery

> Phase 5d plan: [`tasks/phase-5d-plan.md`](./phase-5d-plan.md).
> Boundary contract: [`decisions/18-phase-5e-boundary.md`](../decisions/18-phase-5e-boundary.md).

## Overview

**Phase 5e goal**: a Python package that ships `@hook_spec`-decorated
async callables under the `openharness.hooks` entry-point group can
register named hooks that bundle frontmatter references just like
the built-in `audit_log` / `deny_writes`. Discovery is opt-in via a
Settings flag. The bundle subsystem's `hook_names:` resolution path
gains one extra lookup branch; everything else (engine, dispatch,
hook executor) stays unchanged.

**Total scope**: ~1-2 days, 4 capabilities, ~6 commits, ~150 lines
of production code + ~150 lines test.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/18-phase-5e-boundary.md`](../decisions/18-phase-5e-boundary.md) | D20.1 entry-point discovery (`openharness.hooks` group); D20.2 `@hook_spec(event)` decorator returns `HookSpec`; D20.3 opt-in via `Settings.enable_plugin_hooks`; D20.4 collision policy (framework > plugins > error); D20.5 `resolve_hook(name, plugin_catalog=None)` + `apply_bundle_to_context(..., plugin_hook_catalog=None)` additive kwargs; D20.6 `entry_point_source` test seam |

## Task list

### P5e-T1: `bundles/hook_plugins.py` — discovery scaffolding 🔜 NEXT

**Description**: Pure data + decorator + discovery function. No CLI
or Settings wiring yet. Tests use the `entry_point_source` seam.

**Acceptance**:
- [ ] `bundles/hook_plugins.py` — `HookSpec` frozen dataclass:
  - `event: HookEvent`
  - `hook: Hook`
- [ ] `bundles/hook_plugins.py` — `hook_spec(event)` decorator:
  - Signature `(event: HookEvent) -> Callable[[Hook], HookSpec]`
  - Returns `HookSpec(event=event, hook=decorated_fn)`
- [ ] `bundles/hook_plugins.py` — `discover_plugin_hooks` function:
  - Signature `(*, group: str = "openharness.hooks",
    entry_point_source: Callable[..., EntryPoints] | None = None)
    -> dict[str, HookSpec]`
  - Reads via `importlib.metadata.entry_points(group=group)` when
    `entry_point_source` is None
  - Per-entry skip-not-fail (4-5 error paths):
    - `ep.load()` raises → warn `plugin_hook_load_failed`
    - Loaded value isn't `HookSpec` → warn `plugin_hook_invalid_spec`
    - Name collides with `BUILTIN_HOOKS` → warn
      `plugin_hook_collides_with_builtin`
    - Name collides with already-loaded plugin → warn
      `plugin_hook_collision`
- [ ] `bundles/__init__.py` exports `HookSpec`, `hook_spec`,
  `discover_plugin_hooks`
- [ ] Tests:
  - `HookSpec` dataclass frozen + happy construction
  - `hook_spec` decorator returns correct `HookSpec(event, hook)`
  - `discover_plugin_hooks` happy: two stub entry points → 2-entry
    dict
  - Discovery skips on `ep.load()` raise (4 error paths)
  - Discovery skips on type mismatch
  - Discovery skips + warns on built-in collision (e.g. plugin named
    `audit_log`)
  - Discovery skips + warns on plugin-plugin collision (same name in
    two entry points)
  - Empty `entry_point_source` returns `{}`

**Files**:
- `src/openharness/bundles/hook_plugins.py` (new)
- `src/openharness/bundles/__init__.py` (export additions)
- `tests/bundles/test_hook_plugins.py` (new)

**Sub-units**:
- 1a — `HookSpec` + `hook_spec` decorator + tests
- 1b — `discover_plugin_hooks` happy path + tests
- 1c — Discovery skip-not-fail paths + collision tests

---

### P5e-T2: Wire plugin catalog into `resolve_hook` + `apply_bundle_to_context`

**Description**: Two function signatures gain an additive
`plugin_catalog` / `plugin_hook_catalog` kwarg (default `None`).
Without modifying any existing behavior, the catalog flows through
to the inner lookup.

**Acceptance**:
- [ ] `bundles/hooks.py` — `resolve_hook(name, plugin_catalog=None)`:
  - When `plugin_catalog` is `None` or empty, behavior identical to
    Phase 5d (only `BUILTIN_HOOKS` consulted).
  - When provided, lookup order: `BUILTIN_HOOKS` → `plugin_catalog`
    → raise. Built-ins always shadow plugins on the same name.
  - `UnknownBundleError.available` lists union of built-ins + plugin
    names (sorted) so the error UX gives a complete catalog.
- [ ] `bundles/apply.py` — `apply_bundle_to_context(...,
  plugin_hook_catalog=None)`:
  - New kwarg threaded into `_clone_hook_registry`'s loop where
    `resolve_hook` is invoked
  - Phase 5d call sites that don't pass the kwarg stay byte-identical
- [ ] Tests:
  - `resolve_hook` with `plugin_catalog=None` (Phase 5d regression)
  - `resolve_hook` returns plugin hook by name when in catalog
  - `resolve_hook` built-in name takes precedence even if plugin
    catalog has same name (defensive)
  - `resolve_hook` unknown name lists both catalogs in error
  - `apply_bundle_to_context` with plugin catalog: bundle's
    `hook_names` resolves against plugins
  - `apply_bundle_to_context` without plugin catalog: Phase 5d
    behavior preserved (regression)

**Files**:
- `src/openharness/bundles/hooks.py` (extend signature)
- `src/openharness/bundles/apply.py` (extend signature)
- `tests/bundles/test_hooks.py` (extend)
- `tests/bundles/test_apply.py` (extend)

**Sub-units**:
- 2a — `resolve_hook` extension + tests
- 2b — `apply_bundle_to_context` extension + tests

---

### P5e-T3: Settings flag + CLI flag + bootstrap wiring

**Description**: `Settings.enable_plugin_hooks` + Typer flag +
`cli._run_ask` calls `discover_plugin_hooks` once when flag is on,
threads the catalog through `apply_bundle_to_context`.

**Acceptance**:
- [ ] `config/settings.py`:
  - `enable_plugin_hooks: bool = Field(default=False, ...)` field
  - Env var: `OPENHARNESS_ENABLE_PLUGIN_HOOKS`
- [ ] `cli.py`:
  - `--enable-plugin-hooks` / `--no-enable-plugin-hooks` Typer
    option on `ask`
  - `_run_ask` reads override or settings value, calls
    `discover_plugin_hooks()` when on (empty `{}` when off), passes
    to `apply_bundle_to_context` as `plugin_hook_catalog`
- [ ] CLI tests:
  - Flag off (default): bundle referencing a plugin hook raises
    UnknownBundleError → exit 1 with "Unknown hook: <name>" stderr
  - Flag on: bundle referencing a discovered plugin hook resolves +
    registers (verify via captured QueryContext)
  - Env var (`OPENHARNESS_ENABLE_PLUGIN_HOOKS=true`) enables flag
    same as CLI option
  - `--no-enable-plugin-hooks` overrides env var
  - Bundle hook_names that resolves to BOTH a built-in and a
    plugin → built-in wins (defensive integration test)

**Files**:
- `src/openharness/config/settings.py` (+1 field)
- `src/openharness/cli.py` (+flag + bootstrap)
- `tests/cli/test_cli.py` (extend `TestBundles` with plugin cases)
- `tests/config/test_settings.py` (extend with new field tests)

**Sub-units**:
- 3a — Settings field + env var tests
- 3b — CLI flag + bootstrap wiring + integration tests

---

### P5e-T4: Cross-cutting invariant + README + retro

**Description**: Extended structural invariant + git-diff
verification + docs + DoD closeout.

**Acceptance**:
- [ ] `tests/execution/test_invariant.py`
  `TestPhase5dCrossCuttingInvariant` forbidden set extended with
  `HookSpec`, `hook_spec`, `discover_plugin_hooks`. The 46 protected
  modules continue to enforce zero ref.
- [ ] **Formal git-diff invariant verification** (in retro): vs
  Phase 5d close (`878d80a`):
  - permissions/ → 0 lines
  - hooks/ → 0 lines
  - engine/ → 0 lines
  - observability/ → 0 lines
  - mcp/ → 0 lines
  - compaction/ → 0 lines
  - skills/ → 0 lines
  - commands/ → 0 lines
  - protocols/ → 0 lines
  - tools/ → 0 lines
  - execution/ → 0 lines
  - bundles/{model.py, store.py, registry.py, errors.py} → 0 lines
  - prompts.py → 0 lines
- [ ] `bundles/` module ≥ 95% coverage (incl. new hook_plugins.py)
- [ ] Total coverage ≥ 95%
- [ ] README "Phase 5e — plugin hook discovery" section:
  - Plugin author workflow (pyproject.toml + decorator)
  - End-user enable flow (`--enable-plugin-hooks` + bundle hook_names)
  - Collision policy (framework > plugins)
  - Security model (opt-in default OFF)
- [ ] `learnings/phase-5e.md` retro focusing on:
  - **Extension within the bundle subsystem** (not cross-layer)
  - "Catalog as additive lookup source" pattern
  - Entry points vs filesystem trade-off
  - `entry_point_source` test seam — why pure-function design
    matters for plugin systems
- [ ] Phase 5e DoD checklist all green

**Files**:
- `tests/execution/test_invariant.py` (extend)
- `README.md` (+section)
- `learnings/phase-5e.md` (new)
- `tasks/phase-5e-plan.md` (DoD closeout)

**Sub-units**:
- 4a — Invariant extension + git-diff verification
- 4b — README + retro

---

## Checkpoints

After each capability: **human review** of code ↔ acceptance per
CLAUDE.md GREEN→review→commit pattern.

### After P5e-T1
- **Human review**: `HookSpec` shape — should it carry more metadata
  (description, version, source attribution)? Currently event +
  callable only. Easy to add later but breaking change to plugin
  package contracts.

### After P5e-T2
- **Human review**: lookup precedence (built-in vs plugin). Currently
  built-in shadows; is that the right default? Reverse precedence
  would let users override `audit_log` with a custom version.

### After P5e-T3
- **Real plugin smoke**: author a minimal package locally with
  `pip install -e .`, declare an entry point, run `oh ask
  --enable-plugin-hooks "/cmd"` against a bundle that references
  the plugin hook. Verify it actually fires.

### After P5e-T4 (Phase 5e complete)
- **Decision point**: Phase 8 (markdown_store/ refactor)? Phase 7c
  (gVisor)? Phase 5f (filesystem hook plugins)? Phase 6+ (`oh chat`)?

---

## Risks

| Risk | Mitigation |
|---|---|
| `importlib.metadata.entry_points` behavior varies across Python versions / environments | Pin behavior via the `entry_point_source` seam in tests; document min Python in pyproject |
| User installs a malicious package that ships `openharness.hooks` entry points | D20.3 opt-in flag = explicit consent before discovery runs; README warns to inspect plugins |
| Plugin hook performance (slow async function blocks dispatch chain) | Same as built-in hook risk — plugin contract requires async + the dispatch loop has no timeout protection. Document; defer per-hook timeout to Phase 8 |
| Two plugins shipping the same name silently override | D20.4 collision policy: first-wins + warning. User-visible in logs. |
| `discover_plugin_hooks()` itself raises on environment quirks (e.g. corrupted package metadata) | Wrap the outer `entry_points()` call in try/except → returns `{}` + warning |

## Risks specifically NOT mitigated (Phase 5f+)

- Filesystem hook plugins (`~/.openharness/hooks/*.py`) — Phase 5f
- Per-bundle plugin scoping — YAGNI
- Plugin hot-reload — `oh chat` Phase 7+
- Plugin metadata for catalog UI (descriptions, versions) — Phase 8

---

## Pointers

- Boundary: [`decisions/18-phase-5e-boundary.md`](../decisions/18-phase-5e-boundary.md)
- Phase 5d retro §3.4 (where the deferral was recorded): [`learnings/phase-5d.md`](../learnings/phase-5d.md)
- Phase 5d `BUILTIN_HOOKS` (the catalog being extended): `src/openharness/bundles/hooks.py`
- Phase 7b opt-in flag shape (model for `--enable-plugin-hooks`): [`decisions/16-phase-7b-boundary.md`](../decisions/16-phase-7b-boundary.md) D18.1
