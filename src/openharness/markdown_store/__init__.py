"""Shared markdown+YAML-frontmatter discovery primitives — Phase 8.

Rule-of-three extraction: Phase 5b (commands), 5c (skills), 5d
(bundles) all repeat the same shape:

- a frozen dataclass with ``name`` (safe-identifier regex) +
  ``description`` (non-empty) + ``source_path``
- a ``parse_X(path) -> X | None`` that reads a markdown file with
  YAML frontmatter, validates required fields, and returns ``None``
  on any error (never raises)
- a ``FilesystemXStore(global_dir, project_dir)`` that scans both
  directories, merges with project-overrides-global semantics, and
  caches the result
- an ``EmptyXStore`` sentinel for "no store configured"

This package houses the shared machinery; each domain's
``model.py`` + ``store.py`` keep only the domain-specific bits
(per-dataclass fields, per-parser field extraction).

Public API:

    from openharness.markdown_store import (
        NAME_PATTERN, FRONTMATTER_FENCE,
        split_frontmatter, read_frontmatter_dict,
        MarkdownDocument,
        FilesystemMarkdownStore, EmptyMarkdownStore,
    )
"""

from __future__ import annotations

from openharness.markdown_store.constants import FRONTMATTER_FENCE, NAME_PATTERN
from openharness.markdown_store.parse import (
    read_frontmatter_dict,
    split_frontmatter,
)
from openharness.markdown_store.store import (
    EmptyMarkdownStore,
    FilesystemMarkdownStore,
    MarkdownDocument,
)

__all__ = [
    "FRONTMATTER_FENCE",
    "NAME_PATTERN",
    "EmptyMarkdownStore",
    "FilesystemMarkdownStore",
    "MarkdownDocument",
    "read_frontmatter_dict",
    "split_frontmatter",
]
