"""System prompt assembly -- :func:`build_system_prompt` plus the
:class:`EnvironmentInfo` it consumes.

Per the P2-T5 Three-Axis discussion (D11.1 - D11.6) and ``decisions/06`` D6.5:
this module exposes one public function with a stable signature that later
phases extend without renaming. Phase 3 injected personalization; Phase 5c
added the Skills catalog; **Phase 10 (P10-T4.4d) adds two new keyword
arguments — ``claude_md_content`` and ``memory_manifest`` — for the
project instructions and durable-memory injection**. All extensions land as
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

from openharness.prompts.memory import format_memory_rules_section
from openharness.prompts.memory_inject import (
    format_memory_index_section,
    format_relevant_memories_section,
)

if TYPE_CHECKING:
    from openharness.prompts.memory_inject import MemoryManifest
    from openharness.protocols import ToolSpec
    from openharness.skills.store import SkillStore


_EMPTY_INDEX_PLACEHOLDER = "*(MEMORY.md is empty — no memories yet)*"


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
    "result, read the message and adapt -- most errors are recoverable. "
    "Match response length and tool use to user intent -- don't pre-emptively "
    "explore the filesystem or invoke tools for greetings or casual messages."
)

# Per D11.6: empty registry still gets a `## Tools` section so the prompt
# structure is stable across configurations and snapshot tests don't flake.
_NO_TOOLS_SENTINEL = "(no tools registered)"


# D47.6 — plan-mode posture prompt. Posture, not contract: the actual clamp
# is the permissions deny preset (plan_mode_preset); this text only steers
# the model toward converging on a reviewable plan. Appended per-turn by the
# REPL while mode=plan; never persisted (snapshots store messages, not
# system prompts).
PLAN_MODE_PROMPT_SECTION = """\
## Plan mode

You are in plan mode: file edits and shell commands are blocked by the
permission layer. Research the task with read-only tools, ask clarifying
questions if needed, then converge on ONE concise, reviewable plan (aim
for a single screen) and present it as plain text. Do not attempt to
edit files or run commands — an approval menu is shown to the user after
your reply. Approval returns the session to default mode; it does not
auto-execute the plan. The user decides the next step, such as asking you
to refine the approved plan or turn it into a /goal condition."""


def build_system_prompt(
    tools: list[ToolSpec],
    env: EnvironmentInfo,
    *,
    skill_store: SkillStore | None = None,
    project_instructions_content: str | None = None,
    claude_md_content: str | None = None,
    memory_manifest: MemoryManifest | None = None,
    memory_dir: Path | None = None,
    memory_index_content: str | None = None,
    web_enabled: bool | None = None,
) -> str:
    """Assemble the system prompt from base + tools + skills + env + memory.

    Per ``decisions/06`` D6.5: this function's signature is the load-bearing
    contract; later phases extend the body by appending more sections, not
    by changing the call surface. P5c-T3 added ``skill_store`` keyword-
    only. **P10-T4.4d (D28.6)** adds two more, **P14-T4 (D29.6)** adds one:

    - ``project_instructions_content``: pre-rendered target-project
      instructions. ``claude_md_content`` remains as a compatibility alias.
    - ``memory_manifest``: a :class:`MemoryManifest` carrying the
      MEMORY.md entrypoint + relevance-scored bodies. Each contributes
      its own section (``## Memory`` and ``## Relevant Memories``); both
      are independently skipped if absent.
    - ``web_enabled`` (Phase 14): three-state web-access disposition.
      ``None`` (default) is the byte-identity branch — no new section
      added, Phase 13 callers unchanged. ``True`` appends the
      ``## Web Access`` positive-guidance section (LLM has WebSearch +
      WebFetch). ``False`` appends the ``## No Internet Access``
      anti-substitution section (D29.6 — prevents Grep / Read
      substitution when external info is requested but web tools
      are not registered).

    **P16-T1 (D36.10/D36.11)** adds two more kwargs for the Claude-Code-
    style memory architecture pivot:

    - ``memory_dir``: when set, emit a ``## Memory`` section containing
      the CC-style write rules (from :mod:`prompts.memory`) followed by
      a ``### Memory Index`` subsection with the MEMORY.md content (or
      empty placeholder).
    - ``memory_index_content``: the MEMORY.md text (already truncated
      to first 200 lines by caller per D36.8). ``None`` or empty →
      placeholder text "MEMORY.md is empty — no memories yet".

    ``memory_dir`` and the legacy ``memory_manifest`` are **mutually
    exclusive section sources** for the ``## Memory`` slot: when
    ``memory_dir`` is provided, the new combined rules+index section is
    emitted and ``memory_manifest`` is ignored for the ``## Memory``
    section (relevant memories from manifest are also dropped — D36.7
    deprecates that path). The legacy path (manifest only, no
    ``memory_dir``) remains for byte-identical compatibility with Phase
    10/11 callers until T2 fully migrates them.

    Section order (D28.6 + D29.6 + D36.10):

    1. base instructions
    2. ``## Tools``
    3. ``## Available Skills`` (if skill_store present + non-empty)
    4. ``## Environment``
    5. ``## Project Instructions`` (if project instructions are present)
    6. ``## Memory`` — NEW combined (D36.10) when ``memory_dir`` set,
       LEGACY ``## Memory`` + ``## Relevant Memories`` (D28.6) otherwise
    7. ``## Web Access`` or ``## No Internet Access``
       (if ``web_enabled`` is not None)

    All sections joined by blank lines (``\\n\\n``). Same Markdown-``##``
    convention as P2-T5 (D11.3). The byte-identical invariant
    (P10-T4.4a) holds: when ALL optional kwargs default to None, the
    output is byte-exact to today's prompt — existing 233+ caller
    tests pass unchanged.

    The new memory sections come AFTER Environment because the
    LLM's attention-recency bias should land on the most query-specific
    context (memory) closer to the user message, while project-stable
    project context sits between Environment and Memory for the
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
    if project_instructions_content is not None and claude_md_content is not None:
        raise ValueError(
            "project_instructions_content and claude_md_content are mutually exclusive"
        )
    instruction_content = project_instructions_content or claude_md_content
    if instruction_content is not None:
        sections.append(instruction_content)
    if memory_dir is not None:
        # P16-T1 (D36.10/D36.11): CC-style combined ## Memory section
        # (rules + index). When memory_dir is set, memory_manifest is
        # ignored for the Memory slot — the new path supersedes Phase
        # 10's split rendering.
        sections.append(_format_combined_memory_section(memory_dir, memory_index_content))
    elif memory_manifest is not None:
        # Legacy Phase 10/11 path — byte-identical to v0.3.x callers.
        memory_index = format_memory_index_section(memory_manifest)
        if memory_index is not None:
            sections.append(memory_index)
        relevant = format_relevant_memories_section(memory_manifest.relevant)
        if relevant is not None:
            sections.append(relevant)
    # P14-T4 (D29.6): the web-access disposition section. Three-state
    # kwarg — ``None`` (default, additive-kwarg byte-identity
    # contract: Phase 13 callers see no new section), ``True`` (web
    # tools registered — positive guidance), ``False`` (web tools NOT
    # registered — the anti-substitution paragraph that prevents the
    # LLM from Grep'ing local files as a research substitute).
    if web_enabled is True:
        sections.append(_format_web_enabled_section())
    elif web_enabled is False:
        sections.append(_format_web_disabled_section())
    return "\n\n".join(sections)


def _format_web_enabled_section() -> str:
    """Positive-guidance paragraph for sessions with ``--enable-web``.

    Tells the LLM the canonical search-then-fetch workflow and
    discourages URL hallucination from stale training data.
    """
    return (
        "## Web Access\n"
        "\n"
        "You have ``WebSearch`` and ``WebFetch`` available. Typical "
        "research flow:\n"
        "\n"
        "1. ``WebSearch`` to discover candidate URLs for a topic.\n"
        "2. Pick 1-3 promising hits.\n"
        "3. ``WebFetch`` each picked URL for full content.\n"
        "4. Synthesize from what you fetched, citing the URL alongside "
        "each claim derived from it.\n"
        "\n"
        "Prefer ``WebSearch`` over guessing URLs from training data — "
        "your URL memory is stale and unreliable for any post-cutoff "
        "content."
    )


def _format_web_disabled_section() -> str:
    """Anti-substitution paragraph for sessions WITHOUT ``--enable-web``.

    THE bug fix (D29.6). Without this paragraph, an LLM asked for
    external research will Grep / Read local files (the closest
    available tools) and confabulate findings. With this paragraph,
    the LLM is told explicitly that local files are not the web and
    it should decline rather than substitute.
    """
    return (
        "## No Internet Access\n"
        "\n"
        "You do NOT have internet access in this session. The tools "
        "listed above are your only tools. Specifically:\n"
        "\n"
        "- Do NOT substitute Grep or Read on local files when asked "
        "for external information (news, latest research, current "
        "events, recent developments, anything that requires "
        "up-to-date or out-of-repo knowledge). Local files contain "
        "only what the user has placed there — they are NOT the web.\n"
        "- If asked for current or external information, state plainly "
        "that you have no internet access and recommend the user "
        "re-run with ``--enable-web`` to enable web search.\n"
        "- For questions about your training knowledge (concepts, "
        "history, general principles), answer from training data and "
        "note your cutoff if relevant."
    )


def _format_combined_memory_section(memory_dir: Path, memory_index_content: str | None) -> str:
    """Render the CC-style ``## Memory`` section — rules + index — per D36.10.

    The rules section (from :func:`prompts.memory.format_memory_rules_section`)
    starts with ``## Memory`` and contains the write contract / type
    definitions / "DO save when" + "DO NOT save" / `[[slug]]` syntax /
    200-line cap notes. The MEMORY.md index content is appended as a
    ``### Memory Index`` subsection at the end of the section.

    Empty / None ``memory_index_content`` → render
    :data:`_EMPTY_INDEX_PLACEHOLDER` so the LLM sees that the index
    mechanism exists but is currently empty (vs. silently omitting,
    which would leave the LLM unsure whether memory is configured).
    """
    rules = format_memory_rules_section(memory_dir)
    body = (memory_index_content or "").strip()
    if body:
        index_block = f"### Memory Index\n\n```md\n{body}\n```"
    else:
        index_block = f"### Memory Index\n\n{_EMPTY_INDEX_PLACEHOLDER}"
    return f"{rules}\n\n{index_block}"


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
    # Sprint 1 v2 wording, REVIVED 2026-07-12 after the user recalibrated
    # the regression red line (破绿 = stable break >=2/4, not a single 1/4
    # blip). Profile under this wording: 8,9,8,8 /9 — cures the delegation
    # attractor (TS1 4/4) and stabilizes TS2; residuals: direct-answer on
    # TS5-hyphens (2/4) and a 1/4 skill-name-as-tool blip on TS5-sql.
    # This wording is the subject of evals/skill_trigger — changing it
    # requires re-running the N=4 profile per tasks/sprints-2026-07-plan.md.
    guidance = (
        "If the user's task matches a skill's description below, load that "
        "skill FIRST — before answering directly and before delegating to a "
        "sub-agent. Skills are loaded only via the LoadSkill tool, passing "
        'the skill name as the `name` argument (LoadSkill(name="...")); '
        "skill names are not callable tools themselves. The skill body "
        "contains required expert guidance you do not otherwise have."
    )
    return f"## Available Skills (call LoadSkill to expand)\n\n{guidance}\n\n{body}"
