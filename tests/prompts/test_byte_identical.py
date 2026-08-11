""":func:`build_system_prompt` byte-identical net — P10-T4.4a.

**The load-bearing test for Phase 10's prompts/ refactor and the
2-new-kwargs extension.**

Per ``decisions/25-phase-10-boundary.md`` D28.6 + ``decisions/06`` D11.5:
the existing :func:`build_system_prompt` calling forms must produce
**byte-identical** output across the Phase 10 work. Two things this
test pins:

1. **Refactor invariant** (4a): when ``prompts.py`` becomes
   ``prompts/`` package, callers writing
   ``from openharness.prompts import build_system_prompt`` and calling
   the function get the **exact same string** as before. No
   whitespace drift, no section-spacing change, no separator change.

2. **Extension invariant** (4d): when ``build_system_prompt`` later
   grows ``claude_md_content`` and ``memory_manifest`` keyword
   arguments, omitting them produces byte-identical output to today.
   Existing callers (233+ tests) keep working without modification.

The expected strings below are **snapshotted from the pre-refactor
output** of ``prompts.py``. If any of these tests fails after a
``prompts/`` change, **fix the refactor, not the expected** — the
test exists precisely to catch silent drift in the section assembly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.prompts import (
    EnvironmentInfo,
    build_system_prompt,
)
from openharness.protocols import ToolSpec
from openharness.skills.store import EmptySkillStore


@pytest.fixture
def env() -> EnvironmentInfo:
    """Fixed environment — host-independent snapshot input."""
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
        ToolSpec(
            name="Bash",
            description="Execute a shell command.",
            input_schema={"type": "object", "properties": {}},
        ),
    ]


# Snapshot strings captured from pre-refactor prompts.py output.
# Source of truth: ``uv run python -c "from openharness.prompts import
# build_system_prompt; ..."`` against commit 8be321b (P10-T3 close).

_EXPECTED_WITH_TOOLS = (
    "You are OpenHarness, an LLM agent. You can request tool calls to interact "
    "with the user's filesystem and shell. Tools execute in the working "
    "directory shown in the Environment section. When a tool returns an error "
    "result, read the message and adapt -- most errors are recoverable. "
    "If the user requests an exact verification command, do not claim it "
    "passed unless that exact command completes successfully. Alternative checks "
    "are partial evidence; report the original failure or blocker explicitly. "
    "Follow project instructions for test workflow and place tests in the project's "
    "test suite, not in production modules, unless the user or project convention "
    "explicitly requires otherwise. "
    "Match response length and tool use to user intent -- don't pre-emptively "
    "explore the filesystem or invoke tools for greetings or casual messages.\n\n"
    "## Tools\n\n"
    "- **Read** -- Read a text file.\n"
    "- **Bash** -- Execute a shell command.\n\n"
    "## Environment\n\n"
    "- OS: Darwin 25.4.0\n"
    "- Shell: /bin/zsh\n"
    "- cwd: /work/project\n"
    "- Python: 3.12.3"
)

_EXPECTED_EMPTY_TOOLS = (
    "You are OpenHarness, an LLM agent. You can request tool calls to interact "
    "with the user's filesystem and shell. Tools execute in the working "
    "directory shown in the Environment section. When a tool returns an error "
    "result, read the message and adapt -- most errors are recoverable. "
    "If the user requests an exact verification command, do not claim it "
    "passed unless that exact command completes successfully. Alternative checks "
    "are partial evidence; report the original failure or blocker explicitly. "
    "Follow project instructions for test workflow and place tests in the project's "
    "test suite, not in production modules, unless the user or project convention "
    "explicitly requires otherwise. "
    "Match response length and tool use to user intent -- don't pre-emptively "
    "explore the filesystem or invoke tools for greetings or casual messages.\n\n"
    "## Tools\n\n"
    "(no tools registered)\n\n"
    "## Environment\n\n"
    "- OS: Darwin 25.4.0\n"
    "- Shell: /bin/zsh\n"
    "- cwd: /work/project\n"
    "- Python: 3.12.3"
)


class TestByteIdenticalPositional:
    """Calling forms that worked before Phase 10 must produce identical
    output after the refactor + extension."""

    def test_no_kwargs(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        # The most common calling form. If this drifts, every existing
        # caller gets a silently-different system prompt.
        assert build_system_prompt(tools, env) == _EXPECTED_WITH_TOOLS

    def test_empty_tools_list(self, env: EnvironmentInfo) -> None:
        # D11.6 sentinel: empty tools list still has the section.
        assert build_system_prompt([], env) == _EXPECTED_EMPTY_TOOLS


class TestByteIdenticalSkillStoreFlavors:
    """All Phase 5c-era ``skill_store`` calling forms must produce
    output identical to the no-kwargs call when the store contributes
    no section."""

    def test_skill_store_none_equals_no_kwarg(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        # P5c-T3 contract: explicit None is byte-identical to omission.
        assert build_system_prompt(tools, env, skill_store=None) == (
            build_system_prompt(tools, env)
        )

    def test_empty_skill_store_equals_no_kwarg(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        # P5c-T3 contract: empty store contributes no section ≠ "no skills"
        # placeholder. Must be byte-identical to omission.
        assert build_system_prompt(
            tools, env, skill_store=EmptySkillStore()
        ) == build_system_prompt(tools, env)

    def test_skill_store_none_equals_expected_snapshot(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        # Triangulate: explicit None matches both the no-kwargs result AND
        # the literal snapshot. Catches the case where both forms drift
        # *together* (which the first two tests above would miss).
        assert build_system_prompt(tools, env, skill_store=None) == _EXPECTED_WITH_TOOLS
