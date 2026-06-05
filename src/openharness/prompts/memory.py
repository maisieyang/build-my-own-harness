"""CC-style memory write rules for the system prompt — P16-T1 (D36.10).

This module provides the **memory write rules section** that tells the
LLM (a) what kinds of facts deserve durable memory, (b) when NOT to
save, and (c) the two-step write contract (Write the ``.md`` body THEN
Edit MEMORY.md index). The section is injected by
:func:`openharness.prompts.system.build_system_prompt` when the caller
opts in via the ``memory_dir`` kwarg.

The rules section is **separate** from :mod:`prompts.memory_inject`,
which renders the actual MEMORY.md content as the ``## Memory``
section. This split matches CC's mental model: rules tell the LLM
*how to use* memory; the index tells the LLM *what exists*. Different
audiences, different sections.

Per boundary D36.10:

- No project-specific names (no "OpenHarness" / "oh" / service names).
  The section needs to be reusable across projects.
- The ``<memory_dir>`` path is interpolated at format-time so the LLM
  sees the actual filesystem location.
- The two-step contract ("Write the file, then Edit MEMORY.md") is
  emphasized as the canonical write pattern.
- ``[[slug]]`` syntax is documented as a prompt-level convention; the
  harness does NOT parse links (D36.14).
- The 200-line MEMORY.md cap (D36.8) is mentioned so the LLM keeps
  index entries terse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_MEMORY_RULES_TEMPLATE = """\
## Memory

You have a persistent, file-based memory system at `{memory_dir}/`.
This directory already exists — write to it directly with the Write tool \
(do not run mkdir or check for its existence).

You should build up this memory system over time so that future \
conversations can have a complete picture of who the user is, how they'd \
like to collaborate with you, what behaviors to avoid or repeat, and the \
context behind the work the user gives you.

If the user explicitly asks you to remember something, save it \
immediately as whichever type fits best. If they ask you to forget \
something, find and remove the relevant entry.

### Types of memory

- **user** — the user's role, goals, responsibilities, knowledge. Helps \
you tailor future behavior to their perspective. Save when you learn any \
details about the user's role, preferences, responsibilities, or \
knowledge.
- **feedback** — guidance the user has given you about how to approach \
work, both corrections ("don't do X") and confirmations ("yes, exactly, \
keep doing that"). Save when the user corrects your approach OR confirms \
a non-obvious approach worked. Include *why* so you can judge edge cases \
later.
- **project** — ongoing work, goals, initiatives, bugs, or incidents \
within the project that's not derivable from the code or git history. \
Save when you learn who is doing what, why, or by when. Always convert \
relative dates ("Thursday") to absolute dates ("2026-03-05") so the \
memory stays interpretable.
- **reference** — pointers to where information lives in external \
systems (Linear projects, Slack channels, dashboards). Save when you \
learn about resources in external systems and their purpose.

### What NOT to save

- Code patterns, conventions, architecture, file paths, or project \
structure — these can be derived by reading the current project state.
- Git history, recent changes, or who-changed-what — `git log` / \
`git blame` are authoritative.
- Debugging solutions or fix recipes — the fix is in the code; the \
commit message has the context.
- Anything already documented in the project's stable docs.
- Ephemeral task details: in-progress work, temporary state, current \
conversation context.

These exclusions apply even when the user explicitly asks you to save. \
If they ask you to save a PR list or activity summary, ask what was \
*surprising* or *non-obvious* about it — that is the part worth keeping.

### How to save memories (two-step)

**Step 1** — write the memory to its own file (e.g., \
`{memory_dir}/user_role.md`) using this frontmatter format:

```markdown
---
name: short-kebab-case-slug
description: one-line summary — used to decide relevance in future conversations, so be specific
metadata:
  type: user | feedback | project | reference
---

<memory content — for feedback/project types, structure as: rule/fact, \
then **Why:** and **How to apply:** lines. Link related memories with \
[[their-name]].>
```

**Step 2** — add a pointer to that file in `{memory_dir}/MEMORY.md`. \
MEMORY.md is an index, not a memory — each entry should be one line, \
under ~150 characters: `- [Title](file.md) — one-line hook`. It has no \
frontmatter. Never write memory content directly into MEMORY.md.

Use `Edit` on MEMORY.md to append the new entry — do NOT `Write` over \
MEMORY.md (that would overwrite existing entries). The only time `Write` \
is appropriate for MEMORY.md is when the file does not exist yet.

In the memory body, link to related memories with `[[name]]`, where \
`name` is the other memory's `name:` slug. Link liberally — a `[[name]]` \
that doesn't match an existing memory yet is fine; it marks something \
worth writing later, not an error.

- `MEMORY.md` is loaded into your context at the start of every \
conversation (see the `## Memory Index` section below). Lines after 200 \
will be truncated, so keep the index concise.
- Keep the name, description, and type fields in memory files up-to-date \
with the content.
- Organize memory semantically by topic, not chronologically.
- Update or remove memories that turn out to be wrong or outdated.
- Do not write duplicate memories. First check if there is an existing \
memory you can update (look at the index) before writing a new one.

### When to access memories

- When memories seem relevant, or the user references prior-conversation \
work.
- You MUST access memory when the user explicitly asks you to check, \
recall, or remember.
- If the user says to *ignore* or *not use* memory: do not apply \
remembered facts, cite, compare against, or mention memory content.
- Memory records can become stale. Use memory as context for what was \
true at a given point in time. Before acting on a recalled memory, \
verify against current code/state. If recalled memory conflicts with \
what you observe now, trust observation — and update or remove the stale \
memory rather than acting on it."""


def format_memory_rules_section(memory_dir: Path) -> str:
    """Render the memory write rules section with the given memory_dir.

    The returned string is a single ``## Memory`` Markdown section
    suitable for direct inclusion in :func:`build_system_prompt`'s
    section assembly.

    ``memory_dir`` is interpolated as the literal path the LLM should
    write to. Caller is responsible for resolving to an absolute path
    (typically via :func:`openharness.memory.paths.get_project_memory_dir`).
    """
    return _MEMORY_RULES_TEMPLATE.format(memory_dir=memory_dir)
