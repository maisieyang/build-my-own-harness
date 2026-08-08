"""Tests that the shipped ``examples/`` artifacts stay parseable.

The examples/ directory is the **copy-pastable**onboarding surface
referenced from ``docs/tutorial.md``. If any of them stop parsing,
new users get a broken first experience.

These tests run the same parsers the framework uses at bootstrap,
so any drift between the example format and the framework's
expectations surfaces here.
"""

from __future__ import annotations

from pathlib import Path

# Resolve examples relative to repo root — assumes tests run from
# project root (which is the standard pytest invocation).
_EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


class TestExamplesParse:
    """Each example file parses through its respective framework parser."""

    def test_commands_parse(self) -> None:
        from openharness.commands.model import parse_command

        commands_dir = _EXAMPLES_DIR / "commands"
        files = sorted(commands_dir.glob("*.md"))
        assert files, "examples/commands/ must have at least one .md"
        for path in files:
            cmd = parse_command(path)
            assert cmd is not None, f"{path.name} failed to parse"
            assert cmd.name, f"{path.name} has empty name"

    def test_skills_parse(self) -> None:
        from openharness.skills.model import parse_skill

        skills_dir = _EXAMPLES_DIR / "skills"
        files = sorted(skills_dir.glob("*.md"))
        assert files, "examples/skills/ must have at least one .md"
        for path in files:
            sk = parse_skill(path)
            assert sk is not None, f"{path.name} failed to parse"
            assert sk.name, f"{path.name} has empty name"

    def test_bundles_parse(self) -> None:
        from openharness.bundles import parse_bundle

        bundles_dir = _EXAMPLES_DIR / "bundles"
        files = sorted(bundles_dir.glob("*.md"))
        assert files, "examples/bundles/ must have at least one .md"
        for path in files:
            b = parse_bundle(path)
            assert b is not None, f"{path.name} failed to parse"
            assert b.name, f"{path.name} has empty name"


class TestSpecificExamples:
    """Spot-check the contracts the tutorial documents."""

    def test_review_command_uses_args_placeholder(self) -> None:
        from openharness.commands.model import parse_command

        cmd = parse_command(_EXAMPLES_DIR / "commands" / "review.md")
        assert cmd is not None
        assert cmd.name == "review"
        assert cmd.mode is None  # simple template, no bundle
        assert "{args}" in cmd.body, "review.md must demonstrate {args} substitution"

    def test_code_review_command_references_read_only_bundle(self) -> None:
        from openharness.commands.model import parse_command

        cmd = parse_command(_EXAMPLES_DIR / "commands" / "code-review.md")
        assert cmd is not None
        assert cmd.name == "code-review"
        # The tutorial promises this is the bundle that triggers read-only mode.
        assert cmd.mode == "read-only"

    def test_read_only_bundle_uses_supported_layers(self) -> None:
        from openharness.bundles import parse_bundle

        b = parse_bundle(_EXAMPLES_DIR / "bundles" / "read-only.md")
        assert b is not None
        assert b.name == "read-only"
        # Layer 1 — system_prompt
        assert b.system_prompt is not None
        assert "read-only" in b.system_prompt.lower()
        # Layer 2 — tool whitelist
        assert b.tools_whitelist == ("Read", "Grep")
        # Layer 3 — hooks
        assert "audit_log" in b.hook_names
        assert "deny_writes" in b.hook_names

    def test_python_testing_skill_loads(self) -> None:
        from openharness.skills.model import parse_skill

        sk = parse_skill(_EXAMPLES_DIR / "skills" / "python-testing.md")
        assert sk is not None
        assert sk.name == "python-testing"
        assert sk.version == "1"

    def test_turn_counter_plugin_loads_via_discovery(self) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        catalog = discover_filesystem_hook_plugins(global_dir=_EXAMPLES_DIR / "hooks")
        assert "count_turns" in catalog
        spec = catalog["count_turns"]
        assert spec.event == "PreApiCall"
        # The hook's docstring shows up in `oh hooks describe`.
        import inspect

        doc = inspect.getdoc(spec.hook) or ""
        assert "turn" in doc.lower()

    def test_cost_tracker_plugin_loads_via_discovery(self) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        catalog = discover_filesystem_hook_plugins(global_dir=_EXAMPLES_DIR / "hooks")
        assert "track_cost" in catalog
        spec = catalog["track_cost"]
        assert spec.event == "PostApiCall"
        import inspect

        doc = inspect.getdoc(spec.hook) or ""
        assert "cost" in doc.lower()
