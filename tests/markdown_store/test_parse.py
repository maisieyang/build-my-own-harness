"""Tests for ``split_frontmatter`` + ``read_frontmatter_dict`` — P8-T1.

Replicates the outer scaffolding tests previously duplicated in
``tests/{commands,skills,bundles}/test_model.py``. Each domain's
test_model.py keeps testing its own field-extraction logic;these
tests target the shared parser.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest

from openharness.markdown_store import read_frontmatter_dict, split_frontmatter
from openharness.observability import configure_logging

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


class TestSplitFrontmatter:
    def test_well_formed(self) -> None:
        text = "---\nname: x\ndescription: y\n---\nbody here\n"
        fm, body = split_frontmatter(text)
        assert fm == "name: x\ndescription: y"
        assert body == "body here\n"

    def test_no_opening_fence(self) -> None:
        text = "body only, no frontmatter\n"
        fm, body = split_frontmatter(text)
        assert fm is None
        assert body == text

    def test_unclosed_frontmatter(self) -> None:
        text = "---\nname: x\nno closing fence\n"
        fm, body = split_frontmatter(text)
        # No closing fence and no EOF-close — treat as unparseable.
        assert fm is None
        assert body == text

    def test_closing_fence_at_eof_no_trailing_newline(self) -> None:
        # Some editors strip the final newline; parser still finds
        # the closing fence and yields an empty body.
        text = "---\nname: x\n---"
        fm, body = split_frontmatter(text)
        assert fm == "name: x"
        assert body == ""

    def test_empty_body(self) -> None:
        text = "---\nname: x\n---\n"
        fm, body = split_frontmatter(text)
        assert fm == "name: x"
        assert body == ""


class TestReadFrontmatterDict:
    def test_happy(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: x\ndescription: y\n---\nbody\n", encoding="utf-8")
        fm, body = read_frontmatter_dict(path, logger_name="test_domain")
        assert fm == {"name": "x", "description": "y"}
        assert body == "body\n"

    def test_file_not_exist_logs_read_failed(self, tmp_path: Path, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        fm, body = read_frontmatter_dict(tmp_path / "missing.md", logger_name="test_domain")
        assert fm is None
        assert body == ""
        events = _events(stream)
        assert any(e.get("event") == "test_domain_read_failed" for e in events)

    def test_no_frontmatter_logs(self, tmp_path: Path, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        path = tmp_path / "x.md"
        path.write_text("just body, no frontmatter\n")
        fm, _ = read_frontmatter_dict(path, logger_name="test_domain")
        assert fm is None
        events = _events(stream)
        assert any(e.get("event") == "test_domain_missing_frontmatter" for e in events)

    def test_malformed_yaml_logs(self, tmp_path: Path, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        path = tmp_path / "x.md"
        path.write_text("---\nname: : :\n---\nbody\n")
        fm, _ = read_frontmatter_dict(path, logger_name="test_domain")
        assert fm is None
        events = _events(stream)
        assert any(e.get("event") == "test_domain_yaml_parse_failed" for e in events)

    def test_non_mapping_frontmatter_logs(self, tmp_path: Path, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        path = tmp_path / "x.md"
        path.write_text("---\n- alpha\n---\nbody\n")
        fm, _ = read_frontmatter_dict(path, logger_name="test_domain")
        assert fm is None
        events = _events(stream)
        non_mapping = [e for e in events if e.get("event") == "test_domain_frontmatter_not_mapping"]
        assert len(non_mapping) == 1
        assert non_mapping[0]["got"] == "list"

    def test_logger_name_threaded_through(self, tmp_path: Path, stream: io.StringIO) -> None:
        # Each domain uses its own logger_name; the warning event
        # is prefixed accordingly. This is what preserves backward-
        # compat of the existing per-domain log event names.
        configure_logging(level="INFO", format="json", stream=stream)
        path = tmp_path / "x.md"
        path.write_text("just body\n")
        read_frontmatter_dict(path, logger_name="commands")
        events = _events(stream)
        assert any(e.get("event") == "commands_missing_frontmatter" for e in events)


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
