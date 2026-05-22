# Phase 9 Implementation Plan — Plugin System

> Boundary contract: [`decisions/24-phase-9-boundary.md`](../decisions/24-phase-9-boundary.md).
> Companion to Phase 5/5b/5c/5d/5e/5f extension subsystems — Phase 9
> doesn't replace them, it unifies their distribution.

## Overview

**Phase 9 goal**: Ship a **PluginManifest + PluginLoader** subsystem
that lets a user install a single directory (manually or via
`oh plugins install <git-url>`) and get all 5 component types
(commands / skills / bundles / hooks / MCP servers) registered into
the harness as a unit. The **cross-cutting invariant** (4th compounding
test): `mcp/` / `skills/` / `commands/` / `bundles/` / `engine/query.py`
dispatch / `permissions/` / `hooks/executor.py` / `protocols/` /
`compaction/` all stay **zero diff**.

The conceptual lesson Phase 9 cashes: **abstraction-first compounding
works even at the distribution layer**. Plugin is a multiplexer over
existing registration paths, not a new runtime.

**Total scope**: ~2.5 days, 5 capabilities, ~10-15 commits, ~300 lines
production code.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/24-phase-9-boundary.md`](../decisions/24-phase-9-boundary.md) | D27.1 YAML manifest with required `name/version/description` + 5 optional components; D27.2 filesystem-only distribution (Python pip-installable + marketplace defer to Phase 10); D27.3 plugin-name prefix namespacing (`my-plugin:component`); D27.4 opt-in via `Settings.enable_plugins` (default False); D27.5 inline MCP server config + env-var interpolation; D27.6 CLI list/show/install/remove |

## Task list

### P9-T1: `plugins/` package foundation 🔜 NEXT

**Description**: Pure data + parsing layer. `PluginManifest` dataclass,
YAML parser, validation, error types. No registration / fan-out yet —
just the data model the next capabilities plug into. Same scope shape
as P5-T1 (`McpServerConfig`) + P5c-T1 (`Skill`).

**Acceptance**:
- [ ] `plugins/model.py` — `PluginManifest` frozen dataclass:
  - Required: `name: str` (regex `^[a-z][a-z0-9-]*$`, lowercase + hyphens
    for kebab-case namespace prefix), `version: str` (semver),
    `description: str`
  - Optional metadata: `author / license / homepage / keywords[] /
    openharness_version_min / dependencies[]`
  - 5 component lists, each optional:
    - `commands: list[ComponentRef]` (each `{file: relpath}`)
    - `skills: list[ComponentRef]`
    - `bundles: list[ComponentRef]`
    - `hooks: list[HookRef]` (each `{module: str, name: str}`)
    - `mcp_servers: list[McpServerConfig]` (reusing existing dataclass
      from `mcp/config.py` — zero-change reuse)
  - `source_path: Path` — directory containing this manifest
- [ ] `plugins/model.py` — `parse_manifest(path: Path) -> PluginManifest | None`:
  - YAML parse via existing `pyyaml` dep (already in Phase 5c)
  - Required-field validation (name / version / description present)
  - Name regex validation (kebab-case)
  - `${VAR}` env-var interpolation in `mcp_servers[].env[]` values
  - **Fault-tolerant**: malformed YAML / missing required / invalid name
    → returns `None` + emits warning log (same shape as
    `parse_skill` / `parse_command` from Phase 5b/5c)
- [ ] `plugins/errors.py`:
  - `PluginManifestError(OpenHarnessError)` — malformed manifest
  - `PluginConflictError(OpenHarnessError)` — two plugins with same name
  - `PluginInstallError(OpenHarnessError)` — install/remove operation failed
- [ ] `plugins/__init__.py` — re-exports
- [ ] Tests (`tests/plugins/test_model.py`, ~15 cases):
  - Happy path: minimal manifest (only required fields)
  - Happy path: full manifest with all 5 component types
  - Required-field missing → None + warning
  - Invalid name regex → None + warning
  - Malformed YAML → None + warning
  - Env-var interpolation: `${EXISTING}` resolves
  - Env-var interpolation: `${MISSING}` → warning + plugin skipped
  - Unknown top-level fields → ignored (forward compat)

**Files**:
- `src/openharness/plugins/__init__.py` (new)
- `src/openharness/plugins/model.py` (new, ~150 lines)
- `src/openharness/plugins/errors.py` (new, ~25 lines)
- `tests/plugins/__init__.py`, `tests/plugins/test_model.py` (new)

**Sub-units**:
- 1a — `PluginManifest` dataclass + `ComponentRef` + `HookRef` + tests
- 1b — `parse_manifest` YAML parse + validation + fault-tolerance + tests
- 1c — Env-var interpolation in mcp_servers + tests

---

### P9-T2: `PluginLoader` — discover + fan out

**Description**: The core capability. `PluginLoader` discovers all
plugins under `~/.openharness/plugins/`, parses each manifest, and
fans out registrations into the 5 existing subsystem stores. Namespace
prefix applied at this layer; conflict detection here.

**Acceptance**:
- [ ] `plugins/loader.py` — `PluginLoader`:
  - `__init__(plugins_dir: Path)` — single dir (~/.openharness/plugins/)
  - `discover() -> dict[str, PluginManifest]` — scans `<plugins_dir>/*/manifest.yaml`;
    returns `{plugin_name: manifest}` map; raises `PluginConflictError`
    on duplicate plugin names with both manifest paths in the error
  - `fan_out(manifests, command_store, skill_store, bundle_store,
    hook_registry, plugin_hook_catalog, mcp_servers_accumulator)`:
    - For each plugin's `commands[]`: load via existing `parse_command()`
      from `commands/model.py`, apply namespace prefix
      (`<plugin>:<original>`), register into `command_store`
    - For each `skills[]`: same pattern via `parse_skill()`,
      prefix into `skill_store`
    - For each `bundles[]`: same pattern via `parse_bundle()`,
      prefix into `bundle_store`
    - For each `hooks[]`: `_load_module_from_path()` (reused from P5f)
      with module path `<plugin_dir>/<module>.py`, namespaced as
      `<plugin>:<name>` in `plugin_hook_catalog`
    - For each `mcp_servers[]`: namespaced as `McpServerConfig(name=f"{plugin}:{server_name}", ...)`,
      appended to `mcp_servers_accumulator`
- [ ] Failure isolation: one bad plugin (malformed manifest / missing
  component file / hook module load error) → warning log + that plugin
  skipped, others continue
- [ ] Same-named component WITHIN one plugin (`commands[]` has two
  `deploy.md`) → `PluginManifestError` at load (bug in the plugin)
- [ ] Tests (`tests/plugins/test_loader.py`, ~12 cases):
  - Discover empty dir → empty catalog
  - Single happy-path plugin → all 5 component types correctly namespaced
  - Two plugins with different names → both load, namespaces distinct
  - Two plugins same name → `PluginConflictError` listing both paths
  - One good + one bad plugin (malformed YAML) → good loads, bad skipped + warning
  - Hook module import fails → warning, other components still load
  - MCP server with `${MISSING_VAR}` → that server skipped, other components load

**Files**:
- `src/openharness/plugins/loader.py` (new, ~120 lines)
- `tests/plugins/test_loader.py` (new)

**Sub-units**:
- 2a — `discover()` + conflict detection + tests
- 2b — `fan_out()` for commands + skills + bundles (markdown components) + tests
- 2c — `fan_out()` for hooks (module load) + mcp_servers (namespaced) + tests

---

### P9-T3: Settings + CLI bootstrap integration

**Description**: Wire `PluginLoader` into `cli._run_ask` (and
`cli._run_chat`) after the existing 5-subsystem bootstrap. Opt-in via
`Settings.enable_plugins`. **This is the invariant verification point**.

**Acceptance**:
- [ ] `Settings.enable_plugins: bool = False` field added to
  `src/openharness/config/settings.py`
  - Env var: `OPENHARNESS_ENABLE_PLUGINS=true`
  - CLI flag: `--enable-plugins / --no-enable-plugins`
- [ ] CLI bootstrap in `_run_ask` (and parallel in `_run_chat`):
  - After existing command_store / skill_store / bundle_store /
    plugin_hook_catalog / mcp_servers construction
  - If `enable_plugins` resolved True:
    - `loader = PluginLoader(Path.home() / ".openharness" / "plugins")`
    - `manifests = loader.discover()`
    - `loader.fan_out(...)` injects namespaced components into the
      existing stores
  - Bootstrap log emits `plugins_loaded` event with count + names
- [ ] `--enable-plugins` flag added to both `oh ask` and `oh chat`
- [ ] Tests (`tests/cli/test_plugins_bootstrap.py`, ~6 cases):
  - `enable_plugins=False` → loader NOT called, command store empty of
    plugin components
  - `enable_plugins=True` + 1 plugin → command/skill/bundle/hook/mcp
    all visible in QueryContext
  - `--enable-plugins` CLI flag overrides Settings False
  - `OPENHARNESS_ENABLE_PLUGINS=true` env overrides Settings False
  - Bad plugin doesn't kill bootstrap; good plugin still works
  - `plugins_loaded` log event observable

**Critical invariant verification (D27 cross-cutting)** ⭐:
- [ ] `git diff <P8 close> -- src/openharness/mcp/` empty
- [ ] `git diff <P8 close> -- src/openharness/skills/` empty
- [ ] `git diff <P8 close> -- src/openharness/commands/` empty
- [ ] `git diff <P8 close> -- src/openharness/bundles/` empty
- [ ] `git diff <P8 close> -- src/openharness/hooks/` empty
- [ ] `git diff <P8 close> -- src/openharness/engine/query.py` empty
  (dispatch logic unchanged from P8 close)
- [ ] `git diff <P8 close> -- src/openharness/permissions/checker.py` empty
- [ ] `git diff <P8 close> -- src/openharness/protocols/` empty
- [ ] `git diff <P8 close> -- src/openharness/compaction/` empty
- [ ] `git diff <P8 close> -- src/openharness/cli.py` shows ONLY:
  - `PluginLoader` import (1-2 lines)
  - `--enable-plugins` flag declarations
  - Bootstrap-step block (~10-15 lines)
  - `oh plugins` subcommand series (T4)
- [ ] `git diff <P8 close> -- src/openharness/config/settings.py` shows
  only `enable_plugins` field + validator

**Files**:
- `src/openharness/config/settings.py` (+`enable_plugins` field)
- `src/openharness/cli.py` (+`PluginLoader` import + bootstrap +
  `--enable-plugins` flag on both ask + chat)
- `tests/cli/test_plugins_bootstrap.py` (new)

**Sub-units**:
- 3a — `Settings.enable_plugins` + env/CLI override + tests
- 3b — `cli._run_ask` bootstrap integration + tests
- 3c — `cli._run_chat` parallel integration + tests
- 3d — **Invariant verification commit message**: explicitly list all
  zero-diff dirs verified, append to commit body

---

### P9-T4: CLI subcommands — `oh plugins list / show / install / remove`

**Description**: User-facing CLI surface for plugin management. All
read-only ops (`list` / `show`) work without `--enable-plugins`; mutating
ops (`install` / `remove`) operate on filesystem regardless of flag.

**Acceptance**:
- [ ] `oh plugins list` (text format default, `--format json`):
  - Scans `~/.openharness/plugins/`, parses each manifest
  - Output: `name / version / description / status` (status = `loadable`
    or `(invalid: <reason>)`)
  - Sorted by name
- [ ] `oh plugins show <name>` (text + JSON):
  - Full manifest details:metadata fields + counts of each
    component type + resolved component file paths
  - Validates `<name>` exists; "unknown plugin" error with available
    list otherwise (same UX as `oh hooks describe` unknown name)
- [ ] `oh plugins install <source>`:
  - `<source>` = local path (`./my-plugin/`) OR git URL
    (`https://github.com/foo/bar.git` or `git@github.com:foo/bar.git`)
  - Local path: `shutil.copytree(<source>, ~/.openharness/plugins/<basename>/)`
  - Git URL: `subprocess.run(["git", "clone", url, target_dir])`
  - Post-install: parse manifest;invalid → remove dir + error
  - `--enable` flag: also flips `enable_plugins=True` in
    `~/.openharness/.env` (creates if absent, otherwise appends/updates)
- [ ] `oh plugins remove <name>`:
  - Confirmation prompt `Remove plugin <name>? [y/N]` unless `--force`
  - `shutil.rmtree(~/.openharness/plugins/<name>/)`
  - "Unknown plugin" error with available list if name not found
- [ ] Tests (`tests/cli/test_plugins_cli.py`, ~10 cases):
  - `list` with 0 plugins → "(no plugins installed)" output
  - `list` with 2 plugins → both listed + sorted
  - `list --format json` → valid JSON
  - `show <name>` → full manifest fields visible
  - `show <unknown>` → exit 1 + available list
  - `install <local-path>` → copies, validates, success
  - `install <local-path-with-invalid-manifest>` → installs nothing,
    error + cleanup
  - `install <git-url>` → mocked git clone, success path
  - `remove <name>` with stdin "y\n" → directory gone
  - `remove <unknown>` → exit 1 + available list

**Files**:
- `src/openharness/cli.py` (+`plugins_app` Typer subapp with 4 commands)
- `tests/cli/test_plugins_cli.py` (new)

**Sub-units**:
- 4a — `list` + `show` (read-only) + tests
- 4b — `install` (local path + git URL) + tests
- 4c — `remove` with confirmation + tests

---

### P9-T5: End-to-end smoke + invariant verification + retro

**Description**: Build a real plugin under `~/.openharness/plugins/`
declaring 1 of each component type;`oh ask` with `--enable-plugins`
sees them all;cross-cutting invariant explicitly verified via git
diff;retro written.

**Acceptance**:
- [ ] `examples/plugins/hello-world/` example plugin in repo:
  - `manifest.yaml` declaring 1 command + 1 skill + 1 bundle + 1 hook
    + 1 MCP server (filesystem stub OK)
  - Files for each component
- [ ] Integration test (`tests/plugins/test_e2e.py`):
  - Install via `oh plugins install ./examples/plugins/hello-world/`
  - `oh plugins list` shows it as loadable
  - `oh plugins show hello-world` prints all 5 component types
  - `OPENHARNESS_ENABLE_PLUGINS=true oh ask --dry-run "use /hello-world:greet"`
    shows the command resolved with plugin's prompt
  - Skill `hello-world:my-knowledge` appears in tool catalog
  - Hook `hello-world:audit` fires on PostToolUse
- [ ] **Cross-cutting invariant `git diff <P8 close>` check**:
  - `mcp/` zero diff ✅
  - `skills/` zero diff ✅
  - `commands/` zero diff ✅
  - `bundles/` zero diff ✅
  - `hooks/` zero diff ✅
  - `engine/query.py` zero diff ✅
  - `permissions/` zero diff ✅
  - `protocols/` zero diff ✅
  - `compaction/` zero diff ✅
  - `cli.py` shows additive only (no isinstance / no Plugin-aware branching)
  - `config/settings.py` shows only `enable_plugins` field addition
- [ ] `learnings/phase-9.md` retro:
  - 1. Data points table (commits / tests / coverage / LoC)
  - 2. Per-task takeaway (T1-T5 one-liners)
  - 3. ⭐ **Invariant verification result** — fourth compounding test
    of Phase 3 abstraction;5 extension subsystems all zero-diff;
    `cli.py` minimal additive
  - 4. Conceptual lesson: distribution layer ≠ runtime layer;
    abstraction-first compounding now extends to packaging
  - 5. Real踩坑 (predict 1-2:env-var interpolation timing issues /
    git-clone failure modes / namespace prefix on bundle hook_names)
  - 6. Phase 10+ predictions (Python entry-point distribution /
    marketplace / versioning resolver)
- [ ] Coverage ≥ 95% retained
- [ ] CI green on Python 3.10 + 3.11
- [ ] mypy strict + ruff clean
- [ ] DoD checklist all green (decisions/24 §Acceptance)

**Files**:
- `examples/plugins/hello-world/manifest.yaml` (new + component files)
- `tests/plugins/test_e2e.py` (new)
- `learnings/phase-9.md` (new)

**Sub-units**:
- 5a — Example plugin under `examples/`
- 5b — E2E integration test
- 5c — Invariant git-diff check + commit message documentation
- 5d — `learnings/phase-9.md` retro
- 5e — DoD closeout + PLAYBOOK / README updates

---

## Checkpoints

After each capability: **human review** of the resulting trace + the
"zero change" invariant. T3 commit is the critical invariant
verification — if any of the 9 protected dirs / files shows unexpected
diff, **stop and re-open the boundary doc**. That's the fourth
independent test of Phase 3 / 5 / 7 abstractions failing, not a
Phase 9 implementation detail.

## Risks

| Risk | Mitigation |
|---|---|
| Module load via `_load_module_from_path` interferes with normal package imports inside plugin | Reuse P5f's sha8-prefixed module-name pattern; tests verify isolation |
| `git clone` subprocess fails in CI environment | T4 tests use mocked subprocess for git URL; real git clone only in opt-in integration test (skipif no network) |
| Manifest YAML schema accepts unknown fields silently → typos hide | Warning log for unknown fields (forward-compat without silence) |
| Plugin's MCP server config has bad command → bootstrap crashes | `McpClientPool` already handles per-server init failure (Phase 5 D15.4); plugin's MCP failure stays isolated |
| Two plugins legitimately want same name (`coding-tools` for two different orgs) | Bootstrap hard-error documented + manifest path printed; user manually rename one |
| Namespace prefix breaks user expectation of unprefixed access | `oh ask "/coding-tools:deploy"` is correct invocation — same model as MCP `Filesystem.ReadFile`. Document clearly in PLAYBOOK |

## Risks specifically NOT mitigated (Phase 10+)

- Python entry-point distribution (`pip install openharness-plugin-foo`)
- Plugin marketplace / centralized registry
- Plugin version compatibility resolution
- Plugin signing / verification

## Pointers

- Boundary: [`decisions/24-phase-9-boundary.md`](../decisions/24-phase-9-boundary.md)
- Phase 5e + 5f boundaries (plugin hook discovery — pattern Phase 9
  reuses):[`decisions/18`](../decisions/18-phase-5e-boundary.md) + [`20`](../decisions/20-phase-5f-boundary.md)
- Phase 5d boundary (cross-layer composition — pattern Plugin generalizes):
  [`decisions/17-phase-5d-boundary.md`](../decisions/17-phase-5d-boundary.md)
- Phase 8 boundary (`markdown_store` shared primitives — Phase 9 reuses):
  [`decisions/19-phase-8-boundary.md`](../decisions/19-phase-8-boundary.md)
- Meta-retro §3.1 — abstraction-first compounding evidence:
  [`learnings/phase-7.md`](../learnings/phase-7.md)
- Upstream OpenHarness reference: [`REFERENCE.md`](../REFERENCE.md) §17
- Claude Code plugin docs: <https://docs.claude.com/docs/en/plugins.md>
