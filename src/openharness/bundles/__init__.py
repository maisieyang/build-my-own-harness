"""ModeBundle subsystem — Phase 5d.

Cross-layer composition primitive. A bundle is a markdown file with YAML
frontmatter that declares:

- ``system_prompt`` (Layer 1 override)
- ``tools.whitelist`` (Layer 2 catalog filter)
- ``deny_paths`` (Layer 3 permission overlay)
- ``hooks`` (Layer 3 hook injection by name from BUILTIN_HOOKS)

Per ``decisions/17-phase-5d-boundary.md`` D19.x:

- D19.1: 4-layer composition (all 4 fields optional)
- D19.2: ``~/.openharness/bundles/<name>.md`` global + ``<project>/
  .openharness/bundles/<name>.md`` project; project wins
- D19.3: slash command ``mode: <bundle_name>`` frontmatter triggers
  bundle application
- D19.4: built-in named hooks only (``audit_log`` + ``deny_writes``);
  user plugins defer to Phase 5e
- D19.5: bundle resolves to modified ``QueryContext`` in cli._run_ask
  (pre-LLM); engine sees no Bundle concept
- D19.6: ``WhitelistRegistry`` wraps ``ToolRegistry`` (the only Bundle
  type that the engine treats as ``ToolRegistry`` polymorphically)

Cross-cutting invariant — Phase 5d is the FIRST cross-layer tenant.
It composes existing layer primitives (system_prompt / hook_registry /
deny_paths / wrapped ToolRegistry) WITHOUT modifying any of them.
``permissions/``,``hooks/``,``engine/``,``observability/`` all stay
zero-diff vs Phase 7b close.

Public API:

    from openharness.bundles import (
        Bundle, parse_bundle,
        BundleStore, FilesystemBundleStore, EmptyBundleStore,
        WhitelistRegistry,
        BUILTIN_HOOKS, resolve_hook,
        apply_bundle_to_context,
        UnknownBundleError,
    )
"""

from __future__ import annotations

from openharness.bundles.apply import BundleApplication, apply_bundle_to_context
from openharness.bundles.errors import UnknownBundleError
from openharness.bundles.hook_plugins import (
    HookSpec,
    discover_plugin_hooks,
    hook_spec,
)
from openharness.bundles.hooks import BUILTIN_HOOKS, resolve_hook
from openharness.bundles.model import Bundle, parse_bundle
from openharness.bundles.registry import WhitelistRegistry
from openharness.bundles.store import (
    BundleStore,
    EmptyBundleStore,
    FilesystemBundleStore,
)

__all__ = [
    "BUILTIN_HOOKS",
    "Bundle",
    "BundleApplication",
    "BundleStore",
    "EmptyBundleStore",
    "FilesystemBundleStore",
    "HookSpec",
    "UnknownBundleError",
    "WhitelistRegistry",
    "apply_bundle_to_context",
    "discover_plugin_hooks",
    "hook_spec",
    "parse_bundle",
    "resolve_hook",
]
