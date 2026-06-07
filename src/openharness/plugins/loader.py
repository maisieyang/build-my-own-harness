"""``PluginLoader`` + ``LayeredStore`` — P9-T2 (decisions/24).

This module is the **fan-out point** of the Plugin system. It does two
things:

1. **Discover** plugins by scanning ``<plugins_dir>/<name>/manifest.yaml``
   files. Returns ``dict[plugin_name, PluginManifest]``. Raises
   :class:`PluginConflictError` if two plugin directories declare the
   same ``name``.
2. **Fan out** each plugin's 5 component types into the existing
   subsystem stores by **wrapping** them with :class:`LayeredStore`
   (plugin catalog overlaid on base store), and accumulating MCP
   servers + plugin-hook specs as namespaced entries.

**Namespacing — separator choice**:

Boundary doc decisions/24 D27.3 displayed the namespace as
``my-plugin:deploy`` (with colon). However the existing 5 subsystem
name regexes — :data:`openharness.mcp.config._NAME_PATTERN` for MCP,
:data:`openharness.markdown_store.NAME_PATTERN` for Commands /
Skills / Bundles — all match ``^[A-Za-z][A-Za-z0-9_-]*$``, which
**rejects ``:``**.

To keep the invariant ("mcp/ and the 3 markdown_store domains stay
zero diff"), this module uses **``__`` (double underscore)** as the
actual storage separator: ``my-plugin__deploy``. The double
underscore is valid under all 5 regexes (just letters/digits/`-`/`_`)
and is visually distinguishable from kebab-case plugin names + their
underlying component names. CLI display layers MAY translate ``__``
back to ``:`` for user-facing readability;internal storage is ``__``.

**Module loading (hooks)**:

Plugin hooks are declared as ``{module: my_pkg.hooks, name: my_fn}``.
``module`` is a dotted path resolved against the plugin's
:attr:`PluginManifest.source_path` as a relative filesystem path
(``my_pkg/hooks.py``). This module's :func:`_load_plugin_module`
imports the file using ``importlib.util.spec_from_file_location`` with
a sha8-prefixed module name, matching the pattern in
``bundles/hook_plugins.py:_default_module_loader`` (deliberately
duplicated to keep bundles/ at zero diff;both call sites should
update together if we ever extract a shared module-loader utility).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from openharness.bundles.hook_plugins import HookSpec
from openharness.commands.model import Command, parse_command
from openharness.mcp.config import McpServerConfig
from openharness.observability.logging import get_logger
from openharness.plugins.errors import PluginConflictError
from openharness.plugins.model import (
    HookRef,
    PluginManifest,
    parse_cc_plugin,
    parse_manifest,
)
from openharness.skills.model import Skill, parse_skill

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.bundles.model import Bundle


_logger = get_logger("plugins")


# --------------------------------------------------------------------------- #
# Namespacing                                                                 #
# --------------------------------------------------------------------------- #


NAMESPACE_SEPARATOR = "__"
"""Storage-layer separator between plugin name and component name.

Double underscore is chosen because it satisfies the regex
``^[A-Za-z][A-Za-z0-9_-]*$`` shared by all 5 extension subsystems
(MCP / Skills / Commands / Bundles / Hooks). The colon shown in
boundary doc decisions/24 D27.3 is a *display* convention only —
actual stored names use this separator to avoid touching the
underlying regexes (zero-diff invariant compliance).
"""


def namespaced(plugin_name: str, component_name: str) -> str:
    """Return ``<plugin>__<component>`` — the storage key for a
    namespaced component."""
    return f"{plugin_name}{NAMESPACE_SEPARATOR}{component_name}"


# --------------------------------------------------------------------------- #
# LayeredStore — plugin catalog overlay on a base store                        #
# --------------------------------------------------------------------------- #


T = TypeVar("T")


class _BaseStore(Protocol[T]):
    """Minimal store shape :class:`LayeredStore` wraps.

    Matches :class:`openharness.skills.store.SkillStore`,
    :class:`openharness.commands.store.CommandStore`, and
    :class:`openharness.bundles.store.BundleStore` —— all three
    expose ``get(name)`` + ``discover()``. The Protocol exists so
    LayeredStore can wrap any of them under mypy --strict without
    importing each concrete class.
    """

    def get(self, name: str) -> T | None: ...

    def discover(self) -> dict[str, T]: ...


class LayeredStore(Generic[T]):
    """A plugin catalog overlaid on top of a base markdown store.

    ``get(name)`` checks plugin catalog first (namespaced names), falls
    back to the base store. ``discover()`` returns ``base + plugin``
    (union), with plugin entries winning on collision (should never
    happen given namespace prefix, but explicit for safety).

    Used at cli.py bootstrap (P9-T3) to wrap each existing store with
    the plugin contributions, so the rest of the codebase keeps using
    the same store interface — zero-diff invariant on the consumer
    side (engine / permissions / hooks).
    """

    def __init__(
        self,
        *,
        base: _BaseStore[T],
        plugin_catalog: dict[str, T],
    ) -> None:
        self._base = base
        self._plugin = dict(plugin_catalog)  # defensive copy

    def get(self, name: str) -> T | None:
        if name in self._plugin:
            return self._plugin[name]
        return self._base.get(name)

    def discover(self) -> dict[str, T]:
        merged = self._base.discover()
        merged.update(self._plugin)
        return merged


# --------------------------------------------------------------------------- #
# Loaded plugin catalogs (output of fan_out)                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LoadedPluginCatalogs:
    """Result of :meth:`PluginLoader.fan_out`.

    Each component-type catalog uses ``<plugin>__<name>`` keys, ready
    to be wrapped in :class:`LayeredStore` (for the 3 markdown domains)
    or merged into the existing catalog dict (for hooks) or appended
    to the MCP server list (for MCP).
    """

    commands: dict[str, Command] = field(default_factory=dict)
    skills: dict[str, Skill] = field(default_factory=dict)
    bundles: dict[str, Bundle] = field(default_factory=dict)
    hooks: dict[str, HookSpec] = field(default_factory=dict)
    mcp_servers: tuple[McpServerConfig, ...] = ()


# --------------------------------------------------------------------------- #
# Module loader (hooks)                                                       #
# --------------------------------------------------------------------------- #


def _load_plugin_module(path: Path, plugin_name: str) -> object | None:
    """Import a Python module from filesystem path, sha8-namespaced.

    Same shape as ``bundles/hook_plugins.py:_default_module_loader``:
    sha8 of absolute path → unique module name to avoid collisions
    when two plugins ship modules with the same filename. The module
    is stashed in ``sys.modules`` only during ``exec_module`` so the
    module's own ``from foo import bar`` statements resolve correctly,
    then popped out.

    Returns the loaded module object, or ``None`` on any error
    (warning logged). Skip-not-fail discipline so one bad hook module
    doesn't block other plugins.

    Deliberately duplicated from ``bundles/hook_plugins.py`` instead
    of imported — keeps ``bundles/`` at zero diff per the Phase 9
    cross-cutting invariant.
    """
    try:
        absolute = path.resolve()
    except (
        OSError
    ) as exc:  # pragma: no cover - defensive: requires symlink loop / unreadable parent
        _logger.warning(
            "plugin_hook_path_failed",
            plugin=plugin_name,
            path=str(path),
            error=str(exc),
        )
        return None

    if not absolute.is_file():
        _logger.warning(
            "plugin_hook_module_not_found",
            plugin=plugin_name,
            path=str(absolute),
        )
        return None

    sha8 = hashlib.sha256(str(absolute).encode()).hexdigest()[:8]
    module_name = f"openharness._plugin_hook_{sha8}_{path.stem}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, absolute)
    except (
        OSError,
        ValueError,
    ) as exc:  # pragma: no cover - defensive: spec_from_file_location rarely raises on valid abs path
        _logger.warning(
            "plugin_hook_spec_failed",
            plugin=plugin_name,
            path=str(absolute),
            error=str(exc),
        )
        return None
    if (
        spec is None or spec.loader is None
    ):  # pragma: no cover - defensive: importlib creates valid spec for extant file
        _logger.warning(
            "plugin_hook_spec_invalid",
            plugin=plugin_name,
            path=str(absolute),
        )
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        _logger.warning(
            "plugin_hook_exec_failed",
            plugin=plugin_name,
            path=str(absolute),
            error=str(exc),
        )
        sys.modules.pop(module_name, None)
        return None

    return module


def _manifest_source_path_for_format(manifest: PluginManifest, source_format: str) -> Path:
    """Return the filesystem path of the manifest file that defined
    ``manifest``, given its source format.

    Used by :meth:`PluginLoader.discover_with_format` to build a
    cross-format-aware conflict error message: CC plugins live at
    ``<dir>/.claude-plugin/plugin.json``, OH plugins at
    ``<dir>/manifest.yaml``.
    """
    if source_format == "cc":
        return manifest.source_path / ".claude-plugin" / "plugin.json"
    return manifest.source_path / "manifest.yaml"


def _module_path_for_hook(plugin: PluginManifest, hook: HookRef) -> Path:
    """Resolve ``module: my_pkg.hooks`` against the plugin's directory.

    Dotted-path semantics:``my_pkg.hooks`` → ``<source>/my_pkg/hooks.py``.
    A leading single dot (``.hooks``) → ``<source>/hooks.py`` (i.e.
    relative to the plugin directory itself).
    """
    parts = hook.module.lstrip(".").split(".")
    return plugin.source_path.joinpath(*parts).with_suffix(".py")


# --------------------------------------------------------------------------- #
# PluginLoader                                                                #
# --------------------------------------------------------------------------- #


class PluginLoader:
    """Discover plugins under a directory + fan out into existing stores.

    Constructor:``PluginLoader(plugins_dir=Path.home() / ".openharness" / "plugins")``.

    The two-phase pattern (``discover`` then ``fan_out``) mirrors the
    bootstrap split:CLI calls ``discover()`` early to learn what's
    available + present in ``oh plugins list``, then calls ``fan_out()``
    only when ``Settings.enable_plugins`` is True to actually install
    components.

    Both methods are fault-tolerant per-plugin:one bad plugin (malformed
    manifest, missing files, hook import failure) does not block other
    plugins from loading. The only hard-fail case is duplicate plugin
    names — raised eagerly at ``discover()`` so the user sees the
    conflict + both manifest paths before any partial registration.
    """

    def __init__(self, plugins_dir: Path) -> None:
        self.plugins_dir = plugins_dir

    def discover(self) -> dict[str, PluginManifest]:
        """Scan ``<plugins_dir>/`` and return name → manifest.

        Per Phase 19 D39.1, each subdirectory is dispatched to either
        the CC parser (``.claude-plugin/plugin.json`` present) or the
        Phase 9 OH parser (``manifest.yaml`` present). When both markers
        exist the CC parser wins and a ``plugin_dual_manifest`` WARN is
        emitted (D39.6). Subdirectories with neither marker are
        silently skipped (D39.4).

        Raises :class:`PluginConflictError` if two directories — of
        either format — declare the same plugin name.

        Thin wrapper around :meth:`discover_with_format`; drops the
        format tag so callers that only need ``PluginManifest`` (the
        bulk of Phase 9 + CLI fan-out paths) stay byte-identical.
        ``oh plugins list`` (P19-T3) consumes the with-format variant.
        """
        return {name: manifest for name, (manifest, _fmt) in self.discover_with_format().items()}

    def discover_with_format(
        self,
    ) -> dict[str, tuple[PluginManifest, str]]:
        """Same scan as :meth:`discover` but also returns the source
        format (``"cc"`` or ``"oh"``) per plugin.

        Emits one ``plugin_discovered`` INFO event (D39.8) per
        successful parse and one ``plugin_dual_manifest`` WARN
        (D39.6) per subdirectory containing both marker files.
        """
        if not self.plugins_dir.is_dir():
            return {}

        results: dict[str, tuple[PluginManifest, str]] = {}
        for entry in sorted(self.plugins_dir.iterdir()):
            if not entry.is_dir():
                continue

            cc_marker = entry / ".claude-plugin" / "plugin.json"
            oh_marker = entry / "manifest.yaml"
            has_cc = cc_marker.is_file()
            has_oh = oh_marker.is_file()

            if not has_cc and not has_oh:
                # D39.4: user's plugin dir may contain unrelated content
                # (README, .git, drafts). Silent skip.
                continue

            if has_cc and has_oh:
                # D39.6: dual marker collision — CC wins. WARN identifies
                # which file got picked + which was ignored so a user
                # who happens to maintain both during a migration can
                # decide whether to delete one.
                _logger.warning(
                    "plugin_dual_manifest",
                    plugin_dir=str(entry),
                    picked="cc",
                    ignored="manifest.yaml",
                )

            if has_cc:
                manifest = parse_cc_plugin(entry)
                source_format = "cc"
                manifest_source = cc_marker
            else:
                manifest = parse_manifest(oh_marker)
                source_format = "oh"
                manifest_source = oh_marker

            if manifest is None:
                # parse_* already logged the specific failure (missing
                # field / malformed JSON-or-YAML / invalid name regex).
                continue

            if manifest.name in results:
                existing_manifest, existing_format = results[manifest.name]
                existing_source = _manifest_source_path_for_format(
                    existing_manifest, existing_format
                )
                raise PluginConflictError(
                    f"two plugins claim the same name {manifest.name!r}: "
                    f"{existing_source} and {manifest_source}"
                )
            results[manifest.name] = (manifest, source_format)

            _logger.info(
                "plugin_discovered",
                plugin_name=manifest.name,
                version=manifest.version,
                format=source_format,
                source_path=str(manifest.source_path),
                skills_count=len(manifest.skills),
                mcp_servers_count=len(manifest.mcp_servers),
            )

        return results

    def fan_out(
        self,
        manifests: dict[str, PluginManifest],
    ) -> LoadedPluginCatalogs:
        """Load each manifest's components, namespace them, accumulate.

        Per-component failures are isolated:a bad command file logs a
        warning + is skipped, other components of the same plugin still
        load. The returned catalogs contain only successfully-loaded
        components.
        """
        commands: dict[str, Command] = {}
        skills: dict[str, Skill] = {}
        bundles: dict[str, Bundle] = {}
        hooks: dict[str, HookSpec] = {}
        mcp_servers: list[McpServerConfig] = []

        for plugin_name, manifest in manifests.items():
            self._fan_out_commands(manifest, plugin_name, commands)
            self._fan_out_skills(manifest, plugin_name, skills)
            self._fan_out_bundles(manifest, plugin_name, bundles)
            self._fan_out_hooks(manifest, plugin_name, hooks)
            self._fan_out_mcp_servers(manifest, plugin_name, mcp_servers)

        return LoadedPluginCatalogs(
            commands=commands,
            skills=skills,
            bundles=bundles,
            hooks=hooks,
            mcp_servers=tuple(mcp_servers),
        )

    # ---- per-component fan-out helpers ---- #

    def _fan_out_commands(
        self,
        manifest: PluginManifest,
        plugin_name: str,
        out: dict[str, Command],
    ) -> None:
        for ref in manifest.commands:
            path = manifest.source_path / ref.file
            cmd = parse_command(path)
            if cmd is None:
                _logger.warning(
                    "plugin_component_load_failed",
                    plugin=plugin_name,
                    component_type="command",
                    file=str(path),
                )
                continue
            ns_name = namespaced(plugin_name, cmd.name)
            try:
                ns_cmd = replace(cmd, name=ns_name)
            except (
                ValueError
            ) as exc:  # pragma: no cover - defensive: namespaced name provably satisfies regex
                _logger.warning(
                    "plugin_component_namespace_failed",
                    plugin=plugin_name,
                    component_type="command",
                    original=cmd.name,
                    namespaced=ns_name,
                    error=str(exc),
                )
                continue
            out[ns_name] = ns_cmd

    def _fan_out_skills(
        self,
        manifest: PluginManifest,
        plugin_name: str,
        out: dict[str, Skill],
    ) -> None:
        for ref in manifest.skills:
            path = manifest.source_path / ref.file
            skill = parse_skill(path)
            if skill is None:
                _logger.warning(
                    "plugin_component_load_failed",
                    plugin=plugin_name,
                    component_type="skill",
                    file=str(path),
                )
                continue
            ns_name = namespaced(plugin_name, skill.name)
            try:
                ns_skill = replace(skill, name=ns_name)
            except (
                ValueError
            ) as exc:  # pragma: no cover - defensive: namespaced name provably satisfies regex
                _logger.warning(
                    "plugin_component_namespace_failed",
                    plugin=plugin_name,
                    component_type="skill",
                    original=skill.name,
                    namespaced=ns_name,
                    error=str(exc),
                )
                continue
            out[ns_name] = ns_skill

    def _fan_out_bundles(
        self,
        manifest: PluginManifest,
        plugin_name: str,
        out: dict[str, Bundle],
    ) -> None:
        # Lazy import to avoid the bundles ↔ plugins cycle at import
        # time; bundles already pulls in commands/skills indirectly.
        from openharness.bundles.model import parse_bundle

        # D27.7 + P9-T3 review: a bundle inside the same plugin that
        # references this plugin's own hooks by their unprefixed name
        # (e.g. ``hook_names: [audit]``) must be rewritten to the
        # namespaced form, because that's the key under which
        # :meth:`_fan_out_hooks` registered the hook in the eventual
        # plugin_hook_catalog. Same shape as how we already namespace
        # ``bundle.name``: the plugin author writes naturally;fan_out
        # applies the prefix. Unprefixed names that DON'T belong to
        # this plugin pass through unchanged so they can resolve
        # against ``BUILTIN_HOOKS`` or filesystem-discovered hook
        # plugins at apply_bundle time.
        own_hook_names = {h.name for h in manifest.hooks}

        for ref in manifest.bundles:
            path = manifest.source_path / ref.file
            bundle = parse_bundle(path)
            if bundle is None:
                _logger.warning(
                    "plugin_component_load_failed",
                    plugin=plugin_name,
                    component_type="bundle",
                    file=str(path),
                )
                continue
            ns_name = namespaced(plugin_name, bundle.name)
            ns_hook_names = tuple(
                namespaced(plugin_name, h) if h in own_hook_names else h for h in bundle.hook_names
            )
            try:
                ns_bundle = replace(
                    bundle,
                    name=ns_name,
                    hook_names=ns_hook_names,
                )
            except (
                ValueError
            ) as exc:  # pragma: no cover - defensive: namespaced name provably satisfies regex
                _logger.warning(
                    "plugin_component_namespace_failed",
                    plugin=plugin_name,
                    component_type="bundle",
                    original=bundle.name,
                    namespaced=ns_name,
                    error=str(exc),
                )
                continue
            out[ns_name] = ns_bundle

    def _fan_out_hooks(
        self,
        manifest: PluginManifest,
        plugin_name: str,
        out: dict[str, HookSpec],
    ) -> None:
        for hook_ref in manifest.hooks:
            module_path = _module_path_for_hook(manifest, hook_ref)
            module = _load_plugin_module(module_path, plugin_name)
            if module is None:
                continue  # error already logged
            hook_fn = getattr(module, hook_ref.name, None)
            if not isinstance(hook_fn, HookSpec):
                # Hook function must be decorated with ``@hook_spec``
                # from bundles/hook_plugins.py — that decorator returns
                # a HookSpec wrapping the function + event.
                _logger.warning(
                    "plugin_hook_not_a_hookspec",
                    plugin=plugin_name,
                    module=hook_ref.module,
                    name=hook_ref.name,
                    actual_type=type(hook_fn).__name__,
                )
                continue
            ns_name = namespaced(plugin_name, hook_ref.name)
            out[ns_name] = hook_fn

    def _fan_out_mcp_servers(
        self,
        manifest: PluginManifest,
        plugin_name: str,
        out: list[McpServerConfig],
    ) -> None:
        for srv in manifest.mcp_servers:
            ns_name = namespaced(plugin_name, srv.name)
            try:
                ns_srv = McpServerConfig(
                    name=ns_name,
                    command=srv.command,
                    env=srv.env,
                )
            except ValueError as exc:  # pragma: no cover - defensive: namespaced name provably satisfies McpServerConfig regex
                _logger.warning(
                    "plugin_component_namespace_failed",
                    plugin=plugin_name,
                    component_type="mcp_server",
                    original=srv.name,
                    namespaced=ns_name,
                    error=str(exc),
                )
                continue
            out.append(ns_srv)
