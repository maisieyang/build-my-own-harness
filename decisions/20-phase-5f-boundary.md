# Phase 5f Boundary — Filesystem Hook Plugins

> Status: locked at Phase 5f entry, 2026-05-19.
>
> Scope note: **complement to Phase 5e**. 5e shipped Python-entry-
> point discovery for plugin hooks. 5f adds a second discovery source:
> Python files dropped under `~/.openharness/hooks/*.py` (global) +
> `<cwd>/.openharness/hooks/*.py` (project). Same `@hook_spec`
> decorator. Same opt-in flag. Same collision policy with
> `BUILTIN_HOOKS`. The difference is purely in HOW the framework
> discovers the file — `importlib.util.spec_from_file_location`
> instead of `importlib.metadata.entry_points`.
>
> Rationale: 5e retro §5 + decisions/18 D20.1 acknowledged filesystem
> discovery as a Phase 5f candidate. The user-facing demand is "I
> want a quick hook without packaging it as a pip install" — same
> shape as commands/skills/bundles for markdown.

## Triggering observation

Phase 5e established the `HookSpec` + `hook_spec` decorator + plugin
catalog plumbing through to `apply_bundle_to_context`. 5e's loader
(`discover_plugin_hooks`) reads entry points; the catalog format
(`dict[str, HookSpec]`) is independent of the source.

A second discovery source costs:

- one new loader function (`discover_filesystem_hook_plugins`)
- one new Settings flag (or merge into the existing one — see D22.3)
- a CLI bootstrap line to merge filesystem-discovered hooks into the
  same catalog already flowing through

The catalog merger logic (`{name: HookSpec}` dict with first-wins
collision) is already there. **5f is purely additive on the
discovery axis** — `resolve_hook` and `apply_bundle_to_context`
don't change.

## Decisions

### D22.1 — Filesystem layer: `~/.openharness/hooks/*.py` + `<cwd>/.openharness/hooks/*.py`

Same two-layer convention as commands/skills/bundles, but with `.py`
extension. Global hooks live under `~/.openharness/hooks/`, project-
specific under `<cwd>/.openharness/hooks/`. Project overrides global
on same plugin name (mirrors the markdown_store convention).

Each `.py` file imports `hook_spec` from `openharness.bundles` and
exports one or more `HookSpec`-decorated callables:

```python
# ~/.openharness/hooks/slack_notify.py
from openharness.bundles import hook_spec

@hook_spec("PostToolUse")
async def slack_notify(context):
    """Notify Slack on tool dispatch complete."""
    ...
```

The plugin name = `HookSpec`-decorated attribute name (matches 5e
entry-point shape). File name is irrelevant for plugin naming —
the file can be named anything `.py`; the framework imports the
module and scans its attributes for `HookSpec` instances.

### D22.2 — Same security model as 5e: opt-in via Settings flag

Filesystem hook plugins reuse `Settings.enable_plugin_hooks` (the
5e flag). One flag enables BOTH entry-point AND filesystem
discovery — they're the same trust boundary ("yes, I want
third-party hooks to load"). The flag remains default OFF.

**Why one flag, not two**: a user who's opted into entry-point
plugins has already accepted "arbitrary Python from third-party
packages can deny/modify my tool calls." Adding a separate flag for
filesystem would let them say "I trust npm but not my own
filesystem" — incoherent. The framework treats both as "I am OK
with plugin hooks running." Tighter granularity defers to Phase 5g
if a real use case surfaces.

### D22.3 — `discover_filesystem_hook_plugins` parallel to `discover_plugin_hooks`

New function in `bundles/hook_plugins.py`:

```python
def discover_filesystem_hook_plugins(
    *,
    global_dir: Path | None = None,
    project_dir: Path | None = None,
) -> dict[str, HookSpec]:
    """Scan ``global_dir`` and ``project_dir`` for ``*.py`` files,
    import each, collect ``HookSpec``-typed module attributes."""
```

Same skip-not-fail discipline as entry-point discovery:

- File can't be read → `filesystem_hook_read_failed` warning, skip
- Module fails to import (syntax / dependency / runtime error at
  module level) → `filesystem_hook_load_failed` warning, skip
- File contains no `HookSpec` attributes → `filesystem_hook_no_specs`
  info (not warning — empty file is benign), skip silently
- Spec name collides with `BUILTIN_HOOKS` → skipped + warning
- Project layer collides with global on same plugin name → project
  wins, `filesystem_hook_override` info logged
- Same-layer same-name collision → first-wins, warning logged

The plugin's NAME is the attribute name on the imported module
(not the file name). Multiple `HookSpec`-decorated callables in one
file = multiple plugins from one file.

### D22.4 — Merge order: entry points BEFORE filesystem

When both discovery sources contribute, entry-point plugins are
loaded FIRST, filesystem SECOND. On collision, **first-wins** —
entry-point plugin shadows a filesystem plugin with the same name.

**Why entry-point first**: packaged plugins are a stronger claim of
intent (someone wrote a pyproject.toml + published) than a dropped
filesystem file. Same logic as "framework > plugins" — more
deliberate source wins.

This is implemented in `cli._run_ask`:

```python
catalog = {}
if enable_plugin_hooks:
    catalog.update(discover_plugin_hooks())  # entry points
    fs_catalog = discover_filesystem_hook_plugins(
        global_dir=Path.home() / ".openharness" / "hooks",
        project_dir=Path.cwd() / ".openharness" / "hooks",
    )
    for name, spec in fs_catalog.items():
        if name not in catalog:
            catalog[name] = spec
        # else: entry-point won; skip silently (already logged in
        # discover_filesystem_hook_plugins if internal collision)
```

### D22.5 — Module import via `importlib.util.spec_from_file_location`

Each `.py` file gets imported as a uniquely-named module to avoid
namespace clashes between global / project versions of the same
file name. The module spec uses
`openharness._user_hook_<sha8>_<filename_sans_ext>` as the module
name; sha8 = first 8 hex chars of SHA-256 of the absolute path.

Imported module is NOT added to `sys.modules` permanently (to avoid
polluting future imports) — it's stashed in a local dict for the
duration of `discover_filesystem_hook_plugins` and discarded.

### D22.6 — Test seam: `module_loader` keyword-only override

Same pattern as 5e's `entry_point_source` seam (D20.6). The function
takes a `module_loader: Callable[[Path], object | None] | None = None`
kwarg. Production defaults to a private `_default_module_loader`
that does `spec_from_file_location` + `exec_module`. Tests inject a
stub returning a plain Python object whose attributes are inspected
the same way.

---

## Cross-cutting invariant

Phase 5f is **purely additive within `bundles/hook_plugins.py`**.
Zero diff vs Phase 8 close on:

- `permissions/`, `hooks/`, `engine/`, `observability/`, `mcp/`,
  `compaction/`, `skills/`, `commands/`, `protocols/`, `tools/`,
  `execution/`, `markdown_store/` (Phase 8 module)
- `bundles/{model.py, store.py, registry.py, errors.py, hooks.py,
  apply.py}` — none touched
- `config/settings.py` — no new Settings field (reuses
  `enable_plugin_hooks` from 5e)

**Allowed diffs**:

- `bundles/hook_plugins.py` — add `discover_filesystem_hook_plugins`
  function + private `_default_module_loader` helper. No changes to
  `HookSpec` / `hook_spec` / `discover_plugin_hooks`.
- `bundles/__init__.py` — export `discover_filesystem_hook_plugins`.
- `cli.py` — extend the plugin-catalog assembly to merge filesystem
  hooks after entry points (D22.4).

## Risks specifically NOT mitigated

- **Per-source granular opt-in**: one flag enables both sources. If
  a user wants "entry-point yes, filesystem no" or vice versa,
  defer to Phase 5g.
- **`.py` file hot reload**: discovery is one-shot at bootstrap.
- **Plugin sandboxing**: filesystem hooks run with full host
  privileges. Defer to a hypothetical "hook sandbox" phase if a
  real demand surfaces — non-trivial.
- **Documentation generation from filesystem hook docstrings**:
  Phase 8 candidate (`oh hooks list`).

---

## Pointers

- Phase 5e boundary (entry-point shape this mirrors): `decisions/18-phase-5e-boundary.md`
- Phase 5e retro §5 (filesystem candidate noted): `learnings/phase-5e.md`
- Phase 8 retro §4 (filesystem discovery shape comment): `learnings/phase-8.md`
- The discovery code being extended: `src/openharness/bundles/hook_plugins.py`
