""":func:`build_system_prompt` ``claude_md_content`` + ``memory_manifest``
kwargs integration tests — P10-T4.4d.

Sister file to :mod:`tests.prompts.test_byte_identical` (which pins
the omitted-kwargs invariant). This file verifies the **opt-in**
behavior:

- ``claude_md_content`` present → ``## Project Instructions`` section
  appears in the right slot.
- ``MemoryManifest`` with ``entrypoint_content`` present → ``## Memory``
  section appears.
- ``MemoryManifest`` with ``relevant`` non-empty → ``## Relevant
  Memories`` section appears with per-memory subsections.
- All three together → correct order (after Environment, in the order:
  Project Instructions / Memory / Relevant Memories).
- Empty manifest behaves like ``memory_manifest=None``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from openharness.memory.model import Memory, MemoryScope, MemoryType
from openharness.prompts import (
    EnvironmentInfo,
    MemoryManifest,
    build_system_prompt,
)
from openharness.protocols import ToolSpec

_UTC = timezone.utc
_NOW = datetime(2026, 5, 26, tzinfo=_UTC)


@pytest.fixture
def env() -> EnvironmentInfo:
    return EnvironmentInfo(
        os_name="Darwin",
        os_version="25.4.0",
        shell="/bin/zsh",
        cwd=Path("/work/project"),
        python_version="3.12.3",
    )


@pytest.fixture
def tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="Read",
            description="Read a text file.",
            input_schema={"type": "object", "properties": {}},
        ),
    ]


def _memory(name: str, body: str = "body") -> Memory:
    return Memory(
        id="01HXXXXXXXX",
        name=name,
        description=f"desc for {name}",
        type=MemoryType.PROJECT,
        scope=MemoryScope.PRIVATE,
        created_at=_NOW,
        updated_at=_NOW,
        body=body,
        signature="a" * 64,
        source_path=Path(f"/tmp/{name}.md"),
    )


class TestWebEnabled:
    """Phase 14 D29.6: the ``web_enabled`` three-state kwarg.

    - ``None`` (default) — byte-identity branch; no new section.
    - ``True`` — appends ``## Web Access`` positive-guidance paragraph.
    - ``False`` — appends ``## No Internet Access`` anti-substitution
      paragraph (THE bug fix; prevents Grep/Read substitution).
    """

    def test_none_no_section(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env, web_enabled=None)
        assert "## Web Access" not in prompt
        assert "## No Internet Access" not in prompt

    def test_omitted_no_section(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        # Omitting the kwarg defaults to None; same as test_none.
        prompt = build_system_prompt(tools, env)
        assert "## Web Access" not in prompt
        assert "## No Internet Access" not in prompt

    def test_true_adds_positive_guidance(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env, web_enabled=True)
        assert "## Web Access" in prompt
        assert "## No Internet Access" not in prompt
        # Positive guidance must mention both tools so the LLM
        # knows the canonical chain.
        assert "WebSearch" in prompt
        assert "WebFetch" in prompt
        # Must NOT include the anti-substitution language since web
        # IS available.
        assert "Do NOT substitute Grep" not in prompt

    def test_false_adds_anti_substitution(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        prompt = build_system_prompt(tools, env, web_enabled=False)
        assert "## No Internet Access" in prompt
        assert "## Web Access" not in prompt
        # THE bug fix: explicit instruction not to Grep local files
        # as a research substitute.
        assert "Do NOT substitute Grep" in prompt
        assert "no internet access" in prompt
        # Surface the remediation flag so the LLM tells the user how
        # to fix.
        assert "--enable-web" in prompt

    def test_section_comes_at_the_end(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        """D29.6 + plan section-order: web section is the LAST one,
        after Environment / Project Instructions / Memory."""
        prompt = build_system_prompt(tools, env, web_enabled=False)
        env_idx = prompt.index("## Environment")
        web_idx = prompt.index("## No Internet Access")
        assert env_idx < web_idx
        # Nothing after the web section.
        assert prompt.endswith("note your cutoff if relevant.") or prompt.rstrip().endswith(
            "note your cutoff if relevant."
        )


class TestProjectInstructionsContent:
    def test_omitted_no_section(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env)
        assert "## Project Instructions" not in prompt

    def test_none_no_section(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env, project_instructions_content=None)
        assert "## Project Instructions" not in prompt

    def test_present_adds_section(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        content = "## Project Instructions\n\n### /path/AGENTS.md\n\n```md\nrules\n```"
        prompt = build_system_prompt(tools, env, project_instructions_content=content)
        assert content in prompt

    def test_section_comes_after_environment(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        content = "## Project Instructions\n\n### /p/AGENTS.md\n\n```md\nrules\n```"
        prompt = build_system_prompt(tools, env, project_instructions_content=content)
        env_idx = prompt.index("## Environment")
        proj_idx = prompt.index("## Project Instructions")
        assert env_idx < proj_idx


class TestMemoryManifestEmpty:
    def test_none_no_sections(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env, memory_manifest=None)
        assert "## Memory" not in prompt
        assert "## Relevant Memories" not in prompt

    def test_empty_manifest_no_sections(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env, memory_manifest=MemoryManifest())
        assert "## Memory" not in prompt
        assert "## Relevant Memories" not in prompt


class TestMemoryIndexSection:
    def test_entrypoint_only_adds_memory_section(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        m = MemoryManifest(entrypoint_content="- [stripe](stripe.md)\n")
        prompt = build_system_prompt(tools, env, memory_manifest=m)
        assert "## Memory" in prompt
        assert "- [stripe](stripe.md)" in prompt
        # No Relevant Memories section because relevant is empty
        assert "## Relevant Memories" not in prompt

    def test_memory_section_comes_after_environment(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        m = MemoryManifest(entrypoint_content="content\n")
        prompt = build_system_prompt(tools, env, memory_manifest=m)
        env_idx = prompt.index("## Environment")
        mem_idx = prompt.index("## Memory")
        assert env_idx < mem_idx


class TestRelevantMemoriesSection:
    def test_relevant_only_adds_section(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        m = MemoryManifest(relevant=(_memory("stripe-sdk", body="use v8"),))
        prompt = build_system_prompt(tools, env, memory_manifest=m)
        assert "## Relevant Memories" in prompt
        assert "### stripe-sdk" in prompt
        assert "use v8" in prompt
        # No Memory section because entrypoint_content is None
        assert "## Memory" not in prompt

    def test_multiple_memories_preserved_order(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        m = MemoryManifest(
            relevant=(
                _memory("a", body="first body"),
                _memory("b", body="second body"),
            )
        )
        prompt = build_system_prompt(tools, env, memory_manifest=m)
        assert prompt.index("first body") < prompt.index("second body")


class TestFullSectionOrdering:
    """All three new sections + the existing 4 → exact order per D28.6."""

    def test_all_seven_sections_in_order(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        claude = "## Project Instructions\n\n### /p/CLAUDE.md\n\n```md\nrules\n```"
        manifest = MemoryManifest(
            entrypoint_content="- [stripe](stripe.md)\n",
            relevant=(_memory("stripe-sdk", body="use v8"),),
        )
        prompt = build_system_prompt(
            tools,
            env,
            claude_md_content=claude,
            memory_manifest=manifest,
        )
        # Verify order: Tools < Environment < Project Instructions <
        # Memory < Relevant Memories. (Skills omitted because no
        # skill_store passed.)
        markers = [
            "## Tools",
            "## Environment",
            "## Project Instructions",
            "## Memory",
            "## Relevant Memories",
        ]
        positions = [prompt.index(marker) for marker in markers]
        assert positions == sorted(positions), (
            f"Section order violated: {list(zip(markers, positions, strict=True))}"
        )

    def test_base_instructions_first(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        manifest = MemoryManifest(entrypoint_content="x\n")
        prompt = build_system_prompt(tools, env, memory_manifest=manifest)
        # Base instructions land at offset 0 → must appear before any
        # ``##`` header.
        assert prompt.index("OpenHarness") < prompt.index("## Tools")
