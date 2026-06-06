"""Tests for ``oh memory list / show / path`` — P10-T5.

Three surfaces:

1. ``list`` — empty / populated / sort order / json / column truncation
2. ``show`` — by name / by id / unknown / empty store
3. ``path`` — exists / non-existent dir / cwd-deterministic

The HOME-isolation fixture (``tests/conftest.py``) points
``~/.openharness/memory/...`` into a fresh tmp dir per test so the
developer's real memory store doesn't leak in.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from openharness.cli import app
from openharness.memory.paths import get_project_memory_dir

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_VALID_MEMORY = """\
---
id: {id_}
name: {name}
description: {description}
type: project
scope: private
created_at: 2026-05-26T10:00:00+00:00
updated_at: 2026-05-26T10:00:00+00:00
use_count: {use_count}
last_used_at: 2026-05-26T11:00:00+00:00
---

{body}
"""


_CC_STYLE_MEMORY = """\
---
name: {name}
description: {description}
metadata:
  type: {type_}
---

{body}
"""


def _seed_memory(
    memory_dir: Path,
    *,
    name: str,
    id_: str = "01HXXXXXXXX",
    description: str = "Test memory",
    body: str = "Body",
    use_count: int = 0,
) -> Path:
    """Write a Phase 10-style memory file (full 14-field frontmatter)."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / f"{name}.md"
    path.write_text(
        _VALID_MEMORY.format(
            id_=id_,
            name=name,
            description=description,
            body=body,
            use_count=use_count,
        )
    )
    return path


def _seed_cc_memory(
    memory_dir: Path,
    *,
    name: str,
    description: str = "CC-style memory",
    type_: str = "feedback",
    body: str = "Body",
) -> Path:
    """Write a D36.10 CC-style memory file (name + description +
    nested metadata.type only — no id / scope / timestamps)."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / f"{name}.md"
    path.write_text(
        _CC_STYLE_MEMORY.format(
            name=name,
            description=description,
            type_=type_,
            body=body,
        )
    )
    return path


def _seed_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")


class TestMemoryList:
    def test_empty_store_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P17-T4 (D37.4): empty store renders ``(no memories yet)`` —
        the storage-path hint was dropped because the main LLM is the
        writer now and the user no longer needs the path to drop
        files manually."""
        _seed_required_env(monkeypatch)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "(no memories yet)" in result.stdout

    def test_empty_store_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list", "--format", "json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []

    def test_lists_populated_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        # Seed memory in the per-cwd dir (HOME-isolated by conftest)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(memory_dir, name="stripe-sdk", description="Stripe SDK 8.x")
        _seed_memory(memory_dir, name="charge-bug", id_="01HCHARGE000", body="b")
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "stripe-sdk" in result.stdout
        assert "charge-bug" in result.stdout
        assert "Stripe SDK 8.x" in result.stdout

    def test_lists_populated_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(memory_dir, name="alpha", description="first", use_count=2)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["name"] == "alpha"
        assert data[0]["use_count"] == 2
        assert data[0]["type"] == "project"
        assert data[0]["scope"] == "private"
        assert data[0]["last_used_at"] is not None

    def test_sort_alphabetical_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P17-T4 (D37.4): D37.4 dropped the use_count sort key (Phase
        10/11 relevance metadata is deprecated) — list is now sorted
        alphabetically by name, case-insensitive."""
        _seed_required_env(monkeypatch)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(memory_dir, name="zebra", id_="01HZ000000")
        _seed_memory(memory_dir, name="alpha", id_="01HA000000")
        _seed_memory(memory_dir, name="Beta", id_="01HB000000")
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list", "--format", "json"])
        data = json.loads(result.stdout)
        # Case-insensitive alphabetical: alpha, Beta, zebra
        assert [m["name"] for m in data] == ["alpha", "Beta", "zebra"]

    def test_lists_cc_and_legacy_memories_together(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P17-T4 (D37.4) + D37.1: ``oh memory list`` displays
        memories written by both the Phase 16 dogfood (CC schema —
        name + description + metadata.type only) and Phase 10/11
        files (full 14-field frontmatter). The 2026-06-06 dogfood
        was the regression case for the CC half."""
        _seed_required_env(monkeypatch)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        # Phase 10 legacy file
        _seed_memory(
            memory_dir,
            name="legacy-pref",
            id_="01HL000000",
            description="Legacy preference from Phase 10",
        )
        # CC-style file (what the main LLM emits per D36.10)
        _seed_cc_memory(
            memory_dir,
            name="user-role-and-values",
            description="Engineer between jobs, solo-building with AI",
            type_="user",
        )
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "legacy-pref" in result.stdout
        assert "user-role-and-values" in result.stdout
        # Type rendering: legacy is project, CC is user
        assert "project" in result.stdout
        assert "user" in result.stdout

    def test_long_name_truncated_with_ellipsis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P17-T4: name max 40 chars per D37.4."""
        _seed_required_env(monkeypatch)
        from pathlib import Path

        long_name = "a" * 60  # 60 chars, should truncate to 39 + ellipsis
        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(memory_dir, name=long_name)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        # Truncated form ends with the ellipsis char
        assert "a" * 39 + "…" in result.stdout
        # Full 60-char name does NOT appear verbatim
        assert "a" * 60 not in result.stdout

    def test_malformed_files_silently_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Plan deferred the "(invalid: <reason>)" row — for now,
        # malformed files just don't appear (warnings still log).
        _seed_required_env(monkeypatch)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(memory_dir, name="good")
        # Malformed sibling
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "bad.md").write_text("not even frontmatter\n")
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "good" in result.stdout
        # malformed file doesn't surface


class TestMemoryShow:
    def test_unknown_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "show", "missing"])
        assert result.exit_code == 1
        assert "Unknown memory" in result.stderr
        assert "(none" in result.stderr

    def test_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(memory_dir, name="stripe-sdk", body="Stripe content")
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "show", "stripe-sdk"])
        assert result.exit_code == 0
        # Full file content printed — frontmatter + body
        assert "stripe-sdk" in result.stdout
        assert "Stripe content" in result.stdout
        assert "scope: private" in result.stdout

    def test_by_id_falls_back_when_name_misses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(
            memory_dir,
            name="stripe-sdk",
            id_="01HCANONICAL",
            body="body content",
        )
        runner = CliRunner(env={"COLUMNS": "200"})
        # Lookup by id (not name)
        result = runner.invoke(app, ["memory", "show", "01HCANONICAL"])
        assert result.exit_code == 0
        assert "stripe-sdk" in result.stdout

    def test_unknown_lists_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        from pathlib import Path

        memory_dir = get_project_memory_dir(Path.cwd())
        _seed_memory(memory_dir, name="alpha")
        _seed_memory(memory_dir, name="beta", id_="01HB")
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "show", "missing"])
        assert result.exit_code == 1
        assert "alpha" in result.stderr
        assert "beta" in result.stderr


class TestMemoryPath:
    def test_prints_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "path"])
        assert result.exit_code == 0
        # Output is the path itself
        path = result.stdout.strip()
        assert ".openharness/memory" in path
        # Should be cwd-hashed
        assert "-" in path.rsplit("/", 1)[-1]

    def test_exits_zero_even_when_dir_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D28.11 contract: ``path`` is computable; doesn't require the
        # dir to exist. Fresh project (no memories) → still returns the
        # path so user knows where to drop files.
        _seed_required_env(monkeypatch)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["memory", "path"])
        assert result.exit_code == 0
        # The HOME-isolated dir definitely doesn't exist yet
        from pathlib import Path

        path = result.stdout.strip()
        assert not Path(path).exists()

    def test_same_cwd_same_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Determinism: two invocations from same cwd → same dir
        _seed_required_env(monkeypatch)
        runner = CliRunner(env={"COLUMNS": "200"})
        r1 = runner.invoke(app, ["memory", "path"])
        r2 = runner.invoke(app, ["memory", "path"])
        assert r1.stdout == r2.stdout
