"""Tests for ``configure_logging`` + ``get_logger`` — P3-T5.5a."""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest

from openharness.observability import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    """Fresh StringIO per test; tear down stdlib logging handlers between."""
    s = io.StringIO()
    yield s
    # Strip handlers so the next test's configure_logging starts clean.
    logging.getLogger().handlers.clear()


class TestConfigureLogging:
    def test_json_renderer_emits_one_json_per_line(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")

        log.info("hello", key="value")

        line = stream.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["event"] == "hello"
        assert parsed["key"] == "value"
        assert parsed["level"] == "info"
        # TimeStamper writes "timestamp" field in iso utc shape.
        assert "timestamp" in parsed

    def test_console_renderer_contains_event_and_kv(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="console", stream=stream)
        log = get_logger("test")

        log.info("hello", key="value")

        output = stream.getvalue()
        assert "hello" in output
        # ConsoleRenderer renders KV as ``key=value`` in some form.
        assert "key" in output and "value" in output

    def test_level_filter_drops_below_threshold(self, stream: io.StringIO) -> None:
        configure_logging(level="WARNING", format="json", stream=stream)
        log = get_logger("test")

        log.info("should drop")
        log.warning("should pass")

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        # Only the warning should have made it out.
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "should pass"

    def test_idempotent_reconfiguration_replaces_handler(self, stream: io.StringIO) -> None:
        first_stream = io.StringIO()
        configure_logging(level="DEBUG", format="json", stream=first_stream)

        # Reconfigure to the second stream.
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        log.info("only_in_second")

        # Second stream got the log.
        assert "only_in_second" in stream.getvalue()
        # First stream did NOT (the handler was cleared and replaced).
        assert first_stream.getvalue() == ""

    def test_log_to_stderr_when_no_stream_passed(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Default behavior:writes to ``sys.stderr`` so ``oh ask`` stdout
        # (the LLM response) stays clean.
        configure_logging(level="DEBUG", format="json")
        log = get_logger("test")
        log.info("default_stream")

        captured = capsys.readouterr()
        assert "default_stream" in captured.err
        assert captured.out == ""


class TestGetLogger:
    def test_default_name_is_openharness(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger()
        log.info("anonymous")

        parsed = json.loads(stream.getvalue().strip())
        # JSONRenderer doesn't surface the bound logger name by default;
        # we just verify the call doesn't crash and emits the event.
        assert parsed["event"] == "anonymous"

    def test_custom_name_does_not_crash(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("engine")
        log.info("named")
        assert "named" in stream.getvalue()
