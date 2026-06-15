"""Tests for :func:`mark_memory_used` — P10-T3.3b.

Three load-bearing properties:

1. **use_count + last_used_at update correctly** — the closed-loop
   signal Phase 13 GC needs.
2. **All other fields preserved** — read-modify-write must not drop
   any frontmatter field (forward-compat invariant: even unknown
   future fields round-trip).
3. **Body preserved byte-exactly** — the editor-visible content
   doesn't drift on every usage tick.

Plus failure-mode tests proving the function never raises (prompt
assembly must never block on usage updates).
"""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
import yaml

from openharness.memory import usage
from openharness.memory.model import parse_memory
from openharness.memory.usage import mark_memory_used

if TYPE_CHECKING:
    from pathlib import Path

_UTC = timezone.utc


_VALID_FRONTMATTER = """\
---
id: 01HXXXXXXXX
name: stripe-sdk-version
description: Stripe SDK 8.x with legacy API
type: project
scope: private
created_at: 2026-05-26T10:00:00+00:00
updated_at: 2026-05-26T10:00:00+00:00
signature: deadbeef
importance: 0.75
tags:
  - billing
  - stripe
use_count: 3
---

Body content line 1.

Body content line 3 with  doubled  spaces.

```python
code_block = "preserved"
```
"""


def _write_and_parse(tmp_path: Path) -> tuple[Path, object]:
    """Write the standard fixture file and return (path, parsed Memory)."""
    path = tmp_path / "memdir" / "stripe.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_FRONTMATTER)
    memory = parse_memory(path)
    assert memory is not None
    return path, memory


def _read_frontmatter_dict(path: Path) -> dict:
    """Parse the file's frontmatter back into a dict for assertions."""
    text = path.read_text(encoding="utf-8")
    # Use the same split primitive the production code uses
    from openharness.markdown_store import split_frontmatter

    fm_yaml, _body = split_frontmatter(text)
    assert fm_yaml is not None
    return yaml.safe_load(fm_yaml)


class TestMarkMemoryUsedSuccess:
    def test_increments_use_count(self, tmp_path: Path) -> None:
        path, memory = _write_and_parse(tmp_path)
        mark_memory_used(memory)
        data = _read_frontmatter_dict(path)
        assert data["use_count"] == 4  # 3 + 1

    def test_increments_again_on_second_call(self, tmp_path: Path) -> None:
        path, memory = _write_and_parse(tmp_path)
        mark_memory_used(memory)
        # Re-parse so we have the updated Memory instance pointing at
        # the same file
        memory_v2 = parse_memory(path)
        assert memory_v2 is not None
        mark_memory_used(memory_v2)
        data = _read_frontmatter_dict(path)
        assert data["use_count"] == 5  # 3 + 1 + 1

    def test_sets_last_used_at_to_now(self, tmp_path: Path) -> None:
        path, memory = _write_and_parse(tmp_path)
        before = datetime.now(_UTC)
        mark_memory_used(memory)
        after = datetime.now(_UTC)
        data = _read_frontmatter_dict(path)
        # last_used_at must be ISO-format string parseable into a datetime
        # within the [before, after] window.
        assert isinstance(data["last_used_at"], str)
        stamped = datetime.fromisoformat(data["last_used_at"])
        assert before <= stamped <= after
        assert stamped.tzinfo is not None  # UTC stamp must carry tz

    def test_use_count_zero_when_field_absent(self, tmp_path: Path) -> None:
        # A hand-written file without use_count should start at 0, then
        # increment to 1 on first mark.
        path = tmp_path / "memdir" / "nouc.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            "id: 01H\n"
            "name: nouc\n"
            "description: no use_count present\n"
            "type: project\n"
            "scope: private\n"
            "created_at: 2026-01-01T00:00:00+00:00\n"
            "updated_at: 2026-01-01T00:00:00+00:00\n"
            "---\nbody\n"
        )
        memory = parse_memory(path)
        assert memory is not None
        mark_memory_used(memory)
        data = _read_frontmatter_dict(path)
        assert data["use_count"] == 1


class TestMarkMemoryUsedPreservation:
    def test_all_other_frontmatter_fields_preserved(self, tmp_path: Path) -> None:
        path, memory = _write_and_parse(tmp_path)
        before = _read_frontmatter_dict(path)
        mark_memory_used(memory)
        after = _read_frontmatter_dict(path)
        # Every field other than the two we mutate must round-trip
        # unchanged. Forward-compat invariant: even if a future schema
        # adds new fields, they must not be dropped by our rewrite.
        mutated_keys = {"use_count", "last_used_at"}
        for key in before:
            if key in mutated_keys:
                continue
            assert after[key] == before[key], f"field {key!r} drifted"
        # All before-keys still present (plus possibly new ones we added)
        assert set(before).issubset(set(after))

    def test_body_preserved_byte_exactly(self, tmp_path: Path) -> None:
        path, memory = _write_and_parse(tmp_path)
        # Extract original body via the same split primitive
        from openharness.markdown_store import split_frontmatter

        original_text = path.read_text(encoding="utf-8")
        _, original_body = split_frontmatter(original_text)
        mark_memory_used(memory)
        new_text = path.read_text(encoding="utf-8")
        _, new_body = split_frontmatter(new_text)
        assert new_body == original_body

    def test_unknown_future_fields_preserved(self, tmp_path: Path) -> None:
        # Forward-compat: schema_version=2 adds e.g. ``provenance`` —
        # mark_memory_used must NOT drop it. Otherwise upgrading
        # Phase 10 → 11 would silently lose data.
        path = tmp_path / "memdir" / "future.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            "id: 01H\n"
            "name: future\n"
            "description: forward compat\n"
            "type: project\n"
            "scope: private\n"
            "created_at: 2026-01-01T00:00:00+00:00\n"
            "updated_at: 2026-01-01T00:00:00+00:00\n"
            "provenance: extracted-by-future-agent\n"
            "schema_version: 2\n"
            "---\nbody\n"
        )
        memory = parse_memory(path)
        assert memory is not None
        mark_memory_used(memory)
        data = _read_frontmatter_dict(path)
        assert data["provenance"] == "extracted-by-future-agent"
        assert data["schema_version"] == 2

    def test_reparse_still_succeeds(self, tmp_path: Path) -> None:
        # End-to-end safety: after rewrite, parse_memory must still
        # produce a valid Memory. Catches yaml.safe_dump producing
        # something parse_memory chokes on.
        path, memory = _write_and_parse(tmp_path)
        mark_memory_used(memory)
        reparsed = parse_memory(path)
        assert reparsed is not None
        assert reparsed.name == memory.name
        assert reparsed.use_count == memory.use_count + 1
        assert reparsed.last_used_at is not None


class TestMarkMemoryUsedFailures:
    def test_file_deleted_between_discovery_and_mark(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Race: file existed when discovered, then user deleted it before
        # the relevance scorer got around to marking it used. Must NOT
        # raise — prompt assembly continues.
        path, memory = _write_and_parse(tmp_path)
        path.unlink()
        mark_memory_used(memory)  # no raise
        # File still gone — we didn't accidentally recreate it.
        assert not path.exists()

    def test_does_not_raise_on_read_only_dir(self, tmp_path: Path) -> None:
        # Read-only filesystem → atomic write fails → warning logged,
        # no raise. POSIX-only; Windows handles read-only differently.
        if sys.platform.startswith("win"):
            pytest.skip("POSIX chmod semantics required")
        path, memory = _write_and_parse(tmp_path)
        # Make directory read-only so tempfile creation fails
        parent_mode = path.parent.stat().st_mode
        os.chmod(path.parent, stat.S_IRUSR | stat.S_IXUSR)
        try:
            mark_memory_used(memory)  # no raise
        finally:
            # Restore so pytest can clean up
            os.chmod(path.parent, parent_mode)

    def test_no_orphan_tmp_files_on_success(self, tmp_path: Path) -> None:
        # After successful mark, the memory dir contains exactly one
        # file (the original) — no leftover ``.tmp`` from tempfile.
        path, memory = _write_and_parse(tmp_path)
        mark_memory_used(memory)
        files = sorted(p.name for p in path.parent.iterdir())
        assert files == ["stripe.md"]

    def test_no_raise_when_frontmatter_missing(self, tmp_path: Path) -> None:
        # File lost its frontmatter fence between discovery and mark
        # (e.g. user hand-edited it). Must bail without raising or writing.
        path, memory = _write_and_parse(tmp_path)
        path.write_text("no frontmatter here, just body\n")
        mark_memory_used(memory)  # no raise
        assert path.read_text() == "no frontmatter here, just body\n"

    def test_no_raise_on_invalid_yaml_frontmatter(self, tmp_path: Path) -> None:
        # Frontmatter present but not parseable YAML → bail, no raise.
        path, memory = _write_and_parse(tmp_path)
        path.write_text("---\nfoo: [unclosed\n---\nbody\n")
        mark_memory_used(memory)  # no raise

    def test_no_raise_when_frontmatter_not_a_mapping(self, tmp_path: Path) -> None:
        # Frontmatter is valid YAML but a scalar, not a mapping → bail.
        path, memory = _write_and_parse(tmp_path)
        path.write_text("---\njust a scalar string\n---\nbody\n")
        mark_memory_used(memory)  # no raise

    def test_no_raise_when_serialize_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # yaml.safe_dump blows up on the rewritten frontmatter → bail
        # before touching the file. Original use_count stays 3.
        path, memory = _write_and_parse(tmp_path)

        def _boom(*_a: object, **_k: object) -> str:
            raise yaml.YAMLError("cannot serialize")

        monkeypatch.setattr(usage.yaml, "safe_dump", _boom)
        mark_memory_used(memory)  # no raise
        assert _read_frontmatter_dict(path)["use_count"] == 3

    def test_cleans_up_tmp_when_replace_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Tempfile is written but os.replace fails (e.g. cross-device) →
        # the orphan tempfile must be cleaned up, leaving only the original.
        path, memory = _write_and_parse(tmp_path)

        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(usage.os, "replace", _boom)
        mark_memory_used(memory)  # no raise
        assert sorted(p.name for p in path.parent.iterdir()) == ["stripe.md"]
