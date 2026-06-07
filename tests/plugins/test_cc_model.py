"""Unit tests for ``parse_cc_plugin`` + ``_scan_cc_skills_dir`` — P19-T1.

Covers D39.1 (CC plugin format recognition) / D39.2 (PluginManifest reuse,
no parallel CCPluginManifest dataclass) / D39.9 (`.mcp.json` silently
ignored in M2).

The fault-tolerance contract mirrors :func:`parse_manifest`: any error
(missing file, malformed JSON, required field missing, invalid name
regex) returns ``None`` + emits a WARN, never raises. Discovery loops
treat ``None`` as "skip this plugin, keep loading others."
"""

from __future__ import annotations

import json
from pathlib import Path

from structlog.testing import capture_logs

from openharness.plugins.model import (
    ComponentRef,
    PluginManifest,
    _scan_cc_skills_dir,
    parse_cc_plugin,
)

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _write_cc_plugin(
    plugin_dir: Path,
    *,
    name: str = "credit-report-reviewer",
    version: str | int | float | None = "0.1.0",
    description: str | None = "test description",
    author: dict | str | None = None,
    skills: dict[str, str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Build a CC-format plugin dir under ``plugin_dir``.

    - ``skills`` is ``{skill_name: skill_body_markdown}``; each becomes
      ``<plugin_dir>/skills/<skill_name>/SKILL.md`` with a minimal
      frontmatter.
    - ``extra_files`` is ``{relative_path: content}`` — escape hatch for
      e.g. dummy ``.mcp.json`` used by the D39.9 silent-ignore test.
    - Pass ``None`` for required field to omit it; ``""`` (empty string)
    to write the key with an empty value.
    """
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {}
    if name is not None:
        manifest["name"] = name
    if version is not None:
        manifest["version"] = version
    if description is not None:
        manifest["description"] = description
    if author is not None:
        manifest["author"] = author

    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    if skills:
        for skill_name, body in skills.items():
            skill_dir = plugin_dir / "skills" / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: stub\n---\n{body}",
                encoding="utf-8",
            )

    if extra_files:
        for relpath, content in extra_files.items():
            full = plugin_dir / relpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

    return plugin_dir


# --------------------------------------------------------------------------- #
# Happy paths — D39.1 / D39.2 field mapping                                   #
# --------------------------------------------------------------------------- #


class TestCcPluginFieldMapping:
    def test_full_fixture_maps_required_and_optional_fields(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(
            tmp_path / "credit-report-reviewer",
            name="credit-report-reviewer",
            version="0.1.0",
            description="信审员征信审查辅助",
            author={"name": "Risk Engineering @ MyBank"},
            skills={
                "apply-credit-rules": "rules body",
                "draft-credit-finding": "draft body",
                "parse-credit-report": "parse body",
            },
        )
        manifest = parse_cc_plugin(plugin_dir)

        assert manifest is not None
        assert manifest.name == "credit-report-reviewer"
        assert manifest.version == "0.1.0"
        assert manifest.description == "信审员征信审查辅助"
        assert manifest.author == "Risk Engineering @ MyBank"
        assert manifest.source_path == plugin_dir

        # D39.2: CC has no commands / bundles / hooks / metadata extensions;
        # all collapse to empty tuples / None.
        assert manifest.commands == ()
        assert manifest.bundles == ()
        assert manifest.hooks == ()
        assert manifest.license is None
        assert manifest.homepage is None
        assert manifest.keywords == ()
        assert manifest.openharness_version_min is None
        assert manifest.dependencies == ()

        # Skills are scanned + alphabetically sorted.
        assert manifest.skills == (
            ComponentRef(file="skills/apply-credit-rules/SKILL.md"),
            ComponentRef(file="skills/draft-credit-finding/SKILL.md"),
            ComponentRef(file="skills/parse-credit-report/SKILL.md"),
        )

    def test_minimal_fixture_only_required_fields(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(
            tmp_path / "minimal",
            name="minimal",
            version="0.1.0",
            description="minimal plugin",
            author=None,
            skills=None,
        )
        manifest = parse_cc_plugin(plugin_dir)

        assert manifest is not None
        assert manifest.name == "minimal"
        assert manifest.author is None
        assert manifest.skills == ()
        # D39.9: mcp_servers always empty regardless of plugin shape.
        assert manifest.mcp_servers == ()

    def test_author_nested_dict_extracted_to_top_level_str(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(
            tmp_path / "p",
            author={"name": "Alice", "email": "alice@example.com"},
            skills=None,
        )
        manifest = parse_cc_plugin(plugin_dir)
        assert manifest is not None
        # Only the ``name`` sub-field projects onto ``PluginManifest.author``;
        # email is dropped (D39.2 maps to flat str | None field).
        assert manifest.author == "Alice"

    def test_author_bare_string_falls_through_to_none(self, tmp_path: Path) -> None:
        # D39.2 explicit projection rule: non-dict author = None, not the
        # string itself. Forward-compat decision in case CC ever extends
        # author to a richer object.
        plugin_dir = _write_cc_plugin(
            tmp_path / "p",
            author="bare string author",  # type: ignore[arg-type]
            skills=None,
        )
        manifest = parse_cc_plugin(plugin_dir)
        assert manifest is not None
        assert manifest.author is None

    def test_author_missing_name_field_in_nested_dict(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(
            tmp_path / "p",
            author={"email": "no-name@example.com"},
            skills=None,
        )
        manifest = parse_cc_plugin(plugin_dir)
        assert manifest is not None
        assert manifest.author is None


# --------------------------------------------------------------------------- #
# Fault tolerance — required field gates                                      #
# --------------------------------------------------------------------------- #


class TestCcPluginFaultTolerance:
    """Fault-tolerance contract mirrors :func:`parse_manifest` — any error
    returns ``None`` + emits a structlog WARN event with an identifying
    ``event=`` name. We use ``structlog.testing.capture_logs`` instead
    of ``caplog`` because structlog's default ``PrintLoggerFactory``
    writes to stderr directly (bypassing stdlib's root logger) until
    ``configure_logging`` has been called — which doesn't happen in
    unit-test runs that don't touch the CLI.
    """

    def test_missing_plugin_json_returns_none(self, tmp_path: Path) -> None:
        # No .claude-plugin/plugin.json file at all.
        plugin_dir = tmp_path / "empty"
        plugin_dir.mkdir()
        manifest = parse_cc_plugin(plugin_dir)
        assert manifest is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "broken"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            "{ not valid json",
            encoding="utf-8",
        )
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)
        assert manifest is None
        assert any(log.get("event") == "plugin_cc_manifest_json_parse_failed" for log in cap_logs)

    def test_json_not_an_object_returns_none(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "list-not-object"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            "[1, 2, 3]",
            encoding="utf-8",
        )
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)
        assert manifest is None
        assert any(log.get("event") == "plugin_cc_manifest_not_a_mapping" for log in cap_logs)

    def test_missing_name_returns_none(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(tmp_path / "p", name=None, skills=None)
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)
        assert manifest is None
        assert any(log.get("event") == "plugin_cc_manifest_missing_name" for log in cap_logs)

    def test_missing_version_returns_none(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(tmp_path / "p", version=None, skills=None)
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)
        assert manifest is None
        assert any(log.get("event") == "plugin_cc_manifest_missing_version" for log in cap_logs)

    def test_missing_description_returns_none(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(tmp_path / "p", description=None, skills=None)
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)
        assert manifest is None
        assert any(log.get("event") == "plugin_cc_manifest_missing_description" for log in cap_logs)

    def test_invalid_plugin_name_regex_returns_none(self, tmp_path: Path) -> None:
        # PLUGIN_NAME_PATTERN = ^[a-z][a-z0-9-]*$ — uppercase rejected.
        plugin_dir = _write_cc_plugin(tmp_path / "BadName", name="BadName", skills=None)
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)
        assert manifest is None
        assert any(log.get("event") == "plugin_cc_manifest_validation_failed" for log in cap_logs)


# --------------------------------------------------------------------------- #
# _scan_cc_skills_dir — skills directory shape (D39.1 G1.2)                   #
# --------------------------------------------------------------------------- #


class TestScanCcSkillsDir:
    def test_no_skills_dir_returns_empty(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "no-skills"
        plugin_dir.mkdir()
        assert _scan_cc_skills_dir(plugin_dir) == ()

    def test_empty_skills_dir_returns_empty(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "empty-skills"
        (plugin_dir / "skills").mkdir(parents=True)
        assert _scan_cc_skills_dir(plugin_dir) == ()

    def test_alphabetical_ordering(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "ordered"
        for skill_name in ["zebra", "alpha", "mango"]:
            sd = plugin_dir / "skills" / skill_name
            sd.mkdir(parents=True)
            (sd / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody")
        assert _scan_cc_skills_dir(plugin_dir) == (
            ComponentRef(file="skills/alpha/SKILL.md"),
            ComponentRef(file="skills/mango/SKILL.md"),
            ComponentRef(file="skills/zebra/SKILL.md"),
        )

    def test_subdir_without_skill_md_is_skipped(self, tmp_path: Path) -> None:
        # D39.4 spirit: user's plugin dir may contain unrelated subdirs;
        # silent skip rather than fail.
        plugin_dir = tmp_path / "mixed"
        (plugin_dir / "skills" / "real").mkdir(parents=True)
        (plugin_dir / "skills" / "real" / "SKILL.md").write_text(
            "---\nname: real\ndescription: y\n---\nbody"
        )
        (plugin_dir / "skills" / "decoy").mkdir(parents=True)
        (plugin_dir / "skills" / "decoy" / "README.md").write_text("not a skill")
        assert _scan_cc_skills_dir(plugin_dir) == (ComponentRef(file="skills/real/SKILL.md"),)

    def test_files_directly_under_skills_dir_are_skipped(self, tmp_path: Path) -> None:
        # CC's shape is strictly skills/<name>/SKILL.md; a bare
        # skills/foo.md does NOT count (would be the OH flat shape).
        plugin_dir = tmp_path / "wrong-shape"
        (plugin_dir / "skills").mkdir(parents=True)
        (plugin_dir / "skills" / "foo.md").write_text("flat layout")
        assert _scan_cc_skills_dir(plugin_dir) == ()


# --------------------------------------------------------------------------- #
# D39.9 — `.mcp.json` silently ignored                                        #
# --------------------------------------------------------------------------- #


class TestD39_9_McpJsonSilentIgnore:
    """D39.9 contract: presence of ``.mcp.json`` in the plugin directory
    does not influence ``parse_cc_plugin`` output.

    Three behaviors are locked here:

    1. ``mcp_servers`` on the returned manifest is **always** ``()``.
    2. **No** WARN / INFO event with ``mcp`` in the event name is
       emitted by ``parse_cc_plugin`` even when the file exists.
    3. Source-leak forcing function: ``plugins/model.py`` source must
       not contain code-level markers that would indicate active CC
       ``.mcp.json`` parsing.
    """

    def test_dummy_mcp_json_does_not_change_manifest_or_log(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(
            tmp_path / "with-mcp",
            skills={"only-skill": "body"},
            extra_files={
                # Real CC shape: {mcpServers: {server_id: {type, url, auth}}}.
                # Use a deliberately bizarre content to prove parse_cc_plugin
                # does not look at this file in any way.
                ".mcp.json": '{"this": "is", "not": "parsed by M2 per D39.9"}',
            },
        )
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)

        assert manifest is not None
        assert manifest.mcp_servers == ()
        assert manifest.skills == (ComponentRef(file="skills/only-skill/SKILL.md"),)
        # D39.9: zero events mentioning mcp / .mcp.json from this parse.
        mcp_events = [log for log in cap_logs if "mcp" in log.get("event", "").lower()]
        assert mcp_events == [], (
            "D39.9: parse_cc_plugin must not emit any mcp-related events; "
            f"got {[log.get('event') for log in mcp_events]}"
        )

    def test_malformed_mcp_json_is_still_ignored(self, tmp_path: Path) -> None:
        # Even broken .mcp.json must not poison the parse; whole purpose
        # of D39.9 silent-ignore is that the file effectively doesn't exist.
        plugin_dir = _write_cc_plugin(
            tmp_path / "p",
            skills=None,
            extra_files={".mcp.json": "{ broken json"},
        )
        with capture_logs() as cap_logs:
            manifest = parse_cc_plugin(plugin_dir)
        assert manifest is not None
        assert manifest.mcp_servers == ()
        mcp_events = [log for log in cap_logs if "mcp" in log.get("event", "").lower()]
        assert mcp_events == []

    def test_source_leak_forcing_function(self) -> None:
        """If a future maintainer adds CC ``.mcp.json`` parsing to
        ``plugins/model.py``, this assertion fails — pushing the change
        through a fresh D39.x ratification rather than slipping in.

        Detects code-level markers that would only appear if active
        parsing leaked in. Excluded by design: docstring / comment
        references to the D39.9 decision itself, since those are
        documentation, not parsing intent.
        """
        import openharness.plugins.model as model_module

        src_path = Path(model_module.__file__)  # type: ignore[arg-type]
        source = src_path.read_text(encoding="utf-8")

        # Code-level markers a future CC ``.mcp.json`` parser would
        # almost certainly contain:
        #   - quoted ``.mcp.json`` (Path construction or string compare)
        #   - ``mcpServers`` (the CC JSON object key)
        #   - ``_parse_mcp_json`` (the planned-then-rejected helper name)
        leaks = [
            marker
            for marker in ('".mcp.json"', "'.mcp.json'", "mcpServers", "_parse_mcp_json")
            if marker in source
        ]
        assert leaks == [], (
            "D39.9 source-leak forcing function: plugins/model.py must not "
            f"contain CC `.mcp.json` parse markers. Found: {leaks}. "
            "Escalate to a new D39.x ratification before adding parsing code."
        )


# --------------------------------------------------------------------------- #
# Architecture invariant — PluginManifest dataclass reuse (D39.2)             #
# --------------------------------------------------------------------------- #


class TestPluginManifestDataclassReuse:
    """D39.2 locks: no new ``CCPluginManifest`` dataclass; CC translates
    directly into the existing :class:`PluginManifest`. The check below
    asserts the returned object's exact class identity.
    """

    def test_parse_cc_plugin_returns_pluginmanifest_class(self, tmp_path: Path) -> None:
        plugin_dir = _write_cc_plugin(tmp_path / "p", skills=None)
        manifest = parse_cc_plugin(plugin_dir)
        assert manifest is not None
        assert type(manifest) is PluginManifest

    def test_pluginmanifest_has_no_source_format_field(self) -> None:
        # D39.2 explicit: ``source_format`` field NOT added to
        # PluginManifest. CC vs OH distinction lives in the
        # ``oh plugins list`` intermediate tuple (T3 territory), not
        # in the dataclass.
        field_names = {f.name for f in PluginManifest.__dataclass_fields__.values()}
        assert "source_format" not in field_names
