# Phase 5f Implementation Plan — Filesystem Hook Plugins

> Phase 5f boundary: [`decisions/20-phase-5f-boundary.md`](../decisions/20-phase-5f-boundary.md).
> Builds on Phase 5e: [`decisions/18-phase-5e-boundary.md`](../decisions/18-phase-5e-boundary.md).

## Overview

Add filesystem-based hook plugin discovery: `~/.openharness/hooks/
*.py` (global) + `<cwd>/.openharness/hooks/*.py` (project). Same
`@hook_spec` decorator, same opt-in flag (reused from 5e), same
collision policy. The framework imports each `.py` file, collects
`HookSpec`-typed attributes, merges into the same catalog already
flowing through `apply_bundle_to_context`.

**Total scope**: ~half day, 3 capabilities, ~4 commits, ~100 LoC
production + ~120 LoC tests.

## Task list

### P5f-T1: `discover_filesystem_hook_plugins` 🔜 NEXT

**Description**: New loader function + private `_default_module_loader`
helper in `bundles/hook_plugins.py`. Skip-not-fail discipline; same
shape as the entry-point loader.

**Acceptance**:
- [ ] `bundles/hook_plugins.py`:
  - `_default_module_loader(path) -> object | None` using
    `importlib.util.spec_from_file_location` +
    `exec_module`. Module name = `openharness._user_hook_<sha8>`
    where sha8 is first 8 hex of SHA-256 of absolute path. Returns
    the loaded module object, or None if load fails (with warning).
  - `discover_filesystem_hook_plugins(*, global_dir, project_dir,
    module_loader=None) -> dict[str, HookSpec]`:
    - Scans both dirs for `*.py` files (sorted)
    - For each file, calls `module_loader(path)`; if None, skip
    - Walks `getattr(module, name)` for every attribute name;
      collects those that `isinstance(_, HookSpec)`
    - Applies the SAME collision rules as 5e:
      - `name in BUILTIN_HOOKS` → skip + warning
      - `name in already-loaded plugins` from same scan →
        first-wins + warning
      - `name in global` then project → project wins +
        `filesystem_hook_override` info log
- [ ] `bundles/__init__.py` exports `discover_filesystem_hook_plugins`
- [ ] Tests `tests/bundles/test_hook_plugins.py` extended:
  - Empty dirs → empty catalog
  - Module with one `@hook_spec`-decorated function → loaded
  - Module with multiple `HookSpec` exports → multiple plugins
  - Module that fails to import (stub raises) → skipped + warning
  - Module with no `HookSpec` attributes → silent skip
  - Built-in collision skipped + warning
  - Same-layer same-name collision → first-wins + warning
  - Project overrides global on same name
  - Non-`.py` files ignored

**Files**:
- `src/openharness/bundles/hook_plugins.py` (extend)
- `src/openharness/bundles/__init__.py` (export)
- `tests/bundles/test_hook_plugins.py` (extend)

---

### P5f-T2: CLI bootstrap merges filesystem catalog

**Description**: `cli._run_ask` extends the `discover_plugin_hooks()`
call to also merge filesystem-discovered plugins. Entry-point
plugins shadow filesystem plugins on same name (D22.4).

**Acceptance**:
- [ ] `cli.py`:
  - Compute `plugin_hook_catalog` as: empty if flag off; else
    entry-point catalog ∪ filesystem catalog with first-wins.
  - Reuse the `Path.home() / ".openharness" / "hooks"` /
    `Path.cwd() / ".openharness" / "hooks"` convention.
- [ ] CLI tests in `tests/cli/test_cli.py::TestPluginHookDiscovery`:
  - Flag on + only filesystem hook (no entry points) → resolves
  - Flag on + filesystem hook + bundle hook_names references it →
    registers + fires
  - Entry-point + filesystem with same name → entry-point wins
  - Flag off → filesystem hook NOT loaded (sentinel proves
    `discover_filesystem_hook_plugins` not called)

---

### P5f-T3: Invariant + README + retro

**Acceptance**:
- [ ] `tests/execution/test_invariant.py` extends Phase 5d forbidden
  set with `discover_filesystem_hook_plugins`.
- [ ] Formal git-diff vs Phase 8 close: protected dirs unchanged.
- [ ] README "Phase 5f — filesystem hook plugins" section
- [ ] `learnings/phase-5f.md` retro
- [ ] DoD checklist all green
