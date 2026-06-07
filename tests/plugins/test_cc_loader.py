"""Dual-format ``PluginLoader.discover`` dispatch tests — P19-T2.

Covers D39.1 (route by file marker) / D39.4 (silent skip when neither
marker present) / D39.6 (dual-marker collision = CC wins + WARN) /
D39.8 (``plugin_discovered`` INFO event with ``format`` payload).

Phase 9 OH-only loader tests live in ``test_loader.py``; this file
sits alongside and exercises only the Phase 19 additions. Existing
OH tests must remain green (T2 regression invariant).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from openharness.plugins.errors import PluginConflictError
from openharness.plugins.loader import PluginLoader

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _write_cc_plugin(
    plugin_dir: Path,
    *,
    name: str,
    description: str = "test",
    version: str = "0.1.0",
    skills: dict[str, str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": version, "description": description}
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    if skills:
        for skill_name, body in skills.items():
            sd = plugin_dir / "skills" / skill_name
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: x\n---\n{body}",
                encoding="utf-8",
            )
    if extra_files:
        for relpath, content in extra_files.items():
            full = plugin_dir / relpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
    return plugin_dir


def _write_oh_plugin(
    plugin_dir: Path, *, name: str, version: str = "1.0.0", description: str = "test"
) -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: {version}\ndescription: {description}\n",
        encoding="utf-8",
    )
    return plugin_dir


FINANCE_SKILLS_ROOT = Path(
    "/Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/plugins"
)


# --------------------------------------------------------------------------- #
# D39.1 dispatch — 4 marker-combination paths                                 #
# --------------------------------------------------------------------------- #


class TestDispatchByMarkerPresence:
    def test_cc_only_marker_routes_to_cc_parser(self, tmp_path: Path) -> None:
        _write_cc_plugin(tmp_path / "cc-plugin", name="cc-plugin")
        loader = PluginLoader(tmp_path)

        with capture_logs() as cap_logs:
            results = loader.discover_with_format()

        assert set(results.keys()) == {"cc-plugin"}
        manifest, fmt = results["cc-plugin"]
        assert manifest.name == "cc-plugin"
        assert fmt == "cc"
        # D39.8: one plugin_discovered INFO event with format=cc
        discovered = [log for log in cap_logs if log.get("event") == "plugin_discovered"]
        assert len(discovered) == 1
        assert discovered[0]["format"] == "cc"
        assert discovered[0]["plugin_name"] == "cc-plugin"
        assert discovered[0]["skills_count"] == 0
        assert discovered[0]["mcp_servers_count"] == 0

    def test_oh_only_marker_routes_to_oh_parser(self, tmp_path: Path) -> None:
        _write_oh_plugin(tmp_path / "oh-plugin", name="oh-plugin")
        loader = PluginLoader(tmp_path)

        with capture_logs() as cap_logs:
            results = loader.discover_with_format()

        assert set(results.keys()) == {"oh-plugin"}
        _, fmt = results["oh-plugin"]
        assert fmt == "oh"
        discovered = [log for log in cap_logs if log.get("event") == "plugin_discovered"]
        assert len(discovered) == 1
        assert discovered[0]["format"] == "oh"

    def test_no_marker_dir_is_silently_skipped(self, tmp_path: Path) -> None:
        # D39.4: subdir without either marker just ignored, no log noise.
        unrelated = tmp_path / "just-a-readme"
        unrelated.mkdir()
        (unrelated / "README.md").write_text("hi", encoding="utf-8")
        loader = PluginLoader(tmp_path)

        with capture_logs() as cap_logs:
            results = loader.discover_with_format()

        assert results == {}
        # Zero plugin-related events: no discovered, no dual_manifest, no
        # warning of any kind. The directory existed but isn't a plugin.
        plugin_events = [log for log in cap_logs if str(log.get("event", "")).startswith("plugin_")]
        assert plugin_events == []

    def test_non_dir_entries_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "stray.txt").write_text("not a dir")
        loader = PluginLoader(tmp_path)
        assert loader.discover_with_format() == {}

    def test_missing_plugins_root_returns_empty(self, tmp_path: Path) -> None:
        loader = PluginLoader(tmp_path / "does" / "not" / "exist")
        assert loader.discover_with_format() == {}


# --------------------------------------------------------------------------- #
# D39.6 — dual-manifest collision = CC wins + WARN                            #
# --------------------------------------------------------------------------- #


class TestDualManifestCollision:
    def test_both_markers_present_cc_wins(self, tmp_path: Path) -> None:
        # Plugin dir carries both .claude-plugin/plugin.json AND manifest.yaml.
        # The CC manifest name + version differ from the OH one so the
        # routing pick is observable.
        plugin_dir = tmp_path / "dual"
        _write_cc_plugin(plugin_dir, name="dual", version="9.9.9-cc", description="cc wins")
        (plugin_dir / "manifest.yaml").write_text(
            "name: dual\nversion: 1.0.0-oh\ndescription: oh loses\n",
            encoding="utf-8",
        )

        loader = PluginLoader(tmp_path)
        with capture_logs() as cap_logs:
            results = loader.discover_with_format()

        manifest, fmt = results["dual"]
        # CC's manifest is the one that materialized.
        assert fmt == "cc"
        assert manifest.version == "9.9.9-cc"
        assert manifest.description == "cc wins"

        # D39.6 WARN event present with picked/ignored payload.
        warn_events = [log for log in cap_logs if log.get("event") == "plugin_dual_manifest"]
        assert len(warn_events) == 1
        assert warn_events[0]["picked"] == "cc"
        assert warn_events[0]["ignored"] == "manifest.yaml"
        assert warn_events[0]["plugin_dir"] == str(plugin_dir)

    def test_only_one_marker_does_not_emit_dual_warn(self, tmp_path: Path) -> None:
        # Defensive: confirm the dual-manifest WARN ONLY fires when both
        # markers exist. A CC-only plugin must never carry the warning.
        _write_cc_plugin(tmp_path / "p", name="p")
        loader = PluginLoader(tmp_path)
        with capture_logs() as cap_logs:
            loader.discover_with_format()
        warn_events = [log for log in cap_logs if log.get("event") == "plugin_dual_manifest"]
        assert warn_events == []


# --------------------------------------------------------------------------- #
# Cross-format same-name conflict still raises (Phase 9 invariant)            #
# --------------------------------------------------------------------------- #


class TestCrossFormatNameConflict:
    def test_cc_plus_oh_with_same_name_raises_conflict(self, tmp_path: Path) -> None:
        _write_cc_plugin(tmp_path / "cc-side", name="dup")
        _write_oh_plugin(tmp_path / "oh-side", name="dup")
        loader = PluginLoader(tmp_path)
        with pytest.raises(PluginConflictError) as excinfo:
            loader.discover_with_format()
        msg = str(excinfo.value)
        # Both manifest source paths surface in the error message so the
        # user can immediately see which two directories collided. Mixed
        # CC/OH paths must both render correctly — CC at
        # ``<dir>/.claude-plugin/plugin.json``, OH at ``<dir>/manifest.yaml``.
        assert "dup" in msg
        assert "plugin.json" in msg
        assert "manifest.yaml" in msg

    def test_two_cc_plugins_with_same_name_raises_conflict(self, tmp_path: Path) -> None:
        _write_cc_plugin(tmp_path / "a", name="dup")
        _write_cc_plugin(tmp_path / "b", name="dup")
        loader = PluginLoader(tmp_path)
        with pytest.raises(PluginConflictError) as excinfo:
            loader.discover_with_format()
        # Both source files reference plugin.json (same format collision).
        assert str(excinfo.value).count("plugin.json") == 2


# --------------------------------------------------------------------------- #
# discover() vs discover_with_format() — API parity (D39.2 / Phase 9 compat)  #
# --------------------------------------------------------------------------- #


class TestDiscoverApiParity:
    def test_discover_returns_just_manifest_dict_for_phase9_callers(self, tmp_path: Path) -> None:
        _write_cc_plugin(tmp_path / "cc", name="cc")
        _write_oh_plugin(tmp_path / "oh", name="oh")
        loader = PluginLoader(tmp_path)

        results = loader.discover()
        # Same keys as discover_with_format, values are plain PluginManifest
        # (not tuples).
        assert set(results.keys()) == {"cc", "oh"}
        for name, manifest in results.items():
            assert manifest.name == name

    def test_fault_tolerance_one_broken_plugin_does_not_block_others(self, tmp_path: Path) -> None:
        _write_cc_plugin(tmp_path / "good-cc", name="good-cc")
        # Broken: plugin.json with malformed JSON.
        bad_dir = tmp_path / "bad-cc"
        (bad_dir / ".claude-plugin").mkdir(parents=True)
        (bad_dir / ".claude-plugin" / "plugin.json").write_text("{ not json", encoding="utf-8")
        loader = PluginLoader(tmp_path)
        results = loader.discover_with_format()
        # good-cc loaded, bad-cc skipped — Phase 9 fault tolerance preserved.
        assert "good-cc" in results
        assert "bad-cc" not in results


# --------------------------------------------------------------------------- #
# Integration — real finance-skills fixtures (D39.1 + D39.2 + D39.9)          #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not FINANCE_SKILLS_ROOT.is_dir(),
    reason="finance-skills repo not present at expected path",
)
class TestFinanceSkillsIntegration:
    """End-to-end: copy the actual finance-skills CC plugin dirs into a
    tmp plugins root, run discover + fan_out, and assert downstream
    namespacing landed exactly as Phase 9 + D39.2 promise.

    Skipped automatically if the finance-skills repo isn't checked out —
    this keeps the file CI-friendly while preserving the dogfood
    pre-flight that catches regressions before T4's real-LLM round-trip.
    """

    def test_credit_report_reviewer_skills_namespaced_and_loadable(self, tmp_path: Path) -> None:
        plugins_root = tmp_path / "plugins"
        plugins_root.mkdir()
        shutil.copytree(
            FINANCE_SKILLS_ROOT / "credit-report-reviewer",
            plugins_root / "credit-report-reviewer",
        )

        loader = PluginLoader(plugins_root)
        manifests = loader.discover_with_format()
        assert "credit-report-reviewer" in manifests
        manifest, fmt = manifests["credit-report-reviewer"]
        assert fmt == "cc"

        # The 4 finance-skills credit-report-reviewer SKILL.md files.
        catalogs = loader.fan_out({"credit-report-reviewer": manifest})
        expected_skill_names = {
            "credit-report-reviewer__apply-credit-rules",
            "credit-report-reviewer__cross-verify-application",
            "credit-report-reviewer__draft-credit-finding",
            "credit-report-reviewer__parse-credit-report",
        }
        assert set(catalogs.skills.keys()) == expected_skill_names
        # mcp_servers stays empty per D39.9 even though .mcp.json may
        # exist in other finance-skills plugins.
        assert catalogs.mcp_servers == ()

    def test_credit_bureau_connectors_d39_9_negative_dogfood(self, tmp_path: Path) -> None:
        # credit-bureau-connectors is the .mcp.json-only plugin (3 HTTP MCP
        # servers, 0 skills). D39.9 dogfood: it gets discovered as a CC
        # plugin (because .claude-plugin/plugin.json exists), but
        # mcp_servers stays empty and no mcp-named log event fires.
        plugins_root = tmp_path / "plugins"
        plugins_root.mkdir()
        shutil.copytree(
            FINANCE_SKILLS_ROOT / "credit-bureau-connectors",
            plugins_root / "credit-bureau-connectors",
        )

        loader = PluginLoader(plugins_root)
        with capture_logs() as cap_logs:
            results = loader.discover_with_format()

        manifest, fmt = results["credit-bureau-connectors"]
        assert fmt == "cc"
        assert manifest.skills == ()
        # D39.9 contract surface verified end-to-end on the real fixture:
        # mcp_servers tuple is empty even though the plugin's .mcp.json
        # exists on disk with 3 HTTP servers declared.
        assert manifest.mcp_servers == ()
        # plugin_discovered event reports both counts as 0.
        discovered = [log for log in cap_logs if log.get("event") == "plugin_discovered"]
        assert len(discovered) == 1
        assert discovered[0]["skills_count"] == 0
        assert discovered[0]["mcp_servers_count"] == 0
        # D39.9: no mcp-related event surfaced from the loader either.
        mcp_events = [log for log in cap_logs if "mcp" in str(log.get("event", "")).lower()]
        assert mcp_events == []
