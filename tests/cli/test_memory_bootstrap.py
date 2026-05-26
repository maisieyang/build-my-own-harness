"""Smoke tests for the memory bootstrap helpers + CLI flag — P10-T4.4f.

Three surfaces:

1. ``_load_memory_entrypoint`` — file exists / too big / missing /
   permission denied. Returns None on every failure mode (caller
   treats None as "no MEMORY.md section").
2. ``_build_memory_manifest_for_query`` — empty store / with memories
   matching query / marks used (verify use_count++ via re-parse).
3. CLI flag presence — ``--enable-memory/--no-enable-memory`` appears
   in ``oh ask --help`` and ``oh chat --help`` output.

End-to-end (does ``oh ask "..."`` actually inject CLAUDE.md + memory
into the system prompt?) lives in T6's integration test.
"""

from __future__ import annotations

import os
import stat
import sys
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from openharness.cli import (
    _build_memory_manifest_for_query,
    _load_memory_entrypoint,
    app,
)
from openharness.config.settings import MemorySettings
from openharness.memory.model import parse_memory
from openharness.memory.store import FilesystemMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


_VALID_MEMORY = """\
---
id: 01HXXXXXXXX
name: {name}
description: {description}
type: project
scope: private
created_at: 2026-05-26T10:00:00+00:00
updated_at: 2026-05-26T10:00:00+00:00
use_count: 0
---

{body}
"""


def _write_memory(memory_dir: Path, filename: str, **kwargs: str) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / filename
    path.write_text(
        _VALID_MEMORY.format(
            name=kwargs.get("name", "x"),
            description=kwargs.get("description", "Test memory"),
            body=kwargs.get("body", "body"),
        )
    )
    return path


class TestLoadMemoryEntrypoint:
    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert _load_memory_entrypoint(tmp_path, max_bytes=8_000) is None

    def test_reads_when_present(self, tmp_path: Path) -> None:
        (tmp_path / "MEMORY.md").write_text("- [stripe](stripe.md)\n")
        result = _load_memory_entrypoint(tmp_path, max_bytes=8_000)
        assert result == "- [stripe](stripe.md)\n"

    def test_returns_none_when_oversize(self, tmp_path: Path) -> None:
        # D28.6 / D28.10: index is meant to be small. Files exceeding
        # max_bytes are skipped entirely (not truncated) because a
        # half-cut TOC is worse than no TOC.
        (tmp_path / "MEMORY.md").write_text("X" * 9_000)
        assert _load_memory_entrypoint(tmp_path, max_bytes=8_000) is None

    def test_at_cap_loads(self, tmp_path: Path) -> None:
        (tmp_path / "MEMORY.md").write_text("X" * 8_000)
        result = _load_memory_entrypoint(tmp_path, max_bytes=8_000)
        assert result is not None
        assert len(result) == 8_000

    def test_permission_denied_returns_none(self, tmp_path: Path) -> None:
        if sys.platform.startswith("win"):
            pytest.skip("POSIX chmod semantics required")
        path = tmp_path / "MEMORY.md"
        path.write_text("x\n")
        os.chmod(path, 0)
        try:
            assert _load_memory_entrypoint(tmp_path, max_bytes=8_000) is None
        finally:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


class TestBuildMemoryManifestForQuery:
    def test_empty_store_returns_empty_manifest(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        manifest = _build_memory_manifest_for_query(
            memory_store=store,
            memory_dir=tmp_path,
            query="anything",
            memory_settings=MemorySettings(),
        )
        # No relevant memories AND no MEMORY.md → both fields empty
        assert manifest.entrypoint_content is None
        assert manifest.relevant == ()

    def test_query_match_populates_relevant(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memdir"
        _write_memory(memory_dir, "stripe.md", name="stripe-sdk", description="Stripe SDK")
        store = FilesystemMemoryStore(project_dir=memory_dir)
        manifest = _build_memory_manifest_for_query(
            memory_store=store,
            memory_dir=memory_dir,
            query="how to use stripe",
            memory_settings=MemorySettings(),
        )
        assert len(manifest.relevant) == 1
        assert manifest.relevant[0].name == "stripe-sdk"

    def test_marks_used_increments_use_count(self, tmp_path: Path) -> None:
        # The closed loop Phase 13 GC depends on: every relevance
        # selection bumps use_count atomically. Verify by re-parsing
        # the file after the manifest build.
        memory_dir = tmp_path / "memdir"
        path = _write_memory(memory_dir, "stripe.md", name="stripe-sdk", description="Stripe SDK")
        store = FilesystemMemoryStore(project_dir=memory_dir)
        _ = _build_memory_manifest_for_query(
            memory_store=store,
            memory_dir=memory_dir,
            query="stripe",
            memory_settings=MemorySettings(),
        )
        # Re-parse from disk → use_count was incremented atomically.
        reparsed = parse_memory(path)
        assert reparsed is not None
        assert reparsed.use_count == 1
        assert reparsed.last_used_at is not None

    def test_zero_hits_does_not_mark_used(self, tmp_path: Path) -> None:
        # Memory not surfacing for this query → use_count untouched.
        # Otherwise relevance scoring would mark every memory used
        # every turn, defeating the GC signal.
        memory_dir = tmp_path / "memdir"
        path = _write_memory(memory_dir, "stripe.md", name="stripe-sdk")
        store = FilesystemMemoryStore(project_dir=memory_dir)
        _ = _build_memory_manifest_for_query(
            memory_store=store,
            memory_dir=memory_dir,
            query="completely unrelated query about weather",
            memory_settings=MemorySettings(),
        )
        reparsed = parse_memory(path)
        assert reparsed is not None
        assert reparsed.use_count == 0

    def test_memory_md_loaded_into_manifest(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memdir"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("- [stripe](stripe.md)\n")
        store = FilesystemMemoryStore(project_dir=memory_dir)
        manifest = _build_memory_manifest_for_query(
            memory_store=store,
            memory_dir=memory_dir,
            query="anything",
            memory_settings=MemorySettings(),
        )
        assert manifest.entrypoint_content == "- [stripe](stripe.md)\n"


class TestCliFlagPresence:
    """Wide terminal width avoids Rich's box-rendering truncation that
    would clip ``--enable-memory`` to ``--enable-me…``. ``env=`` sets
    ``COLUMNS=200`` so the help text fits without ellipsis."""

    def test_ask_help_lists_enable_memory(self) -> None:
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["ask", "--help"])
        assert result.exit_code == 0
        assert "--enable-memory" in result.stdout
        assert "--no-enable-memory" in result.stdout

    def test_chat_help_lists_enable_memory(self) -> None:
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["chat", "--help"])
        assert result.exit_code == 0
        assert "--enable-memory" in result.stdout
        assert "--no-enable-memory" in result.stdout
