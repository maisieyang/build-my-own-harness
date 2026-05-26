"""System prompt assembly -- :func:`build_system_prompt` plus the
:class:`EnvironmentInfo` it consumes.

Per the P2-T5 Three-Axis discussion (D11.1 - D11.6) and ``decisions/06`` D6.5:
this module exposes one public function with a stable signature that later
phases extend without renaming. Phase 3 injected personalization; Phase 5c
added the Skills catalog; **Phase 10 (P10-T4.4d) adds two new keyword
arguments — ``claude_md_content`` and ``memory_manifest`` — for the
CLAUDE.md cascade and durable-memory injection**. All extensions land as
additional optional kwargs that default to ``None``, preserving
byte-identical output for callers that don't opt in.

P2-T5 original sub-units (now living here after the P10-T4.4a refactor
from ``prompts.py`` module → ``prompts/`` package):

- 5a: :class:`EnvironmentInfo` + :func:`detect_environment`.
- 5b: :func:`build_system_prompt` -- assembles base instructions, tool
  catalog, optional skill catalog, and environment block.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openharness.prompts.memory_inject import (
    format_memory_index_section,
    format_relevant_memories_section,
)

if TYPE_CHECKING:
    from openharness.prompts.memory_inject import MemoryManifest
    from openharness.protocols import ToolSpec
    from openharness.skills.store import SkillStore


@dataclass(frozen=True)
class EnvironmentInfo:
    """Runtime context the LLM benefits from knowing.

    Per D11.1: five fields. Skipped (intentionally) -- git_branch (changes
    mid-session), venv name (detection complexity), locale (not actionable).
    Phase 3 personalization may add fields when concrete needs surface.
    """

    os_name: str
    os_version: str
    shell: str
    cwd: Path
    python_version: str


def detect_environment() -> EnvironmentInfo:
    """Sample the current environment and build an :class:`EnvironmentInfo`.

    Per D11.2: pure stdlib reads, no exceptions. Each source has a fallback:
    ``SHELL`` defaults to ``/bin/sh`` if the env var is unset; the
    ``platform.*`` calls return empty strings rather than raising on weird
    platforms.
    """
    return EnvironmentInfo(
        os_name=platform.system(),
        os_version=platform.release(),
        shell=os.environ.get("SHELL", "/bin/sh"),
        cwd=Path.cwd(),
        python_version=platform.python_version(),
    )


# Per D11.5: short base instructions with a D8.5-aligned line about errors
# being recoverable (matches how run_query feeds is_error=True back to LLM).
_BASE_INSTRUCTIONS = (
    "You are OpenHarness, an LLM agent. You can request tool calls to interact "
    "with the user's filesystem and shell. Tools execute in the working "
    "directory shown in the Environment section. When a tool returns an error "
    "result, read the message and adapt -- most errors are recoverable."
)

# Per D11.6: empty registry still gets a `## Tools` section so the prompt
# structure is stable across configurations and snapshot tests don't flake.
_NO_TOOLS_SENTINEL = "(no tools registered)"


def build_system_prompt(
    tools: list[ToolSpec],
    env: EnvironmentInfo,
    *,
    skill_store: SkillStore | None = None,
    claude_md_content: str | None = None,
    memory_manifest: MemoryManifest | None = None,
) -> str:
    """Assemble the system prompt from base + tools + skills + env + memory.

    Per ``decisions/06`` D6.5: this function's signature is the load-bearing
    contract; later phases extend the body by appending more sections, not
    by changing the call surface. P5c-T3 added ``skill_store`` keyword-
    only. **P10-T4.4d (D28.6)** adds two more:

    - ``claude_md_content``: pre-rendered output of
      :func:`openharness.prompts.claudemd.load_claude_md_prompt`. When
      ``None`` (or empty after the load returns ``None``), no
      ``## Project Instructions`` section is emitted.
    - ``memory_manifest``: a :class:`MemoryManifest` carrying the
      MEMORY.md entrypoint + relevance-scored bodies. Each contributes
      its own section (``## Memory`` and ``## Relevant Memories``); both
      are independently skipped if absent.

    Section order (D28.6):

    1. base instructions
    2. ``## Tools``
    3. ``## Available Skills`` (if skill_store present + non-empty)
    4. ``## Environment``
    5. ``## Project Instructions`` (if claude_md_content present)
    6. ``## Memory`` (if memory_manifest.entrypoint_content present)
    7. ``## Relevant Memories`` (if memory_manifest.relevant non-empty)

    All sections joined by blank lines (``\\n\\n``). Same Markdown-``##``
    convention as P2-T5 (D11.3). The byte-identical invariant
    (P10-T4.4a) holds: when ALL three optional kwargs default to None,
    the output is byte-exact to today's prompt — existing 233+ caller
    tests pass unchanged.

    The new memory sections come AFTER Environment because the
    LLM's attention-recency bias should land on the most query-specific
    context (memory) closer to the user message, while project-stable
    context (CLAUDE.md) sits between Environment and Memory for the
    same reason.
    """
    sections = [
        _BASE_INSTRUCTIONS,
        _format_tools_section(tools),
    ]
    if skill_store is not None:
        skills_section = _format_skills_section(skill_store)
        if skills_section is not None:
            sections.append(skills_section)
    sections.append(_format_environment_section(env))
    if claude_md_content is not None:
        sections.append(claude_md_content)
    if memory_manifest is not None:
        memory_index = format_memory_index_section(memory_manifest)
        if memory_index is not None:
            sections.append(memory_index)
        relevant = format_relevant_memories_section(memory_manifest.relevant)
        if relevant is not None:
            sections.append(relevant)
    return "\n\n".join(sections)


def _format_tools_section(tools: list[ToolSpec]) -> str:
    """Per D11.4: full ``ToolSpec.description`` verbatim (not truncated to a
    single line). Tools are rendered as a Markdown bullet list."""
    if not tools:
        body = _NO_TOOLS_SENTINEL
    else:
        body = "\n".join(f"- **{tool.name}** -- {tool.description}" for tool in tools)
    return f"## Tools\n\n{body}"


def _format_environment_section(env: EnvironmentInfo) -> str:
    """Render :class:`EnvironmentInfo` as a Markdown bullet list."""
    body = (
        f"- OS: {env.os_name} {env.os_version}\n"
        f"- Shell: {env.shell}\n"
        f"- cwd: {env.cwd}\n"
        f"- Python: {env.python_version}"
    )
    return f"## Environment\n\n{body}"


def _format_skills_section(store: SkillStore) -> str | None:
    """Render the skill catalog as a Markdown bullet list,or ``None`` when
    the store is empty.

    Returning ``None`` lets :func:`build_system_prompt` skip the section
    entirely on an empty store — no "(no skills available)" sentinel
    because empty == "user hasn't authored any skills" is the default
    state, not a degenerate one. Mirrors how MCP doesn't emit a "no MCP
    servers" line when the user hasn't configured any.

    Bullet format matches :func:`_format_tools_section` so the LLM
    parses both sections with the same mental model.
    """
    skills = store.discover()
    if not skills:
        return None
    # Sorted for deterministic output — snapshot tests and trace consumers
    # benefit from stable ordering; LLM's catalog parsing doesn't depend
    # on insertion order.
    body = "\n".join(f"- **{name}** -- {skills[name].description}" for name in sorted(skills))
    return f"## Available Skills (call LoadSkill to expand)\n\n{body}"
