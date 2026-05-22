"""Plugin system — unified distribution layer for the 5 extension subsystems.

Phase 9 (decisions/24) introduces a new ``plugins/`` package that
multiplexes the existing extension mechanisms — Skills (Phase 5c),
Commands (Phase 5b), Bundles (Phase 5d), Hooks (Phase 5e/5f), and
MCP servers (Phase 5) — under a single :class:`PluginManifest`
declaration.

The 5 mechanisms themselves are **not changed** (cross-cutting
invariant); this package is a multiplexed installer that fans out to
their existing registration paths at bootstrap.

Public API:

::

    from openharness.plugins import (
        PluginManifest,
        ComponentRef, HookRef,
        parse_manifest,
        PluginManifestError, PluginConflictError, PluginInstallError,
    )

Sub-units land incrementally (see ``tasks/phase-9-plan.md``):

- P9-T1 (this module): ``PluginManifest`` dataclass + ``parse_manifest``
  YAML parser
- P9-T2: ``PluginLoader.discover`` + ``fan_out`` to 5 stores
- P9-T3: ``Settings.enable_plugins`` + CLI bootstrap integration
- P9-T4: ``oh plugins list / show / install / remove`` CLI subcommands
- P9-T5: End-to-end smoke + retro
"""

from __future__ import annotations

from openharness.plugins.errors import (
    PluginConflictError,
    PluginInstallError,
    PluginManifestError,
)
from openharness.plugins.loader import (
    NAMESPACE_SEPARATOR,
    LayeredStore,
    LoadedPluginCatalogs,
    PluginLoader,
    namespaced,
)
from openharness.plugins.model import (
    PLUGIN_NAME_PATTERN,
    ComponentRef,
    HookRef,
    PluginManifest,
    parse_manifest,
)

__all__ = [
    "NAMESPACE_SEPARATOR",
    "PLUGIN_NAME_PATTERN",
    "ComponentRef",
    "HookRef",
    "LayeredStore",
    "LoadedPluginCatalogs",
    "PluginConflictError",
    "PluginInstallError",
    "PluginLoader",
    "PluginManifest",
    "PluginManifestError",
    "namespaced",
    "parse_manifest",
]
