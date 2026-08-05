"""Tests for the ``/goal`` judge's shared ``strip_markdown_fence`` helper.

Review fix regression: a truncated response (opening fence present, no
closing fence) must keep its last content line rather than silently
dropping it (the original per-module copies sliced ``lines[1:-1]``
unconditionally, discarding real content when there was no closing fence).
"""

from __future__ import annotations

from openharness.services.structured_response import strip_markdown_fence


class TestStripMarkdownFence:
    def test_no_fence_returned_unchanged(self) -> None:
        assert strip_markdown_fence('["a", "b"]') == '["a", "b"]'

    def test_closed_fence_is_stripped(self) -> None:
        assert strip_markdown_fence('```json\n["a", "b"]\n```') == '["a", "b"]'

    def test_unclosed_fence_keeps_content(self) -> None:
        assert strip_markdown_fence('```json\n["a", "b"]') == '["a", "b"]'

    def test_lone_fence_line_returned_unchanged(self) -> None:
        assert strip_markdown_fence("```") == "```"
