"""Tests for ``PluginLoader`` + ``LayeredStore`` — P9-T2.

Coverage targets (per decisions/24 P9-T2 acceptance + tasks/phase-9-plan.md):

- ``discover()``: empty dir, single plugin, multiple plugins, duplicate-name
  raises PluginConflictError, skip dirs without manifest, skip invalid
  manifests (parse_manifest returns None).
- ``fan_out()``: all 5 component types load + get namespaced; one bad
  component skipped, rest load; hook module load failure skipped;
  malformed mcp server still validated against McpServerConfig regex.
- ``LayeredStore``: plugin-first get, fallback to base, discover() merges.
- ``namespaced()`` helper: simple concat with ``__`` separator.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from openharness.commands.store import EmptyCommandStore
from openharness.plugins.errors import PluginConflictError
from openharness.plugins.loader import (
    NAMESPACE_SEPARATOR,
    LayeredStore,
    LoadedPluginCatalogs,
    PluginLoader,
    namespaced,
)
from openharness.skills.store import EmptySkillStore

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Helpers — create realistic plugin directory layouts in tmp_path             #
# --------------------------------------------------------------------------- #


def _write_manifest(plugin_dir: Path, content: str) -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = plugin_dir / "manifest.yaml"
    manifest.write_text(textwrap.dedent(content), encoding="utf-8")
    return manifest


def _write_md(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# namespaced() helper                                                         #
# --------------------------------------------------------------------------- #


class TestNamespacedHelper:
    def test_simple_concat(self) -> None:
        assert namespaced("my-plugin", "deploy") == "my-plugin__deploy"

    def test_separator_is_double_underscore(self) -> None:
        assert NAMESPACE_SEPARATOR == "__"

    def test_namespaced_satisfies_name_pattern(self) -> None:
        """The namespaced result must match the regex used by all 5
        subsystems' name validation (``^[A-Za-z][A-Za-z0-9_-]*$``)."""
        import re

        pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
        # Plugin names use kebab-case, components can use snake or kebab.
        assert pattern.match(namespaced("my-plugin", "deploy"))
        assert pattern.match(namespaced("acme-tools", "react_testing"))
        assert pattern.match(namespaced("p1", "GitHub"))


# --------------------------------------------------------------------------- #
# LayeredStore                                                                #
# --------------------------------------------------------------------------- #


class TestLayeredStore:
    def test_get_falls_back_to_base(self, tmp_path: Path) -> None:
        """If plugin catalog doesn't have the name, base store wins."""

        # Construct a mock base store that returns "found" for ``my-cmd``.
        class _MockBase:
            def get(self, name: str) -> str | None:
                return "found" if name == "my-cmd" else None

            def discover(self) -> dict[str, str]:
                return {"my-cmd": "found"}

        store: LayeredStore[str] = LayeredStore(
            base=_MockBase(),
            plugin_catalog={"plugin__cmd": "ns-found"},
        )
        # Plugin name found
        assert store.get("plugin__cmd") == "ns-found"
        # Base name found via fallback
        assert store.get("my-cmd") == "found"
        # Neither
        assert store.get("nonexistent") is None

    def test_discover_merges(self) -> None:
        class _MockBase:
            def get(self, name: str) -> str | None:
                return {"a": "1"}.get(name)

            def discover(self) -> dict[str, str]:
                return {"a": "1"}

        store: LayeredStore[str] = LayeredStore(
            base=_MockBase(),
            plugin_catalog={"plugin__b": "2"},
        )
        assert store.discover() == {"a": "1", "plugin__b": "2"}

    def test_plugin_wins_on_collision(self) -> None:
        """Should never happen in practice (different namespaces), but
        verify plugin wins for safety."""

        class _MockBase:
            def get(self, name: str) -> str | None:
                return {"a": "base"}.get(name)

            def discover(self) -> dict[str, str]:
                return {"a": "base"}

        store: LayeredStore[str] = LayeredStore(
            base=_MockBase(),
            plugin_catalog={"a": "plugin"},
        )
        assert store.get("a") == "plugin"
        assert store.discover() == {"a": "plugin"}


# --------------------------------------------------------------------------- #
# PluginLoader.discover()                                                     #
# --------------------------------------------------------------------------- #


class TestPluginLoaderDiscover:
    def test_empty_dir(self, tmp_path: Path) -> None:
        loader = PluginLoader(tmp_path)
        assert loader.discover() == {}

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        loader = PluginLoader(tmp_path / "does-not-exist")
        assert loader.discover() == {}

    def test_single_plugin(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path / "my-plugin",
            """\
            name: my-plugin
            version: 0.1.0
            description: A test plugin
            """,
        )
        loader = PluginLoader(tmp_path)
        manifests = loader.discover()
        assert set(manifests.keys()) == {"my-plugin"}
        assert manifests["my-plugin"].version == "0.1.0"

    def test_multiple_plugins(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path / "plugin-a",
            "name: plugin-a\nversion: 1.0\ndescription: A\n",
        )
        _write_manifest(
            tmp_path / "plugin-b",
            "name: plugin-b\nversion: 2.0\ndescription: B\n",
        )
        loader = PluginLoader(tmp_path)
        manifests = loader.discover()
        assert set(manifests.keys()) == {"plugin-a", "plugin-b"}

    def test_skip_dirs_without_manifest(self, tmp_path: Path) -> None:
        # Real plugin
        _write_manifest(
            tmp_path / "real-plugin",
            "name: real-plugin\nversion: 1.0\ndescription: real\n",
        )
        # Empty subdir (no manifest.yaml)
        (tmp_path / "empty-dir").mkdir()
        # File at top level (not a dir)
        (tmp_path / "stray-file.txt").write_text("nothing", encoding="utf-8")
        loader = PluginLoader(tmp_path)
        manifests = loader.discover()
        assert set(manifests.keys()) == {"real-plugin"}

    def test_skip_invalid_manifest(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path / "broken",
            "this is: not valid YAML [unclosed",
        )
        _write_manifest(
            tmp_path / "valid",
            "name: valid\nversion: 1\ndescription: ok\n",
        )
        loader = PluginLoader(tmp_path)
        manifests = loader.discover()
        # Broken plugin skipped silently (warning logged by parse_manifest)
        assert set(manifests.keys()) == {"valid"}

    def test_duplicate_name_raises_conflict_error(self, tmp_path: Path) -> None:
        # Two different directories declaring the same plugin name.
        _write_manifest(
            tmp_path / "dir-1",
            "name: shared-name\nversion: 1.0\ndescription: first\n",
        )
        _write_manifest(
            tmp_path / "dir-2",
            "name: shared-name\nversion: 2.0\ndescription: second\n",
        )
        loader = PluginLoader(tmp_path)
        with pytest.raises(PluginConflictError, match="shared-name"):
            loader.discover()


# --------------------------------------------------------------------------- #
# PluginLoader.fan_out() — commands / skills / bundles                        #
# --------------------------------------------------------------------------- #


class TestFanOutMarkdownComponents:
    def test_commands_namespaced_and_loaded(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: cmd plugin
            commands:
              - file: commands/deploy.md
            """,
        )
        _write_md(
            plugin_dir / "commands" / "deploy.md",
            """\
            ---
            name: deploy
            description: Deploy the application
            ---

            Deploy now.
            """,
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert set(catalogs.commands.keys()) == {"my-plugin__deploy"}
        cmd = catalogs.commands["my-plugin__deploy"]
        assert cmd.name == "my-plugin__deploy"
        # Body preserved.
        assert "Deploy now" in cmd.body

    def test_skills_namespaced_and_loaded(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: skill plugin
            skills:
              - file: skills/k8s.md
            """,
        )
        _write_md(
            plugin_dir / "skills" / "k8s.md",
            """\
            ---
            name: k8s
            description: When working with Kubernetes
            ---

            Use `kubectl get pods` to list pods.
            """,
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert set(catalogs.skills.keys()) == {"my-plugin__k8s"}
        skill = catalogs.skills["my-plugin__k8s"]
        assert skill.name == "my-plugin__k8s"
        assert "kubectl" in skill.body

    def test_bundles_namespaced_and_loaded(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: bundle plugin
            bundles:
              - file: bundles/ops-mode.md
            """,
        )
        _write_md(
            plugin_dir / "bundles" / "ops-mode.md",
            """\
            ---
            name: ops-mode
            description: Operations mode
            ---

            You are an operations expert.
            """,
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert set(catalogs.bundles.keys()) == {"my-plugin__ops-mode"}

    def test_bad_command_file_skipped_other_components_load(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: mixed
            commands:
              - file: commands/broken.md
              - file: commands/good.md
            """,
        )
        # Broken: missing required frontmatter field
        _write_md(
            plugin_dir / "commands" / "broken.md",
            "no frontmatter here, just text",
        )
        _write_md(
            plugin_dir / "commands" / "good.md",
            """\
            ---
            name: good
            description: A working command
            ---

            ok
            """,
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        # Broken skipped; good loaded.
        assert set(catalogs.commands.keys()) == {"my-plugin__good"}

    def test_missing_component_file_skipped(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: m
            commands:
              - file: commands/missing.md
            """,
        )
        # Don't create commands/missing.md
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert catalogs.commands == {}


# --------------------------------------------------------------------------- #
# PluginLoader.fan_out() — bundle hook_names rewrite (D27.7 follow-up)        #
# --------------------------------------------------------------------------- #


class TestFanOutBundleHookRewrite:
    """A bundle inside a plugin that references the plugin's own hook by
    unprefixed name (``hooks: [audit]``) must be rewritten so that
    ``bundle.hook_names`` carries the namespaced form (``my-plugin__audit``)
    — the same key under which :meth:`_fan_out_hooks` registers the
    HookSpec. Unprefixed names that do NOT belong to this plugin pass
    through unchanged so they can resolve against ``BUILTIN_HOOKS`` or
    filesystem-discovered hook plugins at apply_bundle time.

    Rule per D27.7 + P9-T3 review: plugin fan_out is the right layer to
    apply namespace prefixes to plugin-declared component references;
    the plugin author writes naturally and lets the framework
    namespace what it owns.
    """

    def test_bundle_own_hook_reference_rewritten(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: bundle referencing plugin-own hook
            hooks:
              - module: h
                name: audit
            bundles:
              - file: bundles/ops.md
            """,
        )
        _write_md(
            plugin_dir / "bundles" / "ops.md",
            """\
            ---
            name: ops
            description: Ops mode bundle
            hooks:
              - audit
            ---

            You are an operations expert.
            """,
        )
        (plugin_dir / "h.py").write_text(
            textwrap.dedent(
                """\
                from openharness.bundles import hook_spec


                @hook_spec("PostToolUse")
                async def audit(_ctx):
                    return None
                """
            ),
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        bundle = catalogs.bundles["my-plugin__ops"]
        # The bundle's hook reference is rewritten to the same key
        # under which the hook was registered.
        assert bundle.hook_names == ("my-plugin__audit",)
        # Sanity: the hook IS in the catalog under that namespaced key.
        assert "my-plugin__audit" in catalogs.hooks

    def test_bundle_external_hook_name_passes_through(self, tmp_path: Path) -> None:
        """A hook name that doesn't appear in the plugin's manifest is
        left unchanged. apply_bundle_to_context will resolve it against
        BUILTIN_HOOKS / filesystem hooks at runtime."""
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: bundle referencing external hook
            bundles:
              - file: bundles/ops.md
            """,
        )
        _write_md(
            plugin_dir / "bundles" / "ops.md",
            """\
            ---
            name: ops
            description: Ops mode bundle
            hooks:
              - audit_log
              - deny_writes
            ---

            body
            """,
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        bundle = catalogs.bundles["my-plugin__ops"]
        # Plugin declares no hooks — both names pass through unchanged.
        assert bundle.hook_names == ("audit_log", "deny_writes")

    def test_bundle_mixed_own_and_external_hooks(self, tmp_path: Path) -> None:
        """Mix: ``audit`` is plugin-own → rewritten; ``deny_writes`` is
        external → passes through. Verifies the discrimination is
        per-name, not all-or-nothing."""
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: mixed hook refs
            hooks:
              - module: h
                name: audit
            bundles:
              - file: bundles/ops.md
            """,
        )
        _write_md(
            plugin_dir / "bundles" / "ops.md",
            """\
            ---
            name: ops
            description: Ops mode
            hooks:
              - audit
              - deny_writes
            ---

            body
            """,
        )
        (plugin_dir / "h.py").write_text(
            textwrap.dedent(
                """\
                from openharness.bundles import hook_spec


                @hook_spec("PostToolUse")
                async def audit(_ctx):
                    return None
                """
            ),
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        bundle = catalogs.bundles["my-plugin__ops"]
        # ``audit`` rewritten; ``deny_writes`` (external) untouched.
        assert bundle.hook_names == ("my-plugin__audit", "deny_writes")


# --------------------------------------------------------------------------- #
# PluginLoader.fan_out() — hooks                                              #
# --------------------------------------------------------------------------- #


class TestFanOutHooks:
    def test_valid_hook_module_loaded_and_namespaced(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: h
            hooks:
              - module: my_hooks
                name: audit
            """,
        )
        (plugin_dir / "my_hooks.py").write_text(
            textwrap.dedent(
                """\
                from openharness.bundles import hook_spec


                @hook_spec("PostToolUse")
                async def audit(_ctx):
                    return None
                """
            ),
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert set(catalogs.hooks.keys()) == {"my-plugin__audit"}
        # The HookSpec object is what got registered.
        spec = catalogs.hooks["my-plugin__audit"]
        assert spec.event == "PostToolUse"

    def test_nested_dotted_module_path(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: nested module
            hooks:
              - module: sub_pkg.hooks
                name: handler
            """,
        )
        (plugin_dir / "sub_pkg").mkdir()
        (plugin_dir / "sub_pkg" / "hooks.py").write_text(
            textwrap.dedent(
                """\
                from openharness.bundles import hook_spec


                @hook_spec("PreToolUse")
                async def handler(_ctx):
                    return None
                """
            ),
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert set(catalogs.hooks.keys()) == {"my-plugin__handler"}

    def test_missing_hook_module_skipped(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: m
            hooks:
              - module: nonexistent
                name: handler
            """,
        )
        # Don't write nonexistent.py
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert catalogs.hooks == {}

    def test_hook_name_not_in_module_skipped(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: m
            hooks:
              - module: my_hooks
                name: missing_func
            """,
        )
        # Module exists but doesn't have ``missing_func``.
        (plugin_dir / "my_hooks.py").write_text(
            "x = 1\n",
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert catalogs.hooks == {}

    def test_hook_name_not_a_hookspec_skipped(self, tmp_path: Path) -> None:
        """Function present but not decorated with @hook_spec."""
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: m
            hooks:
              - module: my_hooks
                name: not_a_hook
            """,
        )
        (plugin_dir / "my_hooks.py").write_text(
            textwrap.dedent(
                """\
                # No @hook_spec decorator — just a regular function.
                async def not_a_hook(_ctx):
                    return None
                """
            ),
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert catalogs.hooks == {}

    def test_module_import_error_skipped(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: m
            hooks:
              - module: broken
                name: anything
            """,
        )
        (plugin_dir / "broken.py").write_text(
            "raise RuntimeError('boom at import time')\n",
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert catalogs.hooks == {}


# --------------------------------------------------------------------------- #
# PluginLoader.fan_out() — mcp_servers                                        #
# --------------------------------------------------------------------------- #


class TestFanOutMcpServers:
    def test_mcp_server_namespaced(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: mcp test
            mcp_servers:
              - name: GitHub
                command: ["npx", "-y", "@x"]
            """,
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert len(catalogs.mcp_servers) == 1
        srv = catalogs.mcp_servers[0]
        assert srv.name == "my-plugin__GitHub"
        assert srv.command == ("npx", "-y", "@x")

    def test_multiple_mcp_servers_all_namespaced(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: my-plugin
            version: 0.1.0
            description: many mcps
            mcp_servers:
              - name: Alpha
                command: ["bin/a"]
              - name: Beta
                command: ["bin/b"]
            """,
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        names = {s.name for s in catalogs.mcp_servers}
        assert names == {"my-plugin__Alpha", "my-plugin__Beta"}


# --------------------------------------------------------------------------- #
# Full integration — one plugin with all 5 components                          #
# --------------------------------------------------------------------------- #


class TestFanOutFullPlugin:
    def test_full_plugin_all_five_components_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_TOKEN", "secret")
        plugin_dir = tmp_path / "full-plugin"
        _write_manifest(
            plugin_dir,
            """\
            name: full-plugin
            version: 1.0.0
            description: Has all 5 component types
            commands:
              - file: commands/deploy.md
            skills:
              - file: skills/x.md
            bundles:
              - file: bundles/y.md
            hooks:
              - module: h
                name: audit
            mcp_servers:
              - name: GitHub
                command: ["bin"]
                env:
                  TOKEN: ${FAKE_TOKEN}
            """,
        )
        _write_md(
            plugin_dir / "commands" / "deploy.md",
            "---\nname: deploy\ndescription: dep\n---\n\nbody",
        )
        _write_md(
            plugin_dir / "skills" / "x.md",
            "---\nname: x\ndescription: sx\n---\n\nbody",
        )
        _write_md(
            plugin_dir / "bundles" / "y.md",
            "---\nname: y\ndescription: by\n---\n\nbody",
        )
        (plugin_dir / "h.py").write_text(
            textwrap.dedent(
                """\
                from openharness.bundles import hook_spec


                @hook_spec("PostToolUse")
                async def audit(_ctx):
                    return None
                """
            ),
            encoding="utf-8",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert set(catalogs.commands.keys()) == {"full-plugin__deploy"}
        assert set(catalogs.skills.keys()) == {"full-plugin__x"}
        assert set(catalogs.bundles.keys()) == {"full-plugin__y"}
        assert set(catalogs.hooks.keys()) == {"full-plugin__audit"}
        assert len(catalogs.mcp_servers) == 1
        assert catalogs.mcp_servers[0].name == "full-plugin__GitHub"
        assert catalogs.mcp_servers[0].env == {"TOKEN": "secret"}

    def test_empty_manifest_loads_with_empty_catalogs(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path / "minimal",
            "name: minimal\nversion: 1\ndescription: nothing\n",
        )
        loader = PluginLoader(tmp_path)
        catalogs = loader.fan_out(loader.discover())
        assert catalogs == LoadedPluginCatalogs()


# --------------------------------------------------------------------------- #
# Validating LayeredStore can wrap real Empty stores                          #
# --------------------------------------------------------------------------- #


class TestLayeredStoreWithRealStores:
    def test_layered_over_empty_skill_store(self) -> None:
        store: LayeredStore = LayeredStore(
            base=EmptySkillStore(),
            plugin_catalog={"plugin__x": "skill-stand-in"},  # type: ignore[dict-item]
        )
        assert store.get("plugin__x") == "skill-stand-in"
        assert store.get("nonexistent") is None
        assert store.discover() == {"plugin__x": "skill-stand-in"}

    def test_layered_over_empty_command_store(self) -> None:
        store: LayeredStore = LayeredStore(
            base=EmptyCommandStore(),
            plugin_catalog={"plugin__y": "cmd-stand-in"},  # type: ignore[dict-item]
        )
        assert store.get("plugin__y") == "cmd-stand-in"
        assert store.discover() == {"plugin__y": "cmd-stand-in"}
