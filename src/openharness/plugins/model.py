"""``PluginManifest`` + ``parse_manifest`` — P9-T1 (decisions/24).

Per ``decisions/24`` D27.1: a plugin is a directory at
``~/.openharness/plugins/<name>/`` containing a ``manifest.yaml`` that
declares the plugin's identity and the 5 component types it provides.

Required fields: ``name`` (regex ``^[a-z][a-z0-9-]*$`` — lowercase
kebab-case, the user-facing namespace prefix per D27.3),
``version`` (semver string), ``description`` (non-empty).

Five optional component lists:

::

    commands:                     # Phase 5b
      - file: commands/my-cmd.md
    skills:                       # Phase 5c
      - file: skills/my-skill.md
    bundles:                      # Phase 5d
      - file: bundles/my-mode.md
    hooks:                        # Phase 5e/5f
      - module: my_plugin.hooks
        name: my_hook_function
    mcp_servers:                  # Phase 5
      - name: GitHub
        command: ["npx", "-y", "@..."]
        env:
          GITHUB_TOKEN: ${GITHUB_TOKEN}

**Fault-tolerant parsing**: :func:`parse_manifest` returns ``None`` and
emits a warning log on any error (file missing / YAML parse failure /
required field missing / invalid name regex / bad component shape).
One bad manifest must never block other plugins from loading — see
decisions/24 §"Out of scope" / §"Acceptance".

**Env-var interpolation**: ``${VAR}`` references inside
``mcp_servers[].env[]`` values resolve against ``os.environ`` at parse
time. Missing variables cause that *specific* mcp_server to be skipped
(warning logged); other components of the plugin load normally. This
is deliberately more graceful than "skip the whole plugin" — a
plugin with 3 MCP servers, one having a missing token, still loads
its other 2 servers + its commands/skills/bundles/hooks.

**Namespacing**: this module does NOT apply ``<plugin>:<component>``
namespace prefixes. PluginManifest stores raw values from YAML; the
plugin-prefix wrapping happens at fan-out time in P9-T2
:class:`PluginLoader`. Keeping T1 namespace-free means T2 can pick a
separator that satisfies all 5 subsystems' name regexes without
revisiting this layer.

**P8 reuse**: this module uses ``pyyaml`` directly (manifest is a
pure YAML file, not markdown+frontmatter). ``markdown_store`` is
reused later in P9-T2 ``PluginLoader.fan_out`` to parse each plugin's
commands/skills/bundles markdown files via the existing
``parse_command`` / ``parse_skill`` / ``parse_bundle`` entry points.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from openharness.mcp.config import McpServerConfig
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("plugins")

# Plugin name regex (D27.1 + D27.3): lowercase kebab-case. Distinct
# from :data:`openharness.markdown_store.NAME_PATTERN` (which allows
# uppercase + underscore) — plugins are user-facing distribution units
# that benefit from URL/CLI-friendly kebab-case.
PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# Env-var interpolation: matches ``${VAR}`` and ``${VAR_WITH_UNDERSCORES}``.
# Strict pattern (no ``${}`` with default-value syntax) — Phase 9 keeps
# interpolation simple. Bash-style ``${VAR:-default}`` defers.
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Top-level fields we recognize. Anything else logs a forward-compat
# warning but does NOT block loading (D27.1 final paragraph).
_KNOWN_TOP_LEVEL_FIELDS = frozenset(
    {
        # Required identity
        "name",
        "version",
        "description",
        # Optional metadata
        "author",
        "license",
        "homepage",
        "keywords",
        "openharness_version_min",
        "dependencies",
        # 5 component types
        "commands",
        "skills",
        "bundles",
        "hooks",
        "mcp_servers",
    }
)


@dataclass(frozen=True)
class ComponentRef:
    """Reference to a markdown component file within a plugin directory.

    ``file`` is a path relative to the plugin's directory
    (:attr:`PluginManifest.source_path`). T2 ``PluginLoader.fan_out``
    resolves ``source_path / file`` and hands the resulting absolute
    path to the appropriate ``parse_X`` function.
    """

    file: str


@dataclass(frozen=True)
class HookRef:
    """Reference to a hook function exported by a Python module in a plugin.

    ``module`` is a dotted-path or file-relative-path identifier
    (e.g. ``"my_plugin.hooks"`` resolves to
    ``<source_path>/my_plugin/hooks.py``). T2 ``PluginLoader`` reuses
    Phase 5f's ``_load_module_from_path`` mechanism (sha8-prefixed
    module name pattern) to import safely.

    ``name`` is the function name within the loaded module — must be
    decorated with :func:`openharness.bundles.hook_plugins.hook_spec`.
    """

    module: str
    name: str


@dataclass(frozen=True)
class PluginManifest:
    """One plugin's manifest, parsed from ``<plugin-dir>/manifest.yaml``.

    Constructed by :func:`parse_manifest`. The dataclass itself trusts
    its inputs — ``__post_init__`` re-asserts critical identity
    invariants only — so tests can build fixtures programmatically
    without round-tripping through YAML.
    """

    # Required identity
    name: str
    version: str
    description: str
    source_path: Path  # The plugin directory containing manifest.yaml

    # Optional metadata
    author: str | None = None
    license: str | None = None
    homepage: str | None = None
    keywords: tuple[str, ...] = ()
    openharness_version_min: str | None = None
    # Phase 9: warning-only, no resolver. Phase 10+ may add semver matching.
    dependencies: tuple[str, ...] = ()

    # 5 optional component lists
    commands: tuple[ComponentRef, ...] = ()
    skills: tuple[ComponentRef, ...] = ()
    bundles: tuple[ComponentRef, ...] = ()
    hooks: tuple[HookRef, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()

    def __post_init__(self) -> None:
        if not PLUGIN_NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid plugin name {self.name!r}: must match "
                f"{PLUGIN_NAME_PATTERN.pattern} (lowercase kebab-case, "
                "starts with letter)"
            )
        if not self.version.strip():
            raise ValueError(f"plugin {self.name!r}: version must be non-empty")
        if not self.description.strip():
            raise ValueError(f"plugin {self.name!r}: description must be non-empty")


def parse_manifest(path: Path) -> PluginManifest | None:
    """Read a plugin manifest YAML file and return a :class:`PluginManifest`.

    Returns ``None`` and emits a structured warning log on any error.
    Never raises — fault-tolerant by design so the discovery loop's
    ``for plugin in discover(): ...`` pattern naturally skips bad
    plugins.

    Env-var interpolation (``${VAR}``) is applied to
    ``mcp_servers[].env[]`` values. Missing variables cause that
    specific server to be skipped (warning logged); other components
    of the plugin still load.
    """
    if not path.is_file():
        _logger.warning("plugin_manifest_not_a_file", source_path=str(path))
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning(
            "plugin_manifest_read_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None

    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _logger.warning(
            "plugin_manifest_yaml_parse_failed",
            source_path=str(path),
            error=str(exc),
        )
        return None

    if not isinstance(parsed, dict):
        _logger.warning(
            "plugin_manifest_not_a_mapping",
            source_path=str(path),
            actual_type=type(parsed).__name__,
        )
        return None

    # Forward-compat: warn on unknown top-level fields but don't reject.
    unknown = set(parsed.keys()) - _KNOWN_TOP_LEVEL_FIELDS
    if unknown:
        _logger.warning(
            "plugin_manifest_unknown_fields",
            source_path=str(path),
            fields=sorted(unknown),
        )

    # Required identity fields.
    name = parsed.get("name")
    raw_version = parsed.get("version")
    description = parsed.get("description")

    if not isinstance(name, str) or not name:
        _logger.warning("plugin_manifest_missing_name", source_path=str(path))
        return None
    # YAML may parse ``version: 1.0`` as float; ``version: "1.0"`` as str.
    # Accept both; coerce to str for storage.
    if not isinstance(raw_version, (str, int, float)):
        _logger.warning(
            "plugin_manifest_missing_version",
            source_path=str(path),
            name=name,
        )
        return None
    version_str = str(raw_version)
    if not version_str.strip():
        _logger.warning(
            "plugin_manifest_empty_version",
            source_path=str(path),
            name=name,
        )
        return None
    if not isinstance(description, str) or not description.strip():
        _logger.warning(
            "plugin_manifest_missing_description",
            source_path=str(path),
            name=name,
        )
        return None

    # Optional metadata.
    author = parsed.get("author") if isinstance(parsed.get("author"), str) else None
    license_ = parsed.get("license") if isinstance(parsed.get("license"), str) else None
    homepage = parsed.get("homepage") if isinstance(parsed.get("homepage"), str) else None
    raw_keywords = parsed.get("keywords")
    keywords_tuple: tuple[str, ...] = (
        tuple(str(k) for k in raw_keywords) if isinstance(raw_keywords, list) else ()
    )
    openharness_version_min = (
        parsed.get("openharness_version_min")
        if isinstance(parsed.get("openharness_version_min"), str)
        else None
    )
    raw_dependencies = parsed.get("dependencies")
    dependencies_tuple: tuple[str, ...] = (
        tuple(str(d) for d in raw_dependencies) if isinstance(raw_dependencies, list) else ()
    )

    # 5 component lists.
    commands = _parse_component_refs(parsed.get("commands"), "commands", path, name)
    skills = _parse_component_refs(parsed.get("skills"), "skills", path, name)
    bundles = _parse_component_refs(parsed.get("bundles"), "bundles", path, name)
    hooks = _parse_hook_refs(parsed.get("hooks"), path, name)
    mcp_servers = _parse_mcp_servers(parsed.get("mcp_servers"), path, name)

    try:
        return PluginManifest(
            name=name,
            version=version_str,
            description=description,
            source_path=path.parent,
            author=author,
            license=license_,
            homepage=homepage,
            keywords=keywords_tuple,
            openharness_version_min=openharness_version_min,
            dependencies=dependencies_tuple,
            commands=commands,
            skills=skills,
            bundles=bundles,
            hooks=hooks,
            mcp_servers=mcp_servers,
        )
    except ValueError as exc:
        # ``__post_init__`` rejected name regex / empty description / etc.
        # Log + skip, never crash the discovery loop.
        _logger.warning(
            "plugin_manifest_validation_failed",
            source_path=str(path),
            name=name,
            error=str(exc),
        )
        return None


def _interpolate_env(value: str) -> tuple[str, list[str]]:
    """Replace ``${VAR}`` substrings with values from ``os.environ``.

    Returns ``(resolved_string, missing_var_names)``. Missing variables
    are NOT substituted (the literal ``${VAR}`` remains in the output)
    but their names are returned so the caller can decide what to do.
    """
    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        replacement = os.environ.get(var_name)
        if replacement is None:
            missing.append(var_name)
            return match.group(0)  # Leave ``${VAR}`` as-is
        return replacement

    resolved = _ENV_VAR_PATTERN.sub(_replace, value)
    return resolved, missing


def _parse_component_refs(
    raw: Any,
    field_name: str,
    manifest_path: Path,
    plugin_name: str,
) -> tuple[ComponentRef, ...]:
    """Parse ``[{file: relpath}, ...]`` into ``tuple[ComponentRef, ...]``.

    Tolerant: malformed entries are logged + skipped; valid entries
    still flow through. Returns ``()`` if the field is absent / null.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _logger.warning(
            "plugin_manifest_component_not_a_list",
            source_path=str(manifest_path),
            name=plugin_name,
            field=field_name,
            actual_type=type(raw).__name__,
        )
        return ()
    refs: list[ComponentRef] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            _logger.warning(
                "plugin_manifest_component_entry_not_a_mapping",
                source_path=str(manifest_path),
                name=plugin_name,
                field=field_name,
                index=i,
            )
            continue
        file_val = item.get("file")
        if not isinstance(file_val, str) or not file_val:
            _logger.warning(
                "plugin_manifest_component_missing_file",
                source_path=str(manifest_path),
                name=plugin_name,
                field=field_name,
                index=i,
            )
            continue
        refs.append(ComponentRef(file=file_val))
    return tuple(refs)


def _parse_hook_refs(
    raw: Any,
    manifest_path: Path,
    plugin_name: str,
) -> tuple[HookRef, ...]:
    """Parse ``[{module: ..., name: ...}, ...]`` into ``tuple[HookRef, ...]``."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _logger.warning(
            "plugin_manifest_hooks_not_a_list",
            source_path=str(manifest_path),
            name=plugin_name,
            actual_type=type(raw).__name__,
        )
        return ()
    refs: list[HookRef] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            _logger.warning(
                "plugin_manifest_hook_entry_not_a_mapping",
                source_path=str(manifest_path),
                name=plugin_name,
                index=i,
            )
            continue
        module = item.get("module")
        hook_name = item.get("name")
        if not isinstance(module, str) or not module:
            _logger.warning(
                "plugin_manifest_hook_missing_module",
                source_path=str(manifest_path),
                name=plugin_name,
                index=i,
            )
            continue
        if not isinstance(hook_name, str) or not hook_name:
            _logger.warning(
                "plugin_manifest_hook_missing_name",
                source_path=str(manifest_path),
                name=plugin_name,
                index=i,
            )
            continue
        refs.append(HookRef(module=module, name=hook_name))
    return tuple(refs)


def _parse_mcp_servers(
    raw: Any,
    manifest_path: Path,
    plugin_name: str,
) -> tuple[McpServerConfig, ...]:
    """Parse ``mcp_servers:`` list into ``tuple[McpServerConfig, ...]``.

    Env-var interpolation applies to each ``env`` dict's values. A
    server with missing env vars is skipped (warning); others load
    normally. Server names are kept as-is (no plugin-prefix);
    namespacing happens at fan-out time in T2.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _logger.warning(
            "plugin_manifest_mcp_servers_not_a_list",
            source_path=str(manifest_path),
            name=plugin_name,
            actual_type=type(raw).__name__,
        )
        return ()
    servers: list[McpServerConfig] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            _logger.warning(
                "plugin_manifest_mcp_entry_not_a_mapping",
                source_path=str(manifest_path),
                name=plugin_name,
                index=i,
            )
            continue
        srv_name = item.get("name")
        raw_command = item.get("command")
        raw_env = item.get("env") or {}

        if not isinstance(srv_name, str) or not srv_name:
            _logger.warning(
                "plugin_manifest_mcp_missing_name",
                source_path=str(manifest_path),
                name=plugin_name,
                index=i,
            )
            continue
        if not isinstance(raw_command, list) or not raw_command:
            _logger.warning(
                "plugin_manifest_mcp_missing_command",
                source_path=str(manifest_path),
                name=plugin_name,
                index=i,
                server=srv_name,
            )
            continue
        if not isinstance(raw_env, dict):
            _logger.warning(
                "plugin_manifest_mcp_env_not_a_mapping",
                source_path=str(manifest_path),
                name=plugin_name,
                server=srv_name,
            )
            continue

        # Env-var interpolation across all env values.
        resolved_env: dict[str, str] = {}
        all_missing: list[str] = []
        for env_key, env_val in raw_env.items():
            env_val_str = str(env_val) if not isinstance(env_val, str) else env_val
            resolved, missing = _interpolate_env(env_val_str)
            all_missing.extend(missing)
            resolved_env[str(env_key)] = resolved

        if all_missing:
            _logger.warning(
                "plugin_manifest_mcp_missing_env_vars",
                source_path=str(manifest_path),
                name=plugin_name,
                server=srv_name,
                missing=sorted(set(all_missing)),
            )
            continue  # Skip this server; keep other components

        try:
            servers.append(
                McpServerConfig(
                    name=srv_name,
                    command=tuple(str(c) for c in raw_command),
                    env=resolved_env,
                )
            )
        except ValueError as exc:
            # McpServerConfig's own validation (name regex, command non-empty).
            _logger.warning(
                "plugin_manifest_mcp_validation_failed",
                source_path=str(manifest_path),
                name=plugin_name,
                server=srv_name,
                error=str(exc),
            )
            continue

    return tuple(servers)


# ---------------------------------------------------------------------------
# Phase 19 — CC plugin format parser (D39.1 / D39.2 / D39.9)
# ---------------------------------------------------------------------------


_CC_PLUGIN_MANIFEST_RELPATH = (".claude-plugin", "plugin.json")


def parse_cc_plugin(plugin_dir: Path) -> PluginManifest | None:
    """Parse a CC-format plugin directory into a :class:`PluginManifest`.

    CC plugin shape (per
    ``finance-skills/.../plugins/<name>/.claude-plugin/plugin.json``):

    .. code-block:: json

        {
          "name": "credit-report-reviewer",
          "version": "0.1.0",
          "description": "...",
          "author": {"name": "Risk Engineering @ MyBank"}
        }

    Plus optional ``<plugin_dir>/skills/<skill-name>/SKILL.md`` directory
    tree — discovered automatically by :func:`_scan_cc_skills_dir`.

    Per D39.2 the output reuses the same :class:`PluginManifest` dataclass
    as OH-format plugins; downstream :class:`PluginLoader.fan_out` does
    not distinguish source format.

    Per D39.9 (reversed D39.5): CC ``.mcp.json`` is **silently ignored**
    in M2 — the returned manifest's ``mcp_servers`` is always ``()``.
    OH's MCP layer is stdio-only (D15.1) and CC ``.mcp.json`` uses
    HTTP + OAuth2 transport; mapping requires extending the MCP layer
    in a separate phase.

    Returns ``None`` on any unrecoverable error (manifest missing,
    JSON malformed, required field missing). One bad plugin must not
    block the discovery loop — same fault-tolerance model as
    :func:`parse_manifest`.
    """
    manifest_path = plugin_dir.joinpath(*_CC_PLUGIN_MANIFEST_RELPATH)
    if not manifest_path.is_file():
        # Caller (PluginLoader) is responsible for routing; if we land here
        # it's because the discover() dispatch saw a marker but the file
        # disappeared between probe and read.
        _logger.warning("plugin_cc_manifest_not_a_file", source_path=str(manifest_path))
        return None

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning(
            "plugin_cc_manifest_read_failed",
            source_path=str(manifest_path),
            error=str(exc),
        )
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        _logger.warning(
            "plugin_cc_manifest_json_parse_failed",
            source_path=str(manifest_path),
            error=str(exc),
        )
        return None

    if not isinstance(parsed, dict):
        _logger.warning(
            "plugin_cc_manifest_not_a_mapping",
            source_path=str(manifest_path),
            actual_type=type(parsed).__name__,
        )
        return None

    name = parsed.get("name")
    raw_version = parsed.get("version")
    description = parsed.get("description")

    if not isinstance(name, str) or not name:
        _logger.warning("plugin_cc_manifest_missing_name", source_path=str(manifest_path))
        return None
    if not isinstance(raw_version, (str, int, float)):
        _logger.warning(
            "plugin_cc_manifest_missing_version",
            source_path=str(manifest_path),
            name=name,
        )
        return None
    version_str = str(raw_version)
    if not version_str.strip():
        _logger.warning(
            "plugin_cc_manifest_empty_version",
            source_path=str(manifest_path),
            name=name,
        )
        return None
    if not isinstance(description, str) or not description.strip():
        _logger.warning(
            "plugin_cc_manifest_missing_description",
            source_path=str(manifest_path),
            name=name,
        )
        return None

    # CC's ``author`` field is a nested ``{"name": "..."}`` object. OH's
    # ``PluginManifest.author`` is a flat ``str | None``. Extract
    # ``author.name`` when both the outer field is a dict and the inner
    # name is a non-empty string; everything else collapses to None.
    # (Non-dict ``author`` — e.g. a bare string — is treated as "absent"
    # rather than mapped to itself, to keep the projection rule explicit
    # and reversible if CC ever extends author to additional fields.)
    raw_author = parsed.get("author")
    author: str | None = None
    if isinstance(raw_author, dict):
        author_name = raw_author.get("name")
        if isinstance(author_name, str) and author_name.strip():
            author = author_name

    skills = _scan_cc_skills_dir(plugin_dir)

    try:
        return PluginManifest(
            name=name,
            version=version_str,
            description=description,
            source_path=plugin_dir,
            author=author,
            license=None,
            homepage=None,
            keywords=(),
            openharness_version_min=None,
            dependencies=(),
            commands=(),  # CC has no commands concept
            skills=skills,
            bundles=(),  # CC has no bundles concept
            hooks=(),  # CC hooks live in settings.json shell-command form (not M2)
            mcp_servers=(),  # D39.9: silently ignored regardless of .mcp.json presence
        )
    except ValueError as exc:
        # ``__post_init__`` rejected name regex / empty description / etc.
        # Log + skip, mirroring parse_manifest's late-validation path.
        _logger.warning(
            "plugin_cc_manifest_validation_failed",
            source_path=str(manifest_path),
            name=name,
            error=str(exc),
        )
        return None


def _scan_cc_skills_dir(plugin_dir: Path) -> tuple[ComponentRef, ...]:
    """Discover ``<plugin_dir>/skills/<skill-name>/SKILL.md`` files.

    Returns ``ComponentRef(file="skills/<name>/SKILL.md")`` tuples in
    alphabetical order by ``<name>``. ``skills/`` missing or empty →
    ``()``. Subdirectories without a ``SKILL.md`` are skipped silently
    (matches D39.4 "user's plugin dir may contain unrelated content"
    fault tolerance).

    The returned refs flow into Phase 9 ``PluginLoader._fan_out_skills``
    which calls ``parse_skill`` per ref — Phase 17 T1's parser already
    accepts CC frontmatter (multi-line ``description:`` block scalar).
    """
    skills_root = plugin_dir / "skills"
    if not skills_root.is_dir():
        return ()

    refs: list[ComponentRef] = []
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        refs.append(ComponentRef(file=f"skills/{entry.name}/SKILL.md"))
    return tuple(refs)
