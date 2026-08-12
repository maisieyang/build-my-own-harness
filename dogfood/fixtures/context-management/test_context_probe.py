from pathlib import Path

from context_probe import read_anchors


def test_large_context_has_stable_head_middle_tail_anchors() -> None:
    path = Path(__file__).with_name("large_context.txt")
    assert read_anchors(path) == (
        "HEAD_ANCHOR=context-head-0812",
        "MIDDLE_ANCHOR=context-middle-0812",
        "TAIL_ANCHOR=context-tail-0812",
    )


def test_context_facts_keep_the_exact_verification_command() -> None:
    facts = Path(__file__).with_name("context_facts.md").read_text(encoding="utf-8")
    assert "uv run pytest test_context_probe.py -q --no-cov" in facts
