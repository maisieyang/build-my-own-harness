# Phase 9 Boundary — Plugin System (Unified Extension Distribution)

> Status: locked at Phase 9 entry, 2026-05-22.
>
> Scope note: this phase introduces a **new distribution layer** that
> unifies the 5 existing extension mechanisms (MCP / Skills / Commands /
> Bundles / Plugin Hooks) under a single ``PluginManifest`` declaration.
> **The 5 mechanisms themselves are not changed** — Plugin is a
> multiplexed installer that fans out to the existing registration
> paths at bootstrap. Same "abstraction-first over existing capability"
> shape as Phase 7a (substrate Protocol + HostExecution identity
> transform).
>
> Related work references:
> - **Upstream HKUDS/OpenHarness §17** has a ``plugins/`` subsystem
>   with ``PluginManifest`` / ``LoadedPlugin`` / ``PluginLoader``
>   (compat with Claude Code plugins).
> - **Claude Code's plugin system** uses ``.claude-plugin/plugin.json``
>   manifest + filesystem directories (commands/skills/agents/hooks)
>   + ``/plugin marketplace add`` for git-based distribution. See
>   `docs.claude.com/docs/en/plugins.md`.
> - This phase deliberately **does not** copy upstream's API surface
>   (which is broader). Phase 9 ships a minimal 5-mechanism
>   multiplexer matching the OpenHarness-from-scratch's actual subsystem
>   set.

## Triggering observation

After Phase 5/5b/5c/5d/5e/5f shipped 5 independent extension mechanisms,
a real-world user installing a new capability needs to:

1. Drop a markdown file into `~/.openharness/commands/`
2. Drop a markdown file into `~/.openharness/skills/`
3. Drop a markdown file into `~/.openharness/bundles/`
4. Drop a `.py` file into `~/.openharness/hooks/`
5. Edit `OPENHARNESS_MCP_SERVERS` env var

That's 5 separate user actions for one logical "capability". The 5 mechanisms
are independent extension axes — perfect for **framework-level
composability** — but **bad for end-user distribution**.

Plugin closes that gap: **one manifest declares all 5, harness installs
as a unit**. This is the third compounding test of Phase 3's "uniform
extension surface" abstraction — if Plugin lands without changing any
of the 5 underlying mechanisms' source, the abstraction holds.

---

## In scope

**D27.1 — `PluginManifest` schema: YAML, complete metadata + 5 component
declarations.**

```yaml
# ~/.openharness/plugins/<name>/manifest.yaml
name: my-plugin                              # unique, used as namespace prefix
version: 0.1.0                               # semver, required
description: Single-line description shown in `oh plugins list`

# Optional metadata
author: maisieyang
license: MIT
homepage: https://github.com/maisieyang/my-plugin
keywords: ["coding", "review"]
openharness_version_min: "0.1.0"             # Compatibility hint (warning only in Phase 9)
dependencies:                                # Optional, future-resolver (warning only in Phase 9)
  - other-plugin: ">=1.0"

# 5 component types — all optional
commands:                                    # Phase 5b
  - file: commands/my-cmd.md
  - file: commands/another.md

skills:                                      # Phase 5c
  - file: skills/my-skill.md

bundles:                                     # Phase 5d
  - file: bundles/my-mode.md

hooks:                                       # Phase 5e/5f
  - module: my_plugin.hooks                  # filesystem-path-relative or installable module
    name: my_hook_function

mcp_servers:                                 # Phase 5
  - name: GitHub
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
```

Required fields: `name`, `version`, `description`. Everything else
optional. Unknown top-level fields → warning + ignored (forward-compat).

**D27.2 — Distribution: filesystem only.**

Phase 9 supports a single distribution mechanism: directory-based
installation at `~/.openharness/plugins/<plugin-name>/`. User installs
by:
- Manually copying / git-cloning into that directory, **or**
- `oh plugins install <git-url>` (which is just a wrapper around `git clone`)

**Out of scope for Phase 9**:
- Python entry-point distribution (`openharness.plugins` entry-point
  group + `pip install`)
- Plugin marketplace / registry server
- Versioning conflict resolution between installed plugins

Rationale: same UX as Skills / Commands / Bundles which already live
in `~/.openharness/` filesystem directories. User mental model stays
consistent across all subsystems. Python pip distribution is a
language-specific affordance defers to Phase 10 if real PyPI demand
materializes.

**D27.3 — Namespace conflict: plugin-name prefix.**

> **Updated by D27.7**: the actual separator is `__`, not `:`. The
> `:` examples below preserve the original design intent — see D27.7
> for why the build resolved the colon to a double underscore.

When two plugins both declare a command called `/deploy`:

- Plugin `acme-tools` declares `/deploy` → exposed as `/acme-tools:deploy`
- Plugin `xyz` declares `/deploy` → exposed as `/xyz:deploy`

Same shape as MCP's `Server.Tool` naming (D15.3). All 5 component types
follow the same rule:

| Component | Without plugin | With plugin (`my-plugin`) |
|---|---|---|
| Command | `/deploy` | `/my-plugin:deploy` |
| Skill | `react-testing` | `my-plugin:react-testing` |
| Bundle | `coding-mode` | `my-plugin:coding-mode` |
| Hook name | `audit_log` | `my-plugin:audit_log` |
| MCP server | `GitHub` | `my-plugin:GitHub` |

User invoking from CLI: `oh ask "/my-plugin:deploy"`.

Conflict detection: if **two plugins claim the same name** (not the same
component, the plugin itself), bootstrap fails with explicit error
listing both manifest paths.

**D27.4 — Trust model: opt-in, same pattern as Plugin Hooks (P5e).**

- `Settings.enable_plugins: bool = False` (default off)
- Env var: `OPENHARNESS_ENABLE_PLUGINS=true`
- CLI flag: `--enable-plugins`
- Rationale: plugins ship arbitrary Python (via the `hooks:` declarations).
  Same trust concern as filesystem hook plugins from Phase 5f. Default
  off, explicit opt-in.

When `enable_plugins=False`:
- `oh plugins list` still works (read-only introspection of what's available)
- Plugin components do NOT get loaded into the registry at `oh ask` time

When `enable_plugins=True`:
- Plugin components ARE loaded
- Bootstrap log emits `plugins_loaded` event with count + names + manifests

**D27.5 — MCP server in manifest: inline complete config.**

The `mcp_servers:` section in manifest carries the full McpServerConfig:

```yaml
mcp_servers:
  - name: GitHub
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}   # Env-var interpolation supported
```

At bootstrap:
- Plugin's mcp_servers get **merged** with `Settings.mcp_servers` (from env var)
- Namespace prefix applied: plugin's `GitHub` becomes `my-plugin:GitHub`
- Trust whitelist (`Settings.trusted_mcp_servers`) operates on the namespaced
  name (`my-plugin:GitHub`)

Env-var interpolation (`${GITHUB_TOKEN}`) resolves at manifest-load time.
Missing env vars → warning + plugin skipped (graceful degradation).

**D27.6 — CLI command surface: list / show / install / remove.**

```bash
oh plugins list                              # Built-in: name / version / status / source
oh plugins list --format json                # Machine-readable
oh plugins show <name>                       # Manifest details + loaded components
oh plugins install <source>                  # Source = local path OR git URL
oh plugins install <source> --enable         # Install + enable (single step)
oh plugins remove <name>                     # Uninstall (rm -rf the directory)
oh plugins remove <name> --force             # No confirmation prompt
```

Implementation notes:
- `install <local-path>` = recursive copy to `~/.openharness/plugins/<name>/`
- `install <git-url>` = `git clone <url> ~/.openharness/plugins/<name>/`
  (handles `https://...` and `git@github.com:...` forms)
- `install` validates manifest exists + parses cleanly before completing
- `remove` confirms with `[y/N]` prompt unless `--force`

**Out of scope for CLI**:
- `oh plugins update <name>` (re-clone or pull) — Phase 10
- `oh plugins enable <name>` / `disable <name>` granular toggle —
  for Phase 9 it's all-or-nothing via `--enable-plugins` flag

**D27.7 — Namespace separator is `__`, not `:` (supersedes D27.3 example notation).**

> Added 2026-05-26 during P9-T3 review. The boundary doc originally
> displayed `<plugin>:<component>` in D27.3 examples. Build-time
> discovery forced this resolution; recording it here keeps the
> contract honest.

**The constraint**. The 5 underlying subsystems' name regexes all
match `^[A-Za-z][A-Za-z0-9_-]*$`:

- `mcp/config.py` — MCP server name pattern
- `markdown_store/constants.py` `NAME_PATTERN` — shared by commands /
  skills / bundles
- `bundles/hook_plugins.py` — hook name pattern

`:` is not in the allowed character set. D27.3's `my-plugin:deploy`
notation would have required either changing those 5 regexes
(violating the zero-diff invariant Phase 9 was designed to test) or
adding a translation layer.

**Why translation was also rejected**. A `<plugin>:<component>` ↔
`<plugin>__<component>` translation would have to live in *every*
LLM-facing renderer:

- `build_system_prompt`'s skill catalog rendering
- tool catalog rendering for the LLM
- `LoadSkillTool` input parsing (the LLM emits the displayed form)
- `oh plugins list` / `show` rendering
- the slash-command parser in `commands/expand.py`

Each of those renderers becoming plugin-aware propagates plugin
semantics into engine + skills + commands code paths — directly
violating the same zero-diff invariant the regex-widening option
violates. The translation layer pushes the problem deeper instead of
solving it.

**Chosen contract**. `__` (double underscore) is the actual separator
end-to-end:

| Layer | Form |
|---|---|
| `manifest.yaml` declaration | Original unprefixed (`name: deploy`) |
| Storage (command_store / skill_store / bundle_store) | `my-plugin__deploy` |
| Plugin hook catalog key | `my-plugin__audit_log` |
| MCP server name in pool | `my-plugin__GitHub` |
| LLM-facing system prompt catalog | `my-plugin__deploy` |
| User CLI invocation | `oh ask "/my-plugin__deploy"` |
| `oh plugins show` / `list` display | `my-plugin__deploy` (no cosmetic translation) |

`__` was chosen over `-` or `_` because:

- `-` is already a valid character inside both plugin names
  (kebab-case per D27.1) and component names, so `my-plugin-deploy`
  is ambiguous about where the boundary sits.
- Single `_` collides with snake_case component names (`audit_log` →
  `my-plugin_audit_log` is ambiguous).
- `__` is unambiguous AND satisfies all 5 regexes without
  modification (any string of `[A-Za-z0-9_-]` matches).

**Aesthetic cost is accepted**. `__` reads like a Python internal
marker, which is awkward for a public-facing namespace. We absorb
that cost because the alternative — translation layer — costs us
the cross-cutting invariant, which is Phase 9's whole point. PLAYBOOK
and README document `__` as the user-facing form; D27.3's `:`
notation is preserved as design intent we couldn't deliver, not as
the actual contract.

**Conflict detection rule (unchanged from D27.3)**. Bootstrap still
hard-fails if two plugins claim the same `name` — that rule operates
on the plugin name itself (kebab-case, before any separator), not on
the resulting namespaced component keys.

---

## Cross-cutting invariant

**Phase 9 Plugin must not add a new dispatch path.** The following
layers stay **zero diff** in `src/openharness/`:

- `mcp/` package — Plugin's mcp_servers register via existing `McpClientPool`
- `skills/` package — Plugin's skills register via existing `FilesystemSkillStore`
- `commands/` package — Plugin's commands register via existing `FilesystemCommandStore`
- `bundles/` package (including `bundles/hook_plugins.py`) — Plugin's
  bundles + hooks register via existing pathways
- `engine/query.py` dispatch loop — no Plugin-aware branching
- `permissions/checker.py` — no Plugin-aware checks
- `hooks/executor.py` — Plugin-loaded hooks fire identically to manually-installed hooks
- `protocols/` — no new event types
- `compaction/` — zero change

Where change IS allowed (all additive):
- `src/openharness/plugins/` (new package) — `PluginManifest` parse +
  `PluginLoader` discover + fan out
- `src/openharness/config/settings.py` — +`Settings.enable_plugins: bool = False`
- `src/openharness/cli.py` — bootstrap step: if enabled, load plugins
  → fan out to existing registration paths; +`oh plugins` subcommand series

If during build any "zero diff" layer needs editing, **stop and re-open
the boundary doc**. That's the fourth independent test of Phase 3's
abstraction failing, not a Phase 9 implementation detail.

---

## Out of scope (Phase 10+)

- **Python entry-point distribution** (`[project.entry-points]
  "openharness.plugins"` + `pip install openharness-plugin-foo`)
- **Plugin marketplace / registry server** (centralized discovery like
  Claude Code's `/plugin marketplace add`)
- **Version conflict resolution** between plugins requiring different
  versions of same dependency
- **`oh plugins update` / `enable` / `disable`** granular commands
- **Plugin lifecycle hooks** (`on_install` / `on_uninstall` callbacks)
- **Plugin isolation** (each plugin in its own subprocess / sandbox) —
  Phase 9 plugins run in-process, trust model is opt-in only
- **Plugin signing / verification** (signed manifests, GPG signatures)

---

## Critical decisions (D27.x)

| ID | Decision | Why |
|---|---|---|
| **D27.1** | YAML manifest with required `name/version/description` + 5 optional component declarations | Match Phase 5c/5b/5d existing markdown-frontmatter conventions; YAML is consistent across the file ecosystem; required fields force minimal accountability |
| **D27.2** | Filesystem-only distribution; git-clone via CLI is sugar | User mental model consistent with Skills/Commands/Bundles; Python pip-installable + marketplace defer to Phase 10 |
| **D27.3** | Plugin-name prefix namespacing | Same pattern as MCP `Server.Tool` (D15.3); user sees consistent naming across subsystems |
| **D27.4** | Opt-in via `Settings.enable_plugins` | Same shape as `enable_plugin_hooks` (P5e); arbitrary-code-execution warrants explicit opt-in |
| **D27.5** | Inline complete MCP server config in manifest | User installs one plugin → gets full MCP server stack; env-var interpolation for secrets; trust whitelist works on namespaced name |
| **D27.6** | CLI: list / show / install / remove | install accepts local path OR git URL; remove with confirmation prompt; update/enable/disable defer |
| **D27.7** | Namespace separator is `__` end-to-end (storage + LLM + user) — supersedes D27.3 example `:` notation | 5 subsystem regexes reject `:`; a translation layer would have propagated plugin-awareness into LLM-facing renderers, breaking the cross-cutting zero-diff invariant Phase 9 was specifically designed to test |

---

## Dependency direction

```
plugins/                              (new package)
   ├── model.py                       ← PluginManifest dataclass + YAML parse
   ├── loader.py                      ← PluginLoader: discover + fan out to 5 registration paths
   └── errors.py                      ← PluginManifestError / PluginInstallError / PluginConflictError

config/settings.py                    ← +enable_plugins: bool = False
cli.py                                ← bootstrap: if enabled, load plugins
                                        +oh plugins list/show/install/remove
markdown_store/                       ← UNCHANGED (frontmatter parser reused)

mcp/                                  ← ZERO CHANGE (invariant)
skills/                               ← ZERO CHANGE
commands/                             ← ZERO CHANGE
bundles/                              ← ZERO CHANGE
hooks/                                ← ZERO CHANGE
engine/                               ← ZERO CHANGE
permissions/                          ← ZERO CHANGE
protocols/                            ← ZERO CHANGE
compaction/                           ← ZERO CHANGE
```

`plugins/` is downstream of all 5 extension subsystems (it consumes
their registration APIs) and upstream of `cli.py` (bootstrap installs
plugins before constructing QueryContext). The 9 "zero change" layers
are the contract this phase verifies.

---

## Sub-decisions deferred to build

Three open questions resolved tentatively now, locked at build time:

- **Plugin hook module loading strategy** — manifest's `hooks:` declares
  `module: my_plugin.hooks` + `name: my_hook_function`. Should the
  loader use `importlib.import_module()` with `~/.openharness/plugins/<name>/`
  added to `sys.path`? Or use the same `_load_module_from_path()` trick
  as `discover_filesystem_hook_plugins` (P5f)? Tentative: **reuse P5f's
  `_load_module_from_path` mechanism** — same sha8-prefixed module name
  pattern, same safety. Revisit if real plugins surface need for
  relative imports.
- **`oh plugins install <git-url>` SSH vs HTTPS authentication** —
  Phase 9 ships HTTPS-only. SSH keys defer to Phase 10. User can
  manually clone if they need SSH.
- **Manifest schema versioning** — Phase 9 ships unversioned manifest
  (no `manifest_version` field). If field set expands in Phase 10,
  unknown-fields-tolerant policy (D27.1) handles forward compat. If
  a breaking schema change comes, add `manifest_version` then.

---

## Acceptance for Phase 9 close-out (template)

- [ ] `~/.openharness/plugins/example-plugin/manifest.yaml` declared
  with all 5 component types + correct YAML schema
- [ ] `oh plugins list` shows the example plugin + its components
- [ ] `oh plugins show example-plugin` prints manifest details +
  resolved component paths
- [ ] `oh plugins install <local-path>` copies plugin into
  `~/.openharness/plugins/`, validates manifest, returns success
- [ ] `oh plugins install <git-url>` clones repo into
  `~/.openharness/plugins/`, validates manifest
- [ ] `oh plugins remove <name>` with `[y/N]` prompt, removes directory
- [ ] `OPENHARNESS_ENABLE_PLUGINS=true oh ask "..."` loads plugin
  components into the running registry (verifiable via
  `oh ask --dry-run`)
- [ ] Plugin's command `/example:hello` invokable from `oh ask` /
  `oh chat`
- [ ] Plugin's skill `example:my-skill` discoverable by LLM via
  catalog
- [ ] Plugin's MCP server `example:GitHub` namespaced correctly +
  trust-whitelist-aware
- [ ] Plugin's hook `example:audit` fires on the right event
- [ ] Two plugins with same `name` → bootstrap hard error pointing at
  both manifest paths
- [ ] Plugin with malformed YAML / missing required fields →
  `oh plugins list` shows as `(invalid)` + warning log; bootstrap
  with `enable_plugins=True` skips bad plugin (doesn't crash)
- [ ] `git diff <P8 close> -- src/openharness/{mcp,skills,commands,bundles,hooks,engine,permissions,protocols,compaction}/`
  shows **zero diff** — the invariant
- [ ] `git diff <P8 close> -- src/openharness/cli.py` shows only:
  - plugins bootstrap call (3-5 lines)
  - `oh plugins` subcommand series (additive Typer app)
- [ ] mypy strict + ruff check + ruff format clean
- [ ] Coverage ≥ 95% retained (gate)
- [ ] CI green on Python 3.10 + 3.11

---

## Pointers

- Phase 5 boundary (MCP — D15.6 trust whitelist pattern Phase 9 reuses):
  [`decisions/11-phase-5-boundary.md`](./11-phase-5-boundary.md)
- Phase 5d boundary (ModeBundle — cross-layer composition that Plugin
  generalizes): [`decisions/17-phase-5d-boundary.md`](./17-phase-5d-boundary.md)
- Phase 5e boundary (Plugin hook discovery — trust opt-in pattern Phase 9
  reuses): [`decisions/18-phase-5e-boundary.md`](./18-phase-5e-boundary.md)
- Phase 5f boundary (Filesystem hook plugins — module-load-from-path
  pattern Phase 9 reuses): [`decisions/20-phase-5f-boundary.md`](./20-phase-5f-boundary.md)
- Phase 8 boundary (markdown_store refactor — shared primitives Phase 9
  reuses): [`decisions/19-phase-8-boundary.md`](./19-phase-8-boundary.md)
- Phase 7a/7b/7c (abstraction-first-over-existing-capability template):
  [`decisions/15`](./15-phase-7-boundary.md) + [`16`](./16-phase-7b-boundary.md) + [`21`](./21-phase-7c-boundary.md)
- Meta-retro §3.1 — abstraction-first compounding evidence:
  [`learnings/phase-7.md`](../learnings/phase-7.md)
- Upstream OpenHarness §17 plugin reference (independent reimplementation,
  no code copy): [`REFERENCE.md`](../REFERENCE.md) §17
- Claude Code plugin docs (related work, not API source):
  <https://docs.claude.com/docs/en/plugins.md>
