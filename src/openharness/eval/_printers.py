"""Stdout formatting helpers — shared between spike and CLI.

Extracted to single source of truth so spike script and ``oh eval``
subcommand emit identical output. Underscore-prefixed since formatting
policy is a consumer concern, not substrate API.

Public functions:

- :func:`render_message_preview` — one line per message
- :func:`print_case` — full per-case rendering (header, input, scores,
  disagreement signals)
- :func:`print_summary` — final aggregate table + self-test questions

Disagreement signals (D32.6 + Stage 3 craft):

- ``KEYWORD OVER-CLAIMS`` — keyword=1 but capability_substring=0
- ``SUBSTRING BRITTLENESS EXPOSED`` — substring=0 but LLM-judge=1
- ``LATENT FALSE-POSITIVE`` — substring=1 but LLM-judge=0
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock

if TYPE_CHECKING:
    from openharness.eval.runner import CaseResult


def render_message_preview(msg_role: str, first_block: object) -> str:
    if isinstance(first_block, TextBlock):
        return f"[{msg_role}] {first_block.text[:140]}"
    if isinstance(first_block, ToolUseBlock):
        return f"[{msg_role}] [tool_use] {first_block.name}({first_block.input})"[:160]
    if isinstance(first_block, ToolResultBlock):
        return f"[{msg_role}] [tool_result] {first_block.content[:100]}"
    if first_block is None:
        return f"[{msg_role}] (empty)"
    return f"[{msg_role}] (unknown block)"


def print_case(result: CaseResult) -> None:
    sample = result.sample
    print(f"\n{'=' * 64}")
    print(f"{sample.case_id}  [cap={sample.capability}, shape={sample.shape}]")
    print(f"{'=' * 64}")
    print(f"notes: {sample.notes.strip()}")

    print(f"\ninput ({len(sample.messages)} messages):")
    for msg in sample.messages:
        first = msg.content[0] if msg.content else None
        print(f"  {render_message_preview(msg.role, first)}")

    print(f"\nexpected_goal_keywords: {sample.expected_goal_keywords}")
    print(f"capability_assertions:  {sample.capability_assertions}")

    print("\noutput:")
    print(f"  goal:      {result.output.goal!r}")
    print(f"  next_step: {result.output.next_step!r}")

    print("\nscores:")
    for score in result.scores:
        print(f"  {score.dim:<32}  value={score.value!s:<6}  ({score.reason})")

    # Disagreement detection — 3 calibration signals (D32.6)
    score_by_dim = {s.dim: s.value for s in result.scores}
    kw = score_by_dim.get("goal_keyword_match")
    substring_dim = f"capability_{sample.capability}"
    judge_dim = f"capability_{sample.capability}_llm_judge"
    substring = score_by_dim.get(substring_dim)
    judge = score_by_dim.get(judge_dim)

    if kw == 1.0 and substring == 0.0:
        print(f"  ⚠ KEYWORD OVER-CLAIMS: keyword=1 but {substring_dim}=0")
        print("    → keyword scorer false-positive caught by substring assertion")

    if substring == 0.0 and judge == 1.0:
        print(f"  ⭐ SUBSTRING BRITTLENESS EXPOSED: {substring_dim}=0 but {judge_dim}=1")
        print("    → LLM-judge accepted what substring rejected (Stage 3 value)")

    if substring == 1.0 and judge == 0.0:
        print(f"  ⚠ LATENT FALSE-POSITIVE: {substring_dim}=1 but {judge_dim}=0")
        print("    → LLM-judge rejected what substring accepted (substring missed real issue)")


def print_summary(results: list[CaseResult]) -> None:
    print(f"\n{'=' * 64}")
    print("SUMMARY")
    print(f"{'=' * 64}")

    # Aggregate by dim — count value==1.0 across cases
    dim_to_scores: dict[str, list[float]] = {}
    for result in results:
        for score in result.scores:
            if isinstance(score.value, float):
                dim_to_scores.setdefault(score.dim, []).append(score.value)

    for dim, vals in sorted(dim_to_scores.items()):
        pass_count = sum(1 for v in vals if v == 1.0)
        denom = len(vals)
        print(f"  {dim:<32}  {pass_count}/{denom}")

    # Per-capability fail list
    cap_fails: list[tuple[str, str]] = []
    for result in results:
        cap_dim = f"capability_{result.sample.capability}"
        for score in result.scores:
            if score.dim == cap_dim and score.value == 0.0:
                cap_fails.append((result.sample.capability, result.sample.case_id))
    if cap_fails:
        print("\n  capability fails (which probe → which case):")
        for cap_id, case_id in cap_fails:
            print(f"    {cap_id}: {case_id}")

    # False-positive count: keyword=1 but capability=0
    fp_count = 0
    for result in results:
        score_by_dim = {s.dim: s.value for s in result.scores}
        kw = score_by_dim.get("goal_keyword_match")
        cap = score_by_dim.get(f"capability_{result.sample.capability}")
        if kw == 1.0 and cap == 0.0:
            fp_count += 1
    if fp_count > 0:
        print(f"\n  ⚠ false-positive count: {fp_count} case(s)")
        print("    keyword scorer passed but capability scorer caught real failure")

    print()
    print("Self-test (record in docs/ideas/eval-craft-journal.md):")
    print("  1. Which capability failed? Was it predictable?")
    print("  2. How many false-positives did keyword scorer hide?")
    print("  3. Did the same capability fail across runs?")
    print("     → if yes = prompt-level systematic failure, not model noise")
