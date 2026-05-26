"""Tests for memory injection formatters + MemoryManifest — P10-T4.4c.

Three surfaces:

1. **MemoryManifest shape** — frozen, both fields default to "empty"
   (None entrypoint + empty relevant), ``is_empty()`` flag.
2. **format_memory_index_section** — None entrypoint → None;
   present → fenced markdown with ``## Memory`` header.
3. **format_relevant_memories_section** — empty list → None;
   present → header + per-memory ``### name — description`` +
   body, with truncation at ``max_body_chars``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openharness.memory.model import Memory, MemoryScope, MemoryType
from openharness.prompts.memory_inject import (
    MemoryManifest,
    format_memory_index_section,
    format_relevant_memories_section,
)

_UTC = timezone.utc
_NOW = datetime(2026, 5, 26, tzinfo=_UTC)


def _build_memory(
    *,
    name: str = "x",
    description: str = "d",
    body: str = "b",
) -> Memory:
    return Memory(
        id="01HXXXXXXXX",
        name=name,
        description=description,
        type=MemoryType.PROJECT,
        scope=MemoryScope.PRIVATE,
        created_at=_NOW,
        updated_at=_NOW,
        body=body,
        signature="a" * 64,
        source_path=Path("/tmp/x.md"),
    )


class TestMemoryManifest:
    def test_default_is_empty(self) -> None:
        m = MemoryManifest()
        assert m.entrypoint_content is None
        assert m.relevant == ()
        assert m.is_empty() is True

    def test_with_entrypoint_not_empty(self) -> None:
        m = MemoryManifest(entrypoint_content="index content")
        assert m.is_empty() is False

    def test_with_relevant_not_empty(self) -> None:
        memory = _build_memory()
        m = MemoryManifest(relevant=(memory,))
        assert m.is_empty() is False

    def test_is_frozen(self) -> None:
        m = MemoryManifest()
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.entrypoint_content = "x"  # type: ignore[misc]


class TestFormatMemoryIndexSection:
    def test_none_entrypoint_returns_none(self) -> None:
        assert format_memory_index_section(MemoryManifest()) is None

    def test_renders_section_with_header(self) -> None:
        m = MemoryManifest(entrypoint_content="- [stripe](stripe.md)\n")
        result = format_memory_index_section(m)
        assert result is not None
        assert result.startswith("## Memory\n\n")

    def test_fence_wraps_content(self) -> None:
        m = MemoryManifest(entrypoint_content="- [a](a.md)\n- [b](b.md)\n")
        result = format_memory_index_section(m)
        assert result is not None
        assert "```md\n" in result
        assert "\n```" in result

    def test_strips_edge_whitespace(self) -> None:
        # Cosmetic whitespace from editors must not leak into the
        # rendered section (which would change byte length between
        # editor saves and obscure the byte-identical net later).
        m = MemoryManifest(entrypoint_content="\n\n  content  \n\n")
        result = format_memory_index_section(m)
        assert result is not None
        assert "```md\ncontent\n```" in result


class TestFormatRelevantMemoriesSection:
    def test_empty_list_returns_none(self) -> None:
        assert format_relevant_memories_section([]) is None
        assert format_relevant_memories_section(()) is None

    def test_single_memory_renders(self) -> None:
        m = _build_memory(name="stripe-sdk", description="Stripe SDK 8.x", body="Use legacy API")
        result = format_relevant_memories_section([m])
        assert result is not None
        assert result.startswith("## Relevant Memories\n\n")
        assert "### stripe-sdk — Stripe SDK 8.x" in result
        assert "Use legacy API" in result

    def test_multiple_memories_joined_by_blank_lines(self) -> None:
        m1 = _build_memory(name="a", description="da", body="body a")
        m2 = _build_memory(name="b", description="db", body="body b")
        result = format_relevant_memories_section([m1, m2])
        assert result is not None
        a_idx = result.index("body a")
        b_idx = result.index("body b")
        assert a_idx < b_idx  # input order preserved
        # Subsections separated by ``\n\n``
        between = result[a_idx + len("body a") : b_idx]
        assert "\n\n###" in between

    def test_truncates_oversized_body(self) -> None:
        m = _build_memory(name="big", body="X" * 20_000)
        result = format_relevant_memories_section([m], max_body_chars=500)
        assert result is not None
        assert "[truncated]" in result
        # 500 Xs present, 1000+ would mean truncation didn't apply
        assert "X" * 500 in result
        assert "X" * 1_000 not in result

    def test_does_not_truncate_when_under_cap(self) -> None:
        m = _build_memory(name="small", body="short body")
        result = format_relevant_memories_section([m], max_body_chars=500)
        assert result is not None
        assert "[truncated]" not in result

    def test_strips_edge_whitespace_from_body(self) -> None:
        m = _build_memory(name="ws", body="\n\n  content\n\n")
        result = format_relevant_memories_section([m])
        assert result is not None
        # Body section ends with ``content`` not trailing newlines
        assert "content" in result
        # No double-blank-line between description line and body
        # (would happen if body kept leading \n)
        assert "### ws — d\n\ncontent" in result

    def test_order_preserved_input_is_relevance_order(self) -> None:
        # Caller (select_relevant_memories) returns highest-score-first.
        # Formatter must NOT re-sort — top-of-section = top-of-relevance.
        first = _build_memory(name="winner", body="should be first")
        second = _build_memory(name="runner", body="should be second")
        result = format_relevant_memories_section([first, second])
        assert result is not None
        assert result.index("winner") < result.index("runner")
