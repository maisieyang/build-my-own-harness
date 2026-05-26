"""Memory manifest + section formatters for prompt injection — P10-T4.4c.

Per ``decisions/25-phase-10-boundary.md`` D28.6: the durable-memory
layer surfaces in the system prompt as **two sections**, not one:

- ``## Memory`` — the raw ``MEMORY.md`` index file content (the
  "table of contents" of what memories exist, capped at
  ``max_entrypoint_bytes``).
- ``## Relevant Memories`` — the **bodies** of the top-N memories
  selected by :func:`select_relevant_memories` against the user's
  current query (capped per-body at ``max_body_chars``).

The split is load-bearing: agent always sees the index (so it knows
what's in the store), plus the bodies of what's currently relevant
(so it has the actual content without paying for the whole pile every
turn). Without the split you'd choose between "show all" (token-
expensive) or "show selection" (agent doesn't know what else exists).

The two formatters return ``None`` when their input is empty —
:func:`build_system_prompt` (P10-T4.4d) treats ``None`` as "skip this
section entirely", same pattern as :func:`_format_skills_section`
in :mod:`prompts.system`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openharness.memory.model import Memory


_TRUNCATE_MARKER = "\n...[truncated]...\n"
_DEFAULT_MAX_BODY_CHARS = 8_000


@dataclass(frozen=True)
class MemoryManifest:
    """The two memory inputs :func:`build_system_prompt` consumes.

    Constructed at CLI bootstrap (P10-T4.4f) after the
    :class:`FilesystemMemoryStore` discovers + the relevance scorer
    picks the top-N. The frozen-dataclass shape lets the manifest
    flow through the prompt-build call surface as a single kwarg
    instead of two parallel kwargs (cleaner signature, easier to
    extend with future fields like ``compact_summary`` in Phase 11).

    Both fields are independently optional:

    - ``entrypoint_content=None`` → no ``## Memory`` section
      (project hasn't set up MEMORY.md yet).
    - ``relevant=()`` → no ``## Relevant Memories`` section
      (no memory matched the user's query, or memory is empty).

    An entirely-empty manifest produces neither section — equivalent
    to passing ``memory_manifest=None`` to :func:`build_system_prompt`.
    """

    entrypoint_content: str | None = None
    relevant: tuple[Memory, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        """``True`` when neither field would contribute a section."""
        return self.entrypoint_content is None and not self.relevant


def format_memory_index_section(manifest: MemoryManifest) -> str | None:
    """Render the MEMORY.md entrypoint as the ``## Memory`` section.

    Returns ``None`` when ``manifest.entrypoint_content`` is ``None`` —
    no MEMORY.md exists, so emit no section (rather than an empty
    header). Matches the :func:`_format_skills_section` convention:
    absent ≠ "empty placeholder".

    The content is wrapped in a ``\\`\\`\\`md`` fence so the LLM
    can distinguish the literal memory-index content from the
    surrounding prompt markdown — same convention as CLAUDE.md
    rendering in :mod:`prompts.claudemd`.
    """
    content = manifest.entrypoint_content
    if content is None:
        return None
    # ``content.strip()`` — drop leading/trailing blank lines that
    # editors often add; doesn't affect the fence-wrapped semantics.
    return f"## Memory\n\n```md\n{content.strip()}\n```"


def format_relevant_memories_section(
    memories: tuple[Memory, ...] | list[Memory],
    *,
    max_body_chars: int = _DEFAULT_MAX_BODY_CHARS,
) -> str | None:
    """Render the relevance-scored memories as ``## Relevant Memories``.

    Each memory becomes a ``### <name> — <description>`` subsection
    followed by its body. Bodies larger than ``max_body_chars`` get
    the ``\\n...[truncated]...\\n`` marker — same pattern as
    :func:`load_claude_md_prompt` truncation.

    Returns ``None`` when ``memories`` is empty (no relevance match,
    or memory store empty). Caller omits the section.

    The order of ``memories`` is preserved (highest score first per
    :func:`select_relevant_memories`'s contract). The LLM's attention-
    recency bias means the LAST subsection wins under conflict —
    but the relevance scorer puts the HIGHEST-scoring memory FIRST,
    so the most-relevant content actually sits at the top where the
    user's query has already primed attention. The asymmetry is
    intentional: relevance ordering matches "trust the score" rather
    than "trust position."
    """
    if not memories:
        return None
    subsections = [_format_one_memory(m, max_body_chars=max_body_chars) for m in memories]
    return "## Relevant Memories\n\n" + "\n\n".join(subsections)


def _format_one_memory(memory: Memory, *, max_body_chars: int) -> str:
    body = memory.body.strip()
    if len(body) > max_body_chars:
        body = body[:max_body_chars] + _TRUNCATE_MARKER
    return f"### {memory.name} — {memory.description}\n\n{body}"
