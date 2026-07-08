"""T2 (D40 boundary) — prompt builder + hidden-test firewall (D40.3 红线).

The firewall test is the single most important test in the adapter: a leak
of gold patch / hidden test IDs into the prompt silently invalidates every
benchmark number downstream.
"""

from __future__ import annotations

from openharness.swebench.model import SWEBenchInstance
from openharness.swebench.prompt import build_prompt

GOLD_PATCH_SENTINEL = "GOLD-PATCH-SENTINEL-diff --git a/x.py"
TEST_PATCH_SENTINEL = "TEST-PATCH-SENTINEL-diff --git a/test_x.py"
F2P_SENTINEL = "TEST-F2P-SENTINEL"
P2P_SENTINEL = "TEST-P2P-SENTINEL"
HINTS_SENTINEL = "HINTS-SENTINEL frobnicate the wozzle"


def make_instance() -> SWEBenchInstance:
    return SWEBenchInstance.model_validate(
        {
            "instance_id": "astropy__astropy-12907",
            "repo": "astropy/astropy",
            "base_commit": "d16bfe05a744909de4b27f5875fe0d4ed41ce607",
            "problem_statement": "separability_matrix does not compute correctly.",
            "patch": GOLD_PATCH_SENTINEL,
            "test_patch": TEST_PATCH_SENTINEL,
            "FAIL_TO_PASS": f'["test_x.py::{F2P_SENTINEL}"]',
            "PASS_TO_PASS": f'["test_x.py::{P2P_SENTINEL}"]',
            "hints_text": HINTS_SENTINEL,
        }
    )


class TestBuildPrompt:
    def test_contains_problem_statement_and_repo(self) -> None:
        prompt = build_prompt(make_instance())

        assert "separability_matrix does not compute correctly." in prompt
        assert "astropy/astropy" in prompt

    def test_instructs_minimal_fix_and_no_test_edits(self) -> None:
        prompt = build_prompt(make_instance())
        lowered = prompt.lower()

        assert "minimal" in lowered
        assert "do not modify" in lowered and "test" in lowered

    def test_firewall_no_leak_of_gold_fields(self) -> None:
        """D40.3 红线: 泄题字段的内容绝不出现在 prompt."""
        prompt = build_prompt(make_instance())

        assert GOLD_PATCH_SENTINEL not in prompt
        assert TEST_PATCH_SENTINEL not in prompt
        assert F2P_SENTINEL not in prompt
        assert P2P_SENTINEL not in prompt
        assert HINTS_SENTINEL not in prompt

    def test_no_local_machine_paths(self) -> None:
        prompt = build_prompt(make_instance())

        assert "/Users/" not in prompt
        assert "/home/" not in prompt
