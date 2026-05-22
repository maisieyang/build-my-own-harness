"""Plugin system error types — P9-T1 (decisions/24).

Three error classes mapped to the three failure modes the plugin
system surfaces:

- :class:`PluginManifestError` — manifest YAML malformed or fails
  validation (raised only when the caller explicitly opts into strict
  mode; ``parse_manifest`` itself never raises, it returns ``None`` +
  logs a warning).
- :class:`PluginConflictError` — two installed plugins claim the same
  ``name``. Detected at :class:`PluginLoader.discover` time
  (P9-T2). Listing both manifest paths in the error message lets the
  user immediately spot which directory to rename.
- :class:`PluginInstallError` — ``oh plugins install <source>`` or
  ``oh plugins remove <name>`` filesystem operation failed (P9-T4).
  Reasons include: git clone exit non-zero, source path not found,
  destination already exists.

All three derive from :class:`OpenHarnessError` so the CLI top-level
``except OpenHarnessError`` handler catches them uniformly.
"""

from __future__ import annotations

from openharness.errors import OpenHarnessError


class PluginManifestError(OpenHarnessError):
    """Plugin manifest YAML is malformed or fails strict validation."""


class PluginConflictError(OpenHarnessError):
    """Two installed plugins claim the same plugin name."""


class PluginInstallError(OpenHarnessError):
    """``oh plugins install`` / ``remove`` filesystem operation failed."""
