"""Shared parsing helper for callers of :func:`summarize` that expect a
structured (JSON) response — currently ``verification/semantic_gate.py``
(L3' judge) and ``services/decompose.py`` (L5 decomposer).
"""

from __future__ import annotations


def strip_markdown_fence(text: str) -> str:
    """Strip a leading ```` ```lang ```` fence line and, if present, a
    trailing ```` ``` ```` fence line. Text without an opening fence is
    returned unchanged.

    Only strips the trailing line when it actually IS a closing fence —
    a truncated response (opening fence, no closing fence) keeps its last
    content line rather than having it silently discarded.
    """
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    body_lines = lines[1:]
    if body_lines and body_lines[-1].strip() == "```":
        body_lines = body_lines[:-1]
    return "\n".join(body_lines).strip()
