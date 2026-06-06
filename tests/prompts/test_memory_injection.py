"""Memory rules section + ``## Memory`` byte-identical net — P16-T1.

Locks the D36.10 / D36.11 contracts:

- :func:`prompts.memory.format_memory_rules_section` produces a stable
  ``## Memory`` block with project-agnostic copy, the path
  interpolated, the two-step write contract, ``[[slug]]`` syntax, and
  the 200-line cap mention.
- :func:`build_system_prompt` ``memory_dir`` kwarg adds a combined
  ``## Memory`` rules + ``### Memory Index`` section; ``memory_index_content``
  controls the index payload; both default to None preserve
  byte-identical behavior for legacy callers (Phase 10/11).
- :func:`cli._load_memory_index_for_injection` enforces the D36.8
  200-line cap with WARN-on-truncate and silently returns ``None`` on
  missing file (no log) / OSError (WARN log).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.cli import _load_memory_index_for_injection
from openharness.prompts import (
    EnvironmentInfo,
    build_system_prompt,
)
from openharness.prompts.memory import format_memory_rules_section
from openharness.prompts.memory_inject import MemoryManifest

# ---------------------------------------------------------------------------
# Fixtures (mirror tests/prompts/test_byte_identical.py shape)
# ---------------------------------------------------------------------------


@pytest.fixture
def env() -> EnvironmentInfo:
    return EnvironmentInfo(
        os_name="Darwin",
        os_version="25.4.0",
        shell="/bin/zsh",
        cwd=Path("/work/project"),
        python_version="3.12.3",
    )


# ---------------------------------------------------------------------------
# format_memory_rules_section — D36.10 invariants
# ---------------------------------------------------------------------------


class TestMemoryRulesSection:
    def test_section_starts_with_h2_memory_heading(self) -> None:
        rules = format_memory_rules_section(Path("/tmp/m"))
        assert rules.startswith("## Memory")

    def test_memory_dir_path_interpolated(self) -> None:
        rules = format_memory_rules_section(Path("/Users/alice/.oh/memory/proj-abc"))
        assert "/Users/alice/.oh/memory/proj-abc" in rules

    def test_no_project_specific_names(self) -> None:
        """D36.10 invariant: section must be reusable across projects.

        Reject any leakage of project-specific terminology."""
        rules = format_memory_rules_section(Path("/tmp/m"))
        # Bare names that would couple the prompt to OpenHarness
        for forbidden in ("OpenHarness", " oh ", "harness "):
            assert forbidden not in rules, f"forbidden token {forbidden!r} found in rules section"

    def test_four_types_documented(self) -> None:
        rules = format_memory_rules_section(Path("/tmp/m"))
        for kind in ("user", "feedback", "project", "reference"):
            assert f"**{kind}**" in rules, f"type {kind!r} missing"

    def test_two_step_save_contract(self) -> None:
        rules = format_memory_rules_section(Path("/tmp/m"))
        assert "Step 1" in rules
        assert "Step 2" in rules
        # MEMORY.md uses Edit, not Write — explicit per D36.10
        assert "Edit" in rules
        assert "do NOT `Write` over MEMORY.md" in rules

    def test_slug_syntax_documented(self) -> None:
        rules = format_memory_rules_section(Path("/tmp/m"))
        assert "[[name]]" in rules

    def test_two_hundred_line_cap_mentioned(self) -> None:
        rules = format_memory_rules_section(Path("/tmp/m"))
        assert "200" in rules

    def test_what_not_to_save_section_present(self) -> None:
        rules = format_memory_rules_section(Path("/tmp/m"))
        assert "What NOT to save" in rules


# ---------------------------------------------------------------------------
# build_system_prompt — D36.10/D36.11 wiring + byte-identical legacy
# ---------------------------------------------------------------------------


class TestBuildSystemPromptMemoryWiring:
    def test_no_memory_kwargs_no_memory_section(self, env: EnvironmentInfo) -> None:
        """Legacy byte-identical: no memory kwargs → no ## Memory section."""
        prompt = build_system_prompt(tools=[], env=env)
        assert "## Memory" not in prompt

    def test_memory_dir_alone_emits_rules_and_placeholder(self, env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools=[], env=env, memory_dir=Path("/tmp/m"))
        assert "## Memory" in prompt
        assert "### Memory Index" in prompt
        assert "MEMORY.md is empty" in prompt

    def test_memory_dir_with_content_emits_index_block(self, env: EnvironmentInfo) -> None:
        content = "- [Alpha](alpha.md) — A\n- [Beta](beta.md) — B"
        prompt = build_system_prompt(
            tools=[],
            env=env,
            memory_dir=Path("/tmp/m"),
            memory_index_content=content,
        )
        assert "### Memory Index" in prompt
        # Fenced markdown block preserves the index content verbatim
        assert "```md" in prompt
        assert "Alpha" in prompt
        assert "Beta" in prompt

    def test_empty_string_content_treated_as_empty(self, env: EnvironmentInfo) -> None:
        """Whitespace-only content → render the empty placeholder."""
        prompt = build_system_prompt(
            tools=[],
            env=env,
            memory_dir=Path("/tmp/m"),
            memory_index_content="   \n  \n",
        )
        assert "MEMORY.md is empty" in prompt

    def test_memory_dir_wins_over_manifest(self, env: EnvironmentInfo) -> None:
        """When both memory_dir and memory_manifest are set, memory_dir
        takes precedence for the ## Memory slot. memory_manifest's
        entrypoint_content is suppressed (D36.11)."""
        manifest = MemoryManifest(entrypoint_content="- legacy [Old](old.md) — legacy entry")
        prompt = build_system_prompt(
            tools=[],
            env=env,
            memory_dir=Path("/tmp/m"),
            memory_index_content="- [New](n.md) — new",
            memory_manifest=manifest,
        )
        # CC-style rules block present
        assert "You have a persistent" in prompt
        # New index content visible
        assert "[New](n.md)" in prompt
        # Legacy manifest entry suppressed
        assert "legacy [Old]" not in prompt

    def test_manifest_only_keeps_legacy_path(self, env: EnvironmentInfo) -> None:
        """memory_dir=None + manifest set → Phase 10 byte-identical path:
        only the manifest's ## Memory section emits (no rules block).
        """
        manifest = MemoryManifest(entrypoint_content="- legacy [Old](old.md) — legacy entry")
        prompt = build_system_prompt(tools=[], env=env, memory_manifest=manifest)
        assert "## Memory" in prompt
        # New rules block NOT present
        assert "You have a persistent" not in prompt
        assert "### Memory Index" not in prompt
        # Manifest content present (fenced)
        assert "legacy [Old]" in prompt


# ---------------------------------------------------------------------------
# Byte-identical snapshots for the new combined ## Memory section
# ---------------------------------------------------------------------------


# Captured 2026-06-05 from initial implementation; if these drift,
# update intentionally and re-snapshot — same discipline as
# tests/prompts/test_byte_identical.py.

_EXPECTED_NEW_SECTION_EMPTY_TAIL = """\
### Memory Index

*(MEMORY.md is empty — no memories yet)*"""

_EXPECTED_NEW_SECTION_POPULATED_TAIL = """\
### Memory Index

```md
- [A](a.md) — first
- [B](b.md) — second
```"""


class TestByteIdenticalNewSection:
    def test_empty_index_tail_matches(self, env: EnvironmentInfo) -> None:
        """Fixture A (P16-T4 plan acceptance): empty memory_dir → placeholder."""
        prompt = build_system_prompt(tools=[], env=env, memory_dir=Path("/m"))
        assert prompt.endswith(_EXPECTED_NEW_SECTION_EMPTY_TAIL)

    def test_populated_index_tail_matches(self, env: EnvironmentInfo) -> None:
        """Fixture B (P16-T4 plan acceptance): pre-populated MEMORY.md."""
        prompt = build_system_prompt(
            tools=[],
            env=env,
            memory_dir=Path("/m"),
            memory_index_content="- [A](a.md) — first\n- [B](b.md) — second",
        )
        assert prompt.endswith(_EXPECTED_NEW_SECTION_POPULATED_TAIL)

    def test_truncated_index_tail_byte_identical(
        self, env: EnvironmentInfo, tmp_path: Path
    ) -> None:
        """Fixture C (P16-T4 plan acceptance): 300-line MEMORY.md → cut to
        first-200 lines, prompt tail byte-identical.

        End-to-end lock: drives the truncation through both the
        ``_load_memory_index_for_injection`` reader (D36.8 200-line
        cap) AND the ``build_system_prompt`` assembly (D36.10/D36.11
        combined section). Subsequent edits to either side that drift
        the byte output trip this test.
        """
        big = "\n".join(f"- line {i}" for i in range(300))
        (tmp_path / "MEMORY.md").write_text(big, encoding="utf-8")

        index_content = _load_memory_index_for_injection(tmp_path)
        assert index_content is not None
        assert len(index_content.splitlines()) == 200

        prompt = build_system_prompt(
            tools=[],
            env=env,
            memory_dir=tmp_path,
            memory_index_content=index_content,
        )

        expected_first_200 = "\n".join(f"- line {i}" for i in range(200))
        expected_tail = f"### Memory Index\n\n```md\n{expected_first_200}\n```"
        assert prompt.endswith(expected_tail)
        # Truncation boundary is sharp: line 199 in, line 200 out.
        assert "- line 199" in prompt
        assert "- line 200" not in prompt


# ---------------------------------------------------------------------------
# _load_memory_index_for_injection — D36.8 cap behavior
# ---------------------------------------------------------------------------


class TestLoadMemoryIndexForInjection:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Cold-start (no MEMORY.md): silently return None, no exception."""
        assert _load_memory_index_for_injection(tmp_path) is None

    def test_small_file_returns_full_content(self, tmp_path: Path) -> None:
        (tmp_path / "MEMORY.md").write_text(
            "# index\n- [A](a.md) — one\n- [B](b.md) — two\n",
            encoding="utf-8",
        )
        result = _load_memory_index_for_injection(tmp_path)
        assert result is not None
        assert "[A](a.md)" in result
        assert "[B](b.md)" in result

    def test_oversize_file_truncated_to_200_lines(self, tmp_path: Path) -> None:
        big = "\n".join(f"- line {i}" for i in range(300))
        (tmp_path / "MEMORY.md").write_text(big, encoding="utf-8")
        result = _load_memory_index_for_injection(tmp_path)
        assert result is not None
        assert len(result.splitlines()) == 200
        # First 200 lines kept (line 199 yes, line 200 no)
        assert "- line 199" in result
        assert "- line 200" not in result

    def test_exactly_200_lines_no_truncation(self, tmp_path: Path) -> None:
        exact = "\n".join(f"- line {i}" for i in range(200))
        (tmp_path / "MEMORY.md").write_text(exact, encoding="utf-8")
        result = _load_memory_index_for_injection(tmp_path)
        assert result is not None
        assert len(result.splitlines()) == 200

    def test_unreadable_file_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError on read → return ``None`` so caller injects placeholder.

        Simulate via monkeypatching ``Path.read_text`` to raise — we
        cannot rely on filesystem permission denied across CI envs.
        (Whether a WARN log is emitted is documented behavior in the
        function docstring but not asserted here — the structlog-to-
        stdlib bridge requires logging configuration that this unit-
        scope test deliberately stays free of. The contract this test
        locks is the *return value*; observability is checked in
        integration / logging-specific tests.)
        """
        (tmp_path / "MEMORY.md").write_text("- a\n", encoding="utf-8")

        original_read_text = Path.read_text

        def boom(self: Path, *args: object, **kwargs: object) -> str:
            if self.name == "MEMORY.md":
                raise OSError("simulated permission denied")
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", boom)
        result = _load_memory_index_for_injection(tmp_path)
        assert result is None
