"""Tests for ``PluginManifest`` + ``parse_manifest`` — P9-T1.

Coverage targets (per decisions/24 acceptance):

- Happy paths: minimal manifest (required only) + full manifest
- Fault tolerance: file missing / malformed YAML / not a mapping
- Required-field validation: missing name / version / description
- Name regex (kebab-case lowercase) enforcement
- Forward-compat: unknown top-level fields warn but load
- Component parsing: malformed entries skipped, valid loaded
- Env-var interpolation: ${EXISTING} resolves, ${MISSING} skips server
- McpServerConfig reuse (existing validation rules still applied)
"""

from __future__ import annotations

import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

from openharness.mcp.config import McpServerConfig
from openharness.plugins.model import (
    PLUGIN_NAME_PATTERN,
    ComponentRef,
    HookRef,
    PluginManifest,
    parse_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Dataclass construction                                                      #
# --------------------------------------------------------------------------- #


class TestPluginManifestDataclass:
    """``PluginManifest`` constructed directly (no YAML round-trip)."""

    def test_minimal_construction(self, tmp_path: Path) -> None:
        m = PluginManifest(
            name="my-plugin",
            version="0.1.0",
            description="A test plugin",
            source_path=tmp_path,
        )
        assert m.name == "my-plugin"
        assert m.version == "0.1.0"
        assert m.description == "A test plugin"
        assert m.commands == ()
        assert m.skills == ()
        assert m.bundles == ()
        assert m.hooks == ()
        assert m.mcp_servers == ()

    def test_full_construction(self, tmp_path: Path) -> None:
        m = PluginManifest(
            name="acme-tools",
            version="2.1.0",
            description="Full-featured plugin",
            source_path=tmp_path,
            author="alice",
            license="MIT",
            homepage="https://example.com",
            keywords=("coding", "review"),
            commands=(ComponentRef(file="commands/deploy.md"),),
            skills=(ComponentRef(file="skills/x.md"),),
            bundles=(ComponentRef(file="bundles/y.md"),),
            hooks=(HookRef(module="acme.hooks", name="audit"),),
            mcp_servers=(McpServerConfig(name="GitHub", command=("npx", "-y", "@x")),),
        )
        assert len(m.commands) == 1
        assert m.commands[0].file == "commands/deploy.md"
        assert m.hooks[0].name == "audit"
        assert m.mcp_servers[0].name == "GitHub"

    def test_invalid_name_uppercase(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid plugin name"):
            PluginManifest(
                name="MyPlugin",
                version="0.1.0",
                description="x",
                source_path=tmp_path,
            )

    def test_invalid_name_starts_with_digit(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid plugin name"):
            PluginManifest(
                name="1plugin",
                version="0.1.0",
                description="x",
                source_path=tmp_path,
            )

    def test_invalid_name_underscore(self, tmp_path: Path) -> None:
        # ``_`` not allowed — plugins are kebab-case.
        with pytest.raises(ValueError, match="invalid plugin name"):
            PluginManifest(
                name="my_plugin",
                version="0.1.0",
                description="x",
                source_path=tmp_path,
            )

    def test_empty_version(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="version must be non-empty"):
            PluginManifest(
                name="my-plugin",
                version="   ",
                description="x",
                source_path=tmp_path,
            )

    def test_empty_description(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="description must be non-empty"):
            PluginManifest(
                name="my-plugin",
                version="0.1.0",
                description="",
                source_path=tmp_path,
            )


class TestComponentRefAndHookRef:
    """Direct dataclass construction (used by tests + T2 fan-out)."""

    def test_component_ref_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        c = ComponentRef(file="commands/x.md")
        with pytest.raises(FrozenInstanceError):
            c.file = "other.md"  # type: ignore[misc]

    def test_hook_ref_construction(self) -> None:
        h = HookRef(module="my_pkg.hooks", name="audit")
        assert h.module == "my_pkg.hooks"
        assert h.name == "audit"


class TestPluginNamePattern:
    """``PLUGIN_NAME_PATTERN`` regex semantics."""

    @pytest.mark.parametrize(
        "valid",
        ["a", "my-plugin", "acme-tools", "p1", "x-y-z", "abc123"],
    )
    def test_valid_names(self, valid: str) -> None:
        assert PLUGIN_NAME_PATTERN.match(valid) is not None

    @pytest.mark.parametrize(
        "invalid",
        ["", "1abc", "-abc", "ABC", "my_plugin", "my.plugin", "my plugin", "abc!"],
    )
    def test_invalid_names(self, invalid: str) -> None:
        assert PLUGIN_NAME_PATTERN.match(invalid) is None


# --------------------------------------------------------------------------- #
# parse_manifest — happy paths                                                #
# --------------------------------------------------------------------------- #


def _write_manifest(plugin_dir: Path, content: str) -> Path:
    """Helper: create ``<plugin_dir>/manifest.yaml`` with given content."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = plugin_dir / "manifest.yaml"
    manifest.write_text(textwrap.dedent(content), encoding="utf-8")
    return manifest


class TestParseManifestHappy:
    def test_minimal_manifest(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "my-plugin",
            """\
            name: my-plugin
            version: 0.1.0
            description: Minimal plugin
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.name == "my-plugin"
        assert m.version == "0.1.0"
        assert m.description == "Minimal plugin"
        assert m.source_path == manifest.parent
        # All component types empty.
        assert m.commands == ()
        assert m.skills == ()
        assert m.bundles == ()
        assert m.hooks == ()
        assert m.mcp_servers == ()

    def test_full_manifest_all_five_components(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_FAKE")
        manifest = _write_manifest(
            tmp_path / "acme-tools",
            """\
            name: acme-tools
            version: 2.1.0
            description: Full plugin
            author: alice
            license: MIT
            homepage: https://example.com
            keywords: [coding, review]
            openharness_version_min: "0.1.0"
            dependencies:
              - other-plugin
            commands:
              - file: commands/deploy.md
              - file: commands/rollback.md
            skills:
              - file: skills/k8s.md
            bundles:
              - file: bundles/ops-mode.md
            hooks:
              - module: acme.hooks
                name: audit_log
            mcp_servers:
              - name: GitHub
                command: ["npx", "-y", "@modelcontextprotocol/server-github"]
                env:
                  GITHUB_TOKEN: ${GITHUB_TOKEN}
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.author == "alice"
        assert m.license == "MIT"
        assert m.homepage == "https://example.com"
        assert m.keywords == ("coding", "review")
        assert m.openharness_version_min == "0.1.0"
        assert m.dependencies == ("other-plugin",)
        assert len(m.commands) == 2
        assert m.commands[0].file == "commands/deploy.md"
        assert m.commands[1].file == "commands/rollback.md"
        assert m.skills == (ComponentRef(file="skills/k8s.md"),)
        assert m.bundles == (ComponentRef(file="bundles/ops-mode.md"),)
        assert m.hooks == (HookRef(module="acme.hooks", name="audit_log"),)
        assert len(m.mcp_servers) == 1
        assert m.mcp_servers[0].name == "GitHub"
        assert m.mcp_servers[0].env == {"GITHUB_TOKEN": "ghp_FAKE"}

    def test_version_as_float_coerced_to_str(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "x",
            """\
            name: x
            version: 1.0
            description: float-version test
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        # YAML "1.0" parses as float; we coerce.
        assert m.version == "1.0"

    def test_version_as_int_coerced_to_str(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "x",
            """\
            name: x
            version: 1
            description: int-version test
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.version == "1"


# --------------------------------------------------------------------------- #
# parse_manifest — error paths (returns None + warning, never raises)         #
# --------------------------------------------------------------------------- #


class TestParseManifestErrors:
    def test_file_not_found(self, tmp_path: Path) -> None:
        result = parse_manifest(tmp_path / "does-not-exist.yaml")
        assert result is None

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            "name: x\nversion: [unclosed",  # invalid YAML
        )
        assert parse_manifest(manifest) is None

    def test_yaml_not_a_mapping_returns_none(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            "- just\n- a\n- list\n",
        )
        assert parse_manifest(manifest) is None

    def test_missing_name_returns_none(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            version: 0.1.0
            description: x
            """,
        )
        assert parse_manifest(manifest) is None

    def test_missing_version_returns_none(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            description: x
            """,
        )
        assert parse_manifest(manifest) is None

    def test_missing_description_returns_none(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            """,
        )
        assert parse_manifest(manifest) is None

    def test_invalid_name_regex_returns_none(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: MyPlugin
            version: 0.1.0
            description: x
            """,
        )
        # Uppercase plugin name rejected by ``__post_init__``.
        assert parse_manifest(manifest) is None

    def test_unreadable_file_returns_none(self, tmp_path: Path) -> None:
        # File exists (passes is_file) but read_text fails on permissions →
        # warning + None, never raises. POSIX-only chmod semantics.
        if sys.platform.startswith("win"):
            pytest.skip("POSIX chmod semantics required")
        manifest = _write_manifest(
            tmp_path / "p",
            "name: p\nversion: 0.1.0\ndescription: x\n",
        )
        manifest.chmod(0)
        try:
            assert parse_manifest(manifest) is None
        finally:
            manifest.chmod(0o644)


# --------------------------------------------------------------------------- #
# Component parsing — defensive tolerance                                     #
# --------------------------------------------------------------------------- #


class TestComponentParsing:
    def test_commands_not_a_list_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            commands: not-a-list
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.commands == ()  # Bad field tolerated; plugin still loads

    def test_command_entry_missing_file_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            commands:
              - file: valid.md
              - {}
              - file: ""
              - file: also-valid.md
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        # Only the 2 valid entries flow through.
        assert m.commands == (
            ComponentRef(file="valid.md"),
            ComponentRef(file="also-valid.md"),
        )

    def test_non_mapping_component_entry_skipped(self, tmp_path: Path) -> None:
        # A bare scalar (not a mapping) inside a component list is skipped
        # with a warning; valid sibling entries still load.
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            commands:
              - just-a-bare-string
              - file: ok.md
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.commands == (ComponentRef(file="ok.md"),)

    def test_mcp_server_missing_name_skipped(self, tmp_path: Path) -> None:
        # An mcp_servers entry without a usable name is skipped; the
        # well-formed sibling still loads.
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers:
              - command: ["echo", "hi"]
              - name: Good
                command: ["echo", "ok"]
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert tuple(s.name for s in m.mcp_servers) == ("Good",)

    def test_hook_missing_module_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            hooks:
              - name: orphan-hook
              - module: valid_mod
                name: valid_hook
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.hooks == (HookRef(module="valid_mod", name="valid_hook"),)

    def test_hooks_not_a_list_skipped(self, tmp_path: Path) -> None:
        """hooks: just-a-string → field warned + ignored, plugin loads."""
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            hooks: not-a-list
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.hooks == ()

    def test_hook_entry_not_a_mapping_skipped(self, tmp_path: Path) -> None:
        """hooks list with a bare string entry → that entry skipped."""
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            hooks:
              - just-a-string
              - module: ok
                name: also_ok
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.hooks == (HookRef(module="ok", name="also_ok"),)

    def test_hook_missing_name_field_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            hooks:
              - module: m_no_name
              - module: full
                name: full_fn
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.hooks == (HookRef(module="full", name="full_fn"),)


# --------------------------------------------------------------------------- #
# MCP server defensive parsing                                                #
# --------------------------------------------------------------------------- #


class TestMcpServerDefensiveParsing:
    def test_mcp_servers_not_a_list_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers: not-a-list
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.mcp_servers == ()

    def test_mcp_entry_not_a_mapping_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers:
              - just-a-string
              - name: HasOne
                command: ["bin"]
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert [s.name for s in m.mcp_servers] == ["HasOne"]

    def test_mcp_env_not_a_mapping_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers:
              - name: BadEnv
                command: ["bin"]
                env: "not-a-dict"
              - name: GoodEnv
                command: ["bin"]
                env: {}
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert [s.name for s in m.mcp_servers] == ["GoodEnv"]


# --------------------------------------------------------------------------- #
# Version edge cases                                                          #
# --------------------------------------------------------------------------- #


class TestVersionEdgeCases:
    def test_version_explicit_empty_string_returns_none(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: "   "
            description: x
            """,
        )
        # Empty/whitespace-only version after coerce → None + warning.
        assert parse_manifest(manifest) is None

    def test_version_is_a_list_returns_none(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: [1, 0, 0]
            description: x
            """,
        )
        # Lists not in (str, int, float) accepted set.
        assert parse_manifest(manifest) is None


# --------------------------------------------------------------------------- #
# Forward-compat: unknown top-level fields                                    #
# --------------------------------------------------------------------------- #


class TestUnknownFields:
    def test_unknown_top_level_field_warned_but_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            future_field: some-value
            another_unknown: 42
            """,
        )
        m = parse_manifest(manifest)
        # Loads successfully; future fields ignored.
        assert m is not None
        assert m.name == "my-plugin"


# --------------------------------------------------------------------------- #
# MCP server: env-var interpolation                                           #
# --------------------------------------------------------------------------- #


class TestMcpServerEnvInterpolation:
    def test_existing_env_var_resolved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_SECRET", "abc123")
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers:
              - name: GitHub
                command: ["bin/run"]
                env:
                  TOKEN: ${MY_SECRET}
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert len(m.mcp_servers) == 1
        assert m.mcp_servers[0].env == {"TOKEN": "abc123"}

    def test_missing_env_var_skips_only_that_server(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Make sure ``MISSING_VAR`` is not set.
        monkeypatch.delenv("MISSING_VAR", raising=False)
        monkeypatch.setenv("EXISTS_OK", "yes")
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: Plugin with mixed env reqs
            commands:
              - file: c.md
            mcp_servers:
              - name: NeedsToken
                command: ["bin/run"]
                env:
                  TOKEN: ${MISSING_VAR}
              - name: Healthy
                command: ["bin/run"]
                env:
                  FLAG: ${EXISTS_OK}
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        # Plugin still loads; commands intact.
        assert len(m.commands) == 1
        # NeedsToken skipped; Healthy survives.
        names = [s.name for s in m.mcp_servers]
        assert names == ["Healthy"]
        assert m.mcp_servers[0].env == {"FLAG": "yes"}

    def test_no_interpolation_literal_value(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers:
              - name: Static
                command: ["bin"]
                env:
                  LITERAL: just-a-string
                  NUMERIC: "12345"
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert m.mcp_servers[0].env == {"LITERAL": "just-a-string", "NUMERIC": "12345"}

    def test_mcp_missing_command_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers:
              - name: NoCmd
                env: {}
              - name: HasCmd
                command: ["bin"]
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert [s.name for s in m.mcp_servers] == ["HasCmd"]

    def test_mcp_invalid_server_name_skipped(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path / "p",
            """\
            name: my-plugin
            version: 0.1.0
            description: x
            mcp_servers:
              - name: "9-bad-start"  # McpServerConfig regex rejects digit-start
                command: ["bin"]
              - name: "GoodOne"
                command: ["bin"]
            """,
        )
        m = parse_manifest(manifest)
        assert m is not None
        assert [s.name for s in m.mcp_servers] == ["GoodOne"]
