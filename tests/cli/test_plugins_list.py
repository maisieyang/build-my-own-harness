"""``oh plugins list`` subcommand tests — P19-T3.

Covers D39.7 (5-column text + ``--format json`` matching ``oh memory
list`` Phase 14 form) / D39.2 (CC + OH plugins render in same output
without dataclass changes) / D39.6 (dual-manifest plugin labels
``cc``) / D39.9 (``.mcp.json``-only plugin reports ``MCP_SERVERS=0``).

Plugins root resolves to ``$HOME/.openharness/plugins/``; the root
conftest sets ``$HOME`` to an isolation dir per test, so each test
writes its own fixture plugin tree.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

import openharness.cli as cli_module

# --------------------------------------------------------------------------- #
# Helpers — mirror the test_cc_loader.py builders                             #
# --------------------------------------------------------------------------- #


def _plugins_root() -> Path:
    return Path(os.environ["HOME"]) / ".openharness" / "plugins"


def _write_cc_plugin(
    name: str,
    *,
    version: str = "0.1.0",
    description: str = "test",
    skills: list[str] | None = None,
) -> None:
    plugin_dir = _plugins_root() / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": version, "description": description}
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    for skill_name in skills or []:
        sd = plugin_dir / "skills" / skill_name
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: x\n---\nbody",
            encoding="utf-8",
        )


def _write_oh_plugin(name: str, *, version: str = "1.0.0", description: str = "test") -> None:
    plugin_dir = _plugins_root() / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: {version}\ndescription: {description}\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Empty / missing plugins root                                                #
# --------------------------------------------------------------------------- #


class TestEmptyCatalog:
    def test_empty_text_prints_no_plugins_installed(self) -> None:
        result = CliRunner().invoke(cli_module.app, ["inspect", "plugins", "list"])
        assert result.exit_code == 0
        assert "(no plugins installed)" in result.stdout

    def test_empty_json_prints_empty_array(self) -> None:
        result = CliRunner().invoke(
            cli_module.app, ["inspect", "plugins", "list", "--format", "json"]
        )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []


# --------------------------------------------------------------------------- #
# Text rendering — D39.7 5 columns + alphabetical                             #
# --------------------------------------------------------------------------- #


class TestTextRendering:
    def test_header_row_has_five_columns(self) -> None:
        _write_cc_plugin("alpha-plugin", skills=["s1", "s2"])
        result = CliRunner().invoke(cli_module.app, ["inspect", "plugins", "list"])
        assert result.exit_code == 0
        first_line = result.stdout.splitlines()[0]
        for header in ("NAME", "FORMAT", "VERSION", "SKILLS", "MCP_SERVERS"):
            assert header in first_line, f"missing column header {header!r} in {first_line!r}"

    def test_alphabetical_ordering_across_formats(self) -> None:
        _write_cc_plugin("zebra-plugin", skills=["a"])
        _write_cc_plugin("alpha-plugin", skills=["b", "c"])
        _write_oh_plugin("mango-plugin")
        result = CliRunner().invoke(cli_module.app, ["inspect", "plugins", "list"])
        assert result.exit_code == 0
        lines = result.stdout.splitlines()[1:]  # skip header
        names = [line.split()[0] for line in lines if line.strip()]
        assert names == ["alpha-plugin", "mango-plugin", "zebra-plugin"]

    def test_format_column_shows_cc_or_oh(self) -> None:
        _write_cc_plugin("my-cc")
        _write_oh_plugin("my-oh")
        result = CliRunner().invoke(cli_module.app, ["inspect", "plugins", "list"])
        assert result.exit_code == 0
        lines = result.stdout.splitlines()
        cc_row = next(line for line in lines if "my-cc" in line)
        oh_row = next(line for line in lines if "my-oh" in line)
        assert " cc " in cc_row + " "  # surrounded by whitespace to avoid "occluded"
        assert " oh " in oh_row + " "

    def test_skills_count_reflects_directory_scan(self) -> None:
        _write_cc_plugin("counted", skills=["a", "b", "c", "d"])
        result = CliRunner().invoke(cli_module.app, ["inspect", "plugins", "list"])
        assert result.exit_code == 0
        row = next(line for line in result.stdout.splitlines() if "counted" in line)
        # SKILLS column shows 4
        assert " 4 " in row + " "


# --------------------------------------------------------------------------- #
# JSON rendering — D39.7 schema                                               #
# --------------------------------------------------------------------------- #


class TestJsonRendering:
    def test_json_schema_per_plugin(self) -> None:
        _write_cc_plugin("p1", version="2.3.4", skills=["s1", "s2"])
        _write_oh_plugin("p2", version="5.6.7")
        result = CliRunner().invoke(
            cli_module.app, ["inspect", "plugins", "list", "--format", "json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert len(payload) == 2
        # Alphabetical by name
        assert payload[0]["name"] == "p1"
        assert payload[1]["name"] == "p2"
        # Required schema fields per D39.7
        for entry in payload:
            for field in (
                "name",
                "format",
                "version",
                "skills_count",
                "mcp_servers_count",
                "source_path",
            ):
                assert field in entry, f"missing {field!r}"
        # Values
        assert payload[0]["format"] == "cc"
        assert payload[0]["version"] == "2.3.4"
        assert payload[0]["skills_count"] == 2
        assert payload[0]["mcp_servers_count"] == 0
        assert payload[1]["format"] == "oh"
        assert payload[1]["version"] == "5.6.7"


# --------------------------------------------------------------------------- #
# D39.6 — dual-manifest plugin still labeled "cc" in output                   #
# --------------------------------------------------------------------------- #


class TestDualManifestPluginRendering:
    def test_dual_marker_plugin_renders_as_cc(self) -> None:
        # Plugin dir carries BOTH .claude-plugin/plugin.json and manifest.yaml.
        # D39.6: CC wins. ``oh plugins list`` must show ``cc`` in the FORMAT
        # column (and not show two rows for the same plugin name).
        _write_cc_plugin("dual", version="9-cc")
        (_plugins_root() / "dual" / "manifest.yaml").write_text(
            "name: dual\nversion: 1-oh\ndescription: oh\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            cli_module.app, ["inspect", "plugins", "list", "--format", "json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload) == 1
        assert payload[0]["name"] == "dual"
        assert payload[0]["format"] == "cc"
        assert payload[0]["version"] == "9-cc"


# --------------------------------------------------------------------------- #
# D39.9 — .mcp.json-only plugin reports MCP_SERVERS=0                         #
# --------------------------------------------------------------------------- #


class TestD39_9_McpJsonReporting:
    def test_mcp_json_present_but_count_is_zero(self) -> None:
        # CC plugin with .mcp.json on disk but no skills. D39.9: the
        # .mcp.json is silently ignored, so mcp_servers_count must be 0
        # — honest reporting that ``oh plugins list`` doesn't pretend
        # the file was loaded.
        _write_cc_plugin("with-mcp", skills=[])
        (_plugins_root() / "with-mcp" / ".mcp.json").write_text(
            '{"mcpServers": {"x": {"type": "http", "url": "https://example"}}}',
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            cli_module.app, ["inspect", "plugins", "list", "--format", "json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["mcp_servers_count"] == 0


# --------------------------------------------------------------------------- #
# Side-effect-free guarantee — no fan_out triggered                           #
# --------------------------------------------------------------------------- #


class TestNoFanOutSideEffect:
    def test_list_does_not_invoke_fan_out(self, monkeypatch: object) -> None:
        # D39.7 acceptance: ``oh plugins list`` is read-only; it must not
        # call PluginLoader.fan_out (which imports plugin Python hook
        # modules — a side effect that's wrong for a list query).
        from openharness.plugins.loader import PluginLoader

        _write_cc_plugin("p")
        call_log: list[str] = []
        original_fan_out = PluginLoader.fan_out

        def _spy_fan_out(self, manifests):  # type: ignore[no-untyped-def]
            call_log.append("fan_out")
            return original_fan_out(self, manifests)

        monkeypatch.setattr(PluginLoader, "fan_out", _spy_fan_out)  # type: ignore[attr-defined]

        result = CliRunner().invoke(cli_module.app, ["inspect", "plugins", "list"])
        assert result.exit_code == 0
        assert call_log == []


# --------------------------------------------------------------------------- #
# Help wiring                                                                 #
# --------------------------------------------------------------------------- #


class TestHelpWiring:
    def test_plugins_subapp_listed_in_inspect_help(self) -> None:
        result = CliRunner().invoke(cli_module.app, ["inspect", "--help"])
        assert result.exit_code == 0
        assert "plugins" in result.stdout

    def test_plugins_list_listed_in_subapp_help(self) -> None:
        result = CliRunner().invoke(cli_module.app, ["inspect", "plugins", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout
