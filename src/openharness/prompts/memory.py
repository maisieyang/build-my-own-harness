"""Typed project-memory rules injected into the system prompt.

The model owns semantic decisions: what deserves durable memory, which
category fits, and when an existing fact is stale. Typed Memory tools own
storage paths, validation, atomic persistence, and the generated discovery
index. The private control-plane directory is never exposed in the prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_MEMORY_RULES_TEMPLATE = """\
## Memory

You have a persistent project memory system managed through typed Memory tools.
Storage paths, file creation, deduplication, and index maintenance belong to the
runtime. Do not use general filesystem tools to create or modify memory records.

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

### Memory tools

- Use `MemoryList` to inspect the complete catalog when the injected index is
  insufficient.
- Use `MemoryShow` to load the full body of a relevant memory.
- Use `MemoryUpsert` to create a new memory or replace an existing memory with
  the same stable name. The root session owns memory mutation.
- Use `MemoryDelete` when the user asks you to forget something or when a
  stored fact is demonstrably stale. The root session owns deletion.

The `Memory Index` below is a generated discovery view, not a file for you to
maintain. At most 200 entries are injected; use `MemoryList` for the full
catalog. Keep names stable, descriptions concise, and organize memories by
topic rather than chronology. Check for an existing memory before creating a
new one.

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
    """Render the typed memory rules section.

    The returned string is a single ``## Memory`` Markdown section
    suitable for direct inclusion in :func:`build_system_prompt`'s
    section assembly.

    ``memory_dir`` remains an enablement marker for the existing prompt API;
    the private control-plane path is deliberately not exposed to the model.
    """
    del memory_dir
    return _MEMORY_RULES_TEMPLATE
