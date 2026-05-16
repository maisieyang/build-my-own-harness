# Phase 5d Boundary — ModeBundle (Cross-Layer Composition)

> Status: locked at Phase 5d entry, 2026-05-16.
>
> Scope note:**this boundary covers ModeBundle as a 4-layer composition
> primitive**. It's the FIRST cross-layer tenant in the harness — all
> prior tenants (5a/5b/5c/6/7a/7b) touched only 1-2 layers. ModeBundle
> composes Layer 0 (slash command trigger) + Layer 1 (system prompt)
> + Layer 2 (tool catalog) + Layer 3 (hooks + permission overlay).
>
> Rationale + framing:Phase 7a retro § "stable plateau" + Phase 5b
> retro §3.1 "layered extension model". ModeBundle is the **first
> phase that explicitly composes layers** rather than adding a new
> single-layer concept. The compositional discipline:**bundle is a
> QueryContext factory** — it reads metadata and assembles existing
> primitives (system_prompt / wrapped ToolRegistry / hook_registry /
> deny_paths) into a modified QueryContext. Engine + each individual
> layer **stay unchanged**.

## Triggering observation

Phase 5b (slash commands) established Layer 0. Phase 5c (skills)
established Layer 1+2. Phase 6 (sub-agent) showed Layer 2 composition
within `BaseTool.execute`. Phase 7a/7b showed Layer 2 (substrate) as
swappable plug-in.

Every prior tenant landed cleanly because **each layer's contract
stayed minimal** — engine didn't grow new fields, permissions/hooks
didn't grow new dispatch paths. ModeBundle is the load-bearing test
of whether those minimal contracts compose:**can the harness offer
a user-facing "switch mode" feature without inventing a new dispatch
path?**

Yes,if ModeBundle is implemented as a **pre-LLM resolution + factory
pattern**:

1. Slash command (Layer 0) triggers bundle resolution at CLI bootstrap
2. Bundle metadata is parsed (system_prompt / tool_filter / deny_paths
   / hook_names)
3. CLI constructs a modified `QueryContext` using existing primitives:
   - `system_prompt` field set from bundle
   - `tool_registry` wrapped with a filter (existing ToolRegistry
     unchanged)
   - `permission_checker` constructed with bundle's `deny_paths`
     merged into `Settings.deny_paths` (TierBasedPermissionChecker
     unchanged)
   - `hook_registry` registers the bundle's named hooks (registry
     unchanged)
4. Engine runs `run_query(messages, context)` exactly as before

**Engine + permissions/checker + hooks/executor + observability /
mcp / compaction / skills / commands / protocols / tools — ZERO
diff**. The composition lives entirely in `bundles/` + `cli.py`.

---

## In scope

**D19.1 — ModeBundle is a 4-layer composition primitive.**

A ModeBundle is a markdown file with YAML frontmatter that declares:

```markdown
---
name: code-review
description: Read-only code review mode with audit logging
system_prompt: |
  You are a code reviewer. Focus on correctness, readability, security.
  Never modify files. Only analyze and report.
tools:
  whitelist: [Read, Grep, LoadSkill]
deny_paths:
  - secrets/**
  - "*.env"
hooks:
  - audit_log
  - deny_writes
---
```

Per-field semantics:

| Field | Required | Layer | Meaning |
|---|---|---|---|
| `name` | yes | n/a | Identifier (regex `^[A-Za-z][A-Za-z0-9_-]*$`) |
| `description` | yes | n/a | One-line description for trace / catalog |
| `system_prompt` | no | Layer 1 | Override `QueryContext.system_prompt`. If absent, base prompt used. |
| `tools.whitelist` | no | Layer 2 | Tools allowed in this mode. If absent, all tools available. Names match `ToolRegistry`-registered names. |
| `deny_paths` | no | Layer 3 | Additional deny patterns merged into `Settings.deny_paths` for the AuthZ Tier 2 check |
| `hooks` | no | Layer 3 | List of built-in hook names (`audit_log` / `deny_writes`); see D19.4 |

All optional fields fold gracefully — a bundle with only `system_prompt`
is valid and just overrides the prompt.

**D19.2 — Storage: `bundles/` independent package + project/global layers.**

Same shape as Skills (Phase 5c L2) and Commands (Phase 5b C2):

| Layer | Path | When used |
|---|---|---|
| Global | `~/.openharness/bundles/<name>.md` | User-wide modes |
| Project | `<cwd>/.openharness/bundles/<name>.md` | Project-local modes |

Project entry with same `name` overrides global.

**D19.3 — Trigger: via slash command `mode:` frontmatter field.**

Slash commands gain an optional `mode: <bundle_name>` frontmatter field
(extends Phase 5b Command schema):

```markdown
<!-- ~/.openharness/commands/security-review.md -->
---
name: security-review
description: Security-focused code review
mode: code-review
---
Please security-review:

{args}
```

When the LLM dispatches `/security-review last commit`:
1. CLI resolves slash command → expanded user message (existing P5b)
2. CLI reads `mode: code-review` → loads bundle `code-review.md` (new in 5d)
3. CLI applies bundle to QueryContext construction
4. Engine runs `run_query(messages, context)` normally

If `mode:` references an unknown bundle → `UnknownBundleError` raised
at CLI surface (matches `UnknownCommandError` UX pattern from 5b).

A slash command without `mode:` works exactly as in Phase 5b.

**D19.4 — Hook injection: built-in named hooks only (Phase 5d MVP).**

Phase 5d ships a fixed set of **framework-provided** named hooks:

| Name | Event | Behavior |
|---|---|---|
| `audit_log` | `PostToolUse` | Writes structured audit record to stderr (mirrors `tool_complete` log but with `audit=true` marker for easy `jq` filtering) — useful for compliance trace |
| `deny_writes` | `PreToolUse` | Denies any tool with `is_read_only=False` (Read/Grep/LoadSkill allow; Write/Edit/Bash/SpawnAgent deny). Belt-and-braces "read-only mode" — even if tools.whitelist is missed, this catches mutations. |

Bundle frontmatter `hooks: [name1, name2]` references these by string.
Unknown name → `UnknownBundleError` at CLI surface.

User-supplied custom hooks (Python plugin discovery / entry points)
defer to **Phase 5e** when a real third-party-extension demand
surfaces. Current MVP: 2 built-in hooks demonstrate the named-hook
composition mechanism + cover the most common bundle use case
(read-only audited review).

**D19.5 — Bundle application: pre-LLM, in `cli._run_ask`.**

ModeBundle resolution happens **before** `run_query` is called. The
CLI:

1. Resolves slash command (existing 5b code)
2. If command has `mode:` → loads bundle from FilesystemBundleStore
3. Constructs QueryContext with bundle's overrides applied to:
   - `system_prompt`: bundle.system_prompt or base prompt
   - `tool_registry`: wrapped with `WhitelistRegistry(base, bundle.tools.whitelist)` if specified; else base unchanged
   - `permission_checker`: `TierBasedPermissionChecker(registry, settings_with_extra_deny_paths)` — extra_deny_paths = bundle.deny_paths merged into Settings
   - `hook_registry`: base hook_registry + named hooks from bundle's `hooks:` list

The engine never sees bundle metadata — it receives a fully-resolved
QueryContext that LOOKS like any other QueryContext.

**D19.6 — Tool filter: `WhitelistRegistry` wrapping (no `ToolRegistry` change).**

Tool filtering implementation:

```python
class WhitelistRegistry:
    """Wraps a ToolRegistry, exposing only a whitelisted subset.

    Satisfies the same shape as ToolRegistry (get / list_tools /
    to_api_schema) — engine/dispatch is agnostic.
    """

    def __init__(self, base: ToolRegistry, whitelist: set[str]) -> None:
        self._base = base
        self._whitelist = whitelist
```

`ToolRegistry` itself stays unchanged. The wrapper:
- `get(name)`: raises `KeyError` if `name not in whitelist`, else delegates to `base.get`
- `list_tools()`: filtered subset
- `to_api_schema()`: filtered subset → LLM only sees whitelisted tools in its catalog

If a tool name in `whitelist` isn't actually registered → bootstrap
warning (similar to skip-not-fail discipline elsewhere).

---

## Cross-cutting invariant

**Phase 5d composes layers WITHOUT touching any of them**. The following
files stay unchanged:

- `permissions/checker.py` + `permissions/tier_based.py` (Bundle's
  extra deny_paths merge into Settings at QueryContext construction
  time; TierBasedPermissionChecker reads `settings.deny_paths`
  unchanged)
- `hooks/executor.py` + `hooks/registry.py` (Bundle's hooks register
  into the existing HookRegistry; named-hook lookup is a separate
  dict in `bundles/hooks.py`)
- `engine/query.py` + `engine/context.py` (Engine never knows about
  ModeBundle; receives resolved QueryContext)
- `observability/logging.py` (`audit_log` hook uses existing
  `get_logger` API; no new infrastructure)
- `mcp/` + `compaction/` + `skills/` + `commands/` (`commands/`
  gets one frontmatter field added — `mode: Optional[str]` — but
  Bundle resolution lives elsewhere)
- `protocols/` + `tools/` + `execution/` (untouched)

Where change IS allowed (additive):

- `bundles/` (new package): `Bundle` dataclass, `parse_bundle`,
  `FilesystemBundleStore`, `WhitelistRegistry`, `BUILTIN_HOOKS`
  registry, `UnknownBundleError`
- `commands/model.py`: `Command` dataclass gains optional `mode: str | None = None` field (frontmatter parser tolerates it; ignored if no bundle store consumes it)
- `cli.py`: bootstrap chain extended — load bundle on command resolution; apply overrides to QueryContext construction

If during build any "no change allowed" layer needs editing, **stop
and re-open the boundary doc**. ModeBundle's whole reason for being
is to validate that the layered model composes cleanly — failure to
honor that invariant means the layer abstractions need revisiting,
not that ModeBundle needs a hack.

---

## Out of scope (Phase 5e+)

- **User-supplied custom hooks** (Python plugin discovery / entry
  points). Phase 5d ships 2 built-in hooks; Phase 5e if demand surfaces.
- **`tools.blacklist`** (vs whitelist). Whitelist is cleaner contract
  (explicit allow); blacklist defers if a real use case demands.
- **CLI flag `--bundle <name>`** for ad-hoc bundle invocation without
  a slash command. Phase 5e+ — current MVP uses slash command as the
  sole trigger to reuse 5b UX completely.
- **Bundle inheritance** (bundle extends another bundle). Defer; YAGNI
  until users actually write many similar bundles.
- **Mid-conversation bundle switching** (push/pop). Bundles are per-
  query in 5d. Stateful sessions defer to Phase 7+ `oh chat` mode.
- **Bundle catalog injection into system prompt** ("Available bundles").
  Not relevant — bundles are user-triggered, LLM doesn't choose them.
- **Permission tier OVERRIDE** (bundle disables Tier 1 hardcoded
  paths). Out of scope — Tier 1 is framework-owned safety, never
  user-overridable.

---

## Critical decisions (D19.x)

| ID | Decision | Why |
|---|---|---|
| **D19.1** | ModeBundle as 4-layer composition (system_prompt + tool filter + deny_paths overlay + named hooks) | The first cross-layer tenant; validates that the layered model composes |
| **D19.2** | Independent `bundles/` package, global + project storage | Clean separation from commands/skills; same UX shape users already know |
| **D19.3** | Trigger via slash command `mode:` frontmatter field | Reuses 5b UX completely; no new trigger mechanism |
| **D19.4** | Built-in named hooks only (`audit_log` + `deny_writes`) | MVP covers 80% use case (read-only audited mode); plugin discovery defers to 5e |
| **D19.5** | Bundle resolves to a modified QueryContext at CLI bootstrap | Pre-LLM resolution; engine sees no Bundle concept |
| **D19.6** | Tool filter via `WhitelistRegistry` wrapper | `ToolRegistry` stays unchanged; wrapper satisfies same API shape |

---

## Dependency direction

```
bundles/                          (new package)
   ├── model.py                   ← Bundle dataclass + parse_bundle (YAML frontmatter)
   ├── store.py                   ← FilesystemBundleStore (global + project)
   ├── registry.py                ← WhitelistRegistry (wraps ToolRegistry)
   ├── hooks.py                   ← BUILTIN_HOOKS = {"audit_log": ..., "deny_writes": ...}
   └── errors.py                  ← UnknownBundleError + InvalidBundleFieldError

commands/model.py                 ← +1 optional field (mode: str | None = None)
cli.py                            ← bootstrap chain extended:
                                       1. resolve slash command (5b)
                                       2. if command.mode → load bundle
                                       3. apply bundle to QueryContext

permissions/                      ← ZERO CHANGE (invariant)
hooks/executor.py + registry.py   ← ZERO CHANGE (invariant; bundle's named hooks
                                                   register into existing infra)
engine/                           ← ZERO CHANGE (invariant)
observability/                    ← ZERO CHANGE (audit_log uses get_logger)
mcp/ + compaction/ + skills/      ← ZERO CHANGE
protocols/ + tools/ + execution/  ← ZERO CHANGE
```

`bundles/` is downstream of `commands/` (uses Command's `mode` field)
and `tools/` (wraps ToolRegistry); upstream of `cli.py` (consumed in
bootstrap).

---

## Acceptance for Phase 5d close-out

- [ ] `bundles/model.py` — `Bundle` frozen dataclass + `parse_bundle`
  (YAML frontmatter, never-raises discipline)
- [ ] `bundles/store.py` — `FilesystemBundleStore` (two-layer global +
  project; project wins on same name)
- [ ] `bundles/registry.py` — `WhitelistRegistry` wraps `ToolRegistry`
  exposing only listed tools
- [ ] `bundles/hooks.py` — `BUILTIN_HOOKS` dict with `audit_log` +
  `deny_writes` named hooks
- [ ] `bundles/errors.py` — `UnknownBundleError` + validation errors
- [ ] `commands/model.py` — Command dataclass gains optional `mode:
  str | None = None` field
- [ ] `cli.py` — bootstrap loads bundle when command has `mode`,
  applies overrides (system_prompt + WhitelistRegistry + extra
  deny_paths merge + named hooks register)
- [ ] Unit tests for each piece (bundle parse / store / registry
  wrap / built-in hooks behavior)
- [ ] CLI integration test: slash command with `mode:` triggers
  bundle, all 4 layers applied (verified via captured QueryContext)
- [ ] **Cross-cutting invariant verified by structural test**: no
  `Bundle` / `WhitelistRegistry` / `BUILTIN_HOOKS` / `parse_bundle`
  identifier in any of the 22+ protected modules (extends Phase 7b's
  invariant test)
- [ ] **Formal git-diff invariant verification**: zero diff against
  Phase 7b close on permissions / hooks / engine / observability /
  mcp / compaction / skills / protocols / tools / execution
- [ ] mypy strict + ruff clean
- [ ] README "Phase 5d — ModeBundle" section
- [ ] `learnings/phase-5d.md` retro

---

## Pointers

- Phase 5b boundary (commands trigger reused here): [`decisions/14-phase-5b-boundary.md`](./14-phase-5b-boundary.md)
- Phase 5c boundary (skills storage shape mirrored): [`decisions/12-phase-5c-skills-boundary.md`](./12-phase-5c-skills-boundary.md)
- Phase 7a retro ("stable plateau" framing — Phase 5d is the cross-layer composition test): [`learnings/phase-7a.md`](../learnings/phase-7a.md) §3.1
- Phase 5b retro ("layered extension model" — Phase 5d composes these layers without touching them): [`learnings/phase-5b-commands.md`](../learnings/phase-5b-commands.md) §3.1
