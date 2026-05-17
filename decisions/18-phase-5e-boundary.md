# Phase 5e Boundary — Plugin Hook Discovery

> Status: locked at Phase 5e entry, 2026-05-17.
>
> Scope note: **this boundary covers third-party hook discovery via
> Python entry points**, generalizing Phase 5d's `BUILTIN_HOOKS`
> catalog so users can supply custom hooks. Bundle frontmatter's
> `hooks: [name1, name2]` field continues to work unchanged — names
> resolve against the union of framework-built-ins + discovered
> plugins.
>
> Rationale + framing: Phase 5d D19.4 explicitly deferred plugin
> discovery to "5e when a real third-party-extension demand surfaces."
> The demand is structural: the framework's two built-in hooks
> (`audit_log` + `deny_writes`) cover compliance + read-only semantics
> but can't anticipate every cross-cutting concern (cost guards, Slack
> notifications, organizational audit, custom retry policies). Without
> a plugin pathway, users would have to either fork the framework or
> register hooks programmatically — neither acceptable for the
> "FDE-deployed harness" use case (declarative configuration, no code
> changes to deploy a new behavior).

## Triggering observation

Phase 5d landed `BUILTIN_HOOKS: dict[str, tuple[HookEvent, Hook]]` as
the catalog bundle frontmatter looks up against. The structure is
already extensible by design — `resolve_hook(name)` reads the dict +
raises `UnknownBundleError(kind="hook")` on miss. Phase 5e adds **one
more catalog source** (Python entry points) without changing the
bundle's lookup semantics. The cross-layer invariant for 5e is
therefore **narrower than 5d's**: zero diff to all original layers AND
zero diff to `bundles/{model.py, store.py, registry.py, errors.py}` —
only `bundles/hooks.py` (extend `resolve_hook`), `bundles/apply.py`
(thread plugin catalog through), new `bundles/hook_plugins.py`,
`cli.py`, and `config/settings.py` may move.

## Decisions

### D20.1 — Discovery via Python entry points (group: `openharness.hooks`)

Plugin authors declare hooks in their package's `pyproject.toml`:

```toml
[project.entry-points."openharness.hooks"]
slack_notify = "my_pkg.hooks:slack_notify_spec"
budget_guard = "my_pkg.hooks:budget_guard_spec"
```

Each entry point points to a module attribute of type `HookSpec`
(frozen dataclass with `event: HookEvent` + `hook: Hook` fields). The
framework reads via `importlib.metadata.entry_points(group=
"openharness.hooks")` at bootstrap.

**Why entry points (vs filesystem-drop)**:

| Axis | Entry points | `~/.openharness/hooks/*.py` |
|---|---|---|
| Code-execution surface | Already consented via `pip install` | Arbitrary code from filesystem |
| Discovery cost | One stdlib call | Walk directory + import each file |
| Plugin distribution | Standard `pip` ecosystem | Manual file copy |
| Same shape as commands/skills/bundles? | No (Python package, not markdown) | Yes |

Filesystem-drop matches the markdown-discovery convention but the
"arbitrary Python from disk" surface is materially riskier than
"arbitrary markdown from disk." Entry points are the standard Python
plugin mechanism (mirrors pytest fixtures, setuptools plugins, etc.)
— users already understand the security model. Phase 5e ships
entry-point discovery only; filesystem `*.py` defers unless real
demand surfaces.

### D20.2 — Decorator API: `@hook_spec(event)` returns `HookSpec`

Plugin author UX:

```python
from openharness.bundles import hook_spec

@hook_spec("PostToolUse")
async def slack_notify_spec(context):
    """Notify a Slack channel when a tool dispatch completes."""
    ...
```

The decorator wraps the function in a `HookSpec(event=event,
hook=<original_fn>)` instance. Entry point loads the wrapped object.
`HookSpec` is a frozen dataclass; no inheritance hierarchy.

**Why a decorator (vs raw `HookSpec(event=..., hook=fn)` constructor
call)**: callers naturally write `@decorator` next to a function
definition, so the entry-point attribute path is just the function's
own name. Constructor-style requires a separate `_fn = ...; my_hook =
HookSpec(event="...", hook=_fn)` line and exports two names.
Decorator is shorter and matches `pytest.fixture` / `click.command`
idiom.

### D20.3 — Opt-in via Settings flag (default OFF)

Plugin hook discovery is gated by `Settings.enable_plugin_hooks:
bool = False` (env: `OPENHARNESS_ENABLE_PLUGIN_HOOKS=true`; CLI:
`--enable-plugin-hooks` / `--no-enable-plugin-hooks`).

**Why opt-in (vs auto-discover)**: a transitive dependency that ships
an `openharness.hooks` entry point could register a hook the user
doesn't know about — and unlike a tool, a hook can DENY/MODIFY any
tool call. The blast radius is too large to default ON. Matches the
`--sandbox` (P7b D18.1) opt-in shape.

When the flag is OFF, `discover_plugin_hooks()` is not called at all
— bundle's `hook_names:` field resolves against only `BUILTIN_HOOKS`.
A bundle referencing a plugin hook name will raise
`UnknownBundleError(kind="hook")` exactly as it would if the plugin
weren't installed.

### D20.4 — Collision policy: framework > plugins > error

`discover_plugin_hooks()` returns a `dict[str, HookSpec]` of valid
plugin entries. Collision rules:

1. **Plugin name == built-in name**: framework wins. Plugin entry is
   **skipped + warning logged** (`plugin_hook_collides_with_builtin`).
   Rationale: framework hooks are documented, version-stable, and
   tested; a plugin can't silently override them.
2. **Plugin name == another plugin name**: first-seen wins (entry-point
   discovery order is package install order, generally deterministic
   but not user-controlled). Subsequent entries skipped + warning
   logged (`plugin_hook_collision`). Users debug by uninstalling one.
3. **Plugin load fails** (import error, wrong type, exception in
   decorator): skipped + warning logged. Same skip-not-fail discipline
   as `parse_command` / `parse_skill` / `parse_bundle`.

### D20.5 — `resolve_hook` extension via additive kwarg

`resolve_hook(name)` becomes `resolve_hook(name, plugin_catalog=None)`.
When `plugin_catalog` is `None` or empty, behavior is byte-identical
to Phase 5d (only `BUILTIN_HOOKS` consulted). When provided, lookup
order is: `BUILTIN_HOOKS` first → `plugin_catalog` second → raise.
Built-in hooks always shadow plugins on the same name (D20.4
collision policy already filtered plugin entries that collide, but
the lookup order is defensive in case).

`apply_bundle_to_context(..., plugin_hook_catalog=None)` gains the
same kwarg, threading the catalog to its internal `resolve_hook` call
via `_clone_hook_registry`'s loop. The Phase 5d signature stays
backward-compatible — existing tests that pass `plugin_hook_catalog=None`
or omit it entirely behave exactly as Phase 5d.

### D20.6 — Discovery is a pure-function abstraction (testable without real entry points)

`discover_plugin_hooks(*, group="openharness.hooks", entry_point_source=None)`
where `entry_point_source` defaults to `importlib.metadata.entry_points`
but can be overridden in tests with a stub returning fixture entry
points. This avoids the "test plugin discovery without installing a
real package" pain.

The seam is keyword-only so production callers (CLI bootstrap) always
use the default + can't accidentally short-circuit discovery.

---

## Cross-cutting invariant

**Phase 5e adds a catalog source without modifying any layer**. The
following stay UNCHANGED vs Phase 5d close (commit `878d80a`):

- `permissions/` — 0 lines
- `hooks/` — 0 lines (hook event/registry/executor unchanged)
- `engine/` — 0 lines
- `observability/` — 0 lines
- `mcp/` — 0 lines
- `compaction/` — 0 lines
- `skills/` — 0 lines
- `commands/` — 0 lines
- `protocols/` — 0 lines
- `tools/` — 0 lines
- `execution/` — 0 lines
- `bundles/model.py` — 0 lines
- `bundles/store.py` — 0 lines
- `bundles/registry.py` — 0 lines
- `bundles/errors.py` — 0 lines (`UnknownBundleError(kind="hook")` is
  already the right shape from Phase 5d)
- `prompts.py` — 0 lines

**Allowed diffs (additive only)**:

- `bundles/hooks.py` — extend `resolve_hook` signature (add
  `plugin_catalog` kwarg, default `None`). Body adds one extra lookup
  branch.
- `bundles/apply.py` — extend `apply_bundle_to_context` signature
  (add `plugin_hook_catalog` kwarg, default `None`). Pass through to
  `_clone_hook_registry` → inner `resolve_hook` call.
- `bundles/hook_plugins.py` — **new module**. `HookSpec` dataclass +
  `hook_spec` decorator + `discover_plugin_hooks()` function.
- `bundles/__init__.py` — export additions (`HookSpec`, `hook_spec`,
  `discover_plugin_hooks`).
- `cli.py` — discover at bootstrap when flag on; thread catalog
  through `apply_bundle_to_context`; new `--enable-plugin-hooks`
  Typer flag.
- `config/settings.py` — `enable_plugin_hooks: bool = False` field.

## Test invariant extension

`tests/execution/test_invariant.py` `TestPhase5dCrossCuttingInvariant`
forbidden set extended with `HookSpec` / `hook_spec` /
`discover_plugin_hooks`. The 46 protected modules already enforced
in Phase 5d remain — Phase 5e identifiers must NOT leak there
either.

## Risks specifically NOT mitigated (Phase 5f+ candidates)

- **Filesystem plugin discovery** (`~/.openharness/hooks/*.py`) —
  symmetric with commands/skills/bundles but materially bigger
  security surface; defer until demand surfaces.
- **Hook plugin scoped per-bundle** — currently a plugin hook is
  available globally once `--enable-plugin-hooks` is on; bundles
  reference any hook by name. Hypothetical "plugin available only
  for this bundle" is YAGNI.
- **Plugin hook unloading / reloading** — discovery is one-shot at
  bootstrap. `oh chat` (Phase 7+) might want dynamic reload.
- **Plugin metadata** (version, description, source attribution) —
  current `HookSpec` has only event + callable. README example shows
  docstring-as-description; no programmatic discovery API.

---

## Pointers

- Phase 5d retro (where the deferral was recorded): `learnings/phase-5d.md` §3.4
- Phase 5d boundary D19.4 (the "built-in only for MVP" decision): `decisions/17-phase-5d-boundary.md`
- Phase 7b boundary D18.1 (the opt-in flag shape this phase mirrors): `decisions/16-phase-7b-boundary.md`
- Hook runtime (untouched in 5e): `src/openharness/hooks/`
