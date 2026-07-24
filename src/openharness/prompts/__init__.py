"""System prompt assembly + environment detection.

Restructured from ``prompts.py`` (module) → ``prompts/`` (package) in
**P10-T4.4a** so additional concerns (CLAUDE.md cascade in 4b, memory
injection formatters in 4c) can live as sibling modules without
bloating a single file. The public API is unchanged: callers that
imported ``from openharness.prompts import X`` get the exact same
symbols (re-exported below).

Modules:

- :mod:`openharness.prompts.system` — :func:`build_system_prompt` +
  :class:`EnvironmentInfo` + :func:`detect_environment` (the original
  ``prompts.py`` content, byte-exact behavior).
- :mod:`openharness.prompts.claudemd` (P10-T4.4b) — CLAUDE.md cascade
  discovery + loading.
- :mod:`openharness.prompts.memory_inject` (P10-T4.4c) — memory
  manifest + relevant-memories section formatters.

**Refactor invariant**: ``build_system_prompt(tools, env)`` and
``build_system_prompt(tools, env, skill_store=X)`` produce
byte-identical output to the pre-refactor ``prompts.py`` —
verified by :mod:`tests.prompts.test_byte_identical`.
"""

from __future__ import annotations

from openharness.prompts.claudemd import (
    discover_claude_md_files,
    load_claude_md_prompt,
)
from openharness.prompts.memory_inject import (
    MemoryManifest,
    format_memory_index_section,
    format_relevant_memories_section,
)
from openharness.prompts.system import (
    PLAN_MODE_PROMPT_SECTION,
    EnvironmentInfo,
    build_system_prompt,
    detect_environment,
)

__all__ = [
    "PLAN_MODE_PROMPT_SECTION",
    "EnvironmentInfo",
    "MemoryManifest",
    "build_system_prompt",
    "detect_environment",
    "discover_claude_md_files",
    "format_memory_index_section",
    "format_relevant_memories_section",
    "load_claude_md_prompt",
]
