"""Stdout printers for the memory_decision eval — shared between
the spike script and the ``oh dev eval memory_decision`` CLI subcommand
so dev and prod entries produce byte-identical output.

Parallel to :mod:`openharness.eval._printers` (focus_state's
printers); kept in a separate module so the two consumers can diverge
when their output shapes need to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openharness.eval.memory_decision import MemoryDecisionCaseResult


def print_case(result: MemoryDecisionCaseResult) -> None:
    """Per-case stdout block: header + tool_use trace + scorer dims."""
    s = result.sample
    print("=" * 68)
    print(f"{s.case_id:34} [cap={s.capability}, shape={s.shape}]")
    print("=" * 68)
    print(f"  expect_write: {s.expect_write}  expected_type: {s.expected_memory_type!r}")
    print(f"  user_msg:     {s.user_msg.strip()[:120]!r}")
    print(f"  fixture:      {len(s.pre_populated_files)} pre-populated file(s)")
    print(f"  turns:        {result.output.turn_count}")
    print(f"  tool_uses:    {len(result.output.tool_uses)} block(s)")
    for t in result.output.tool_uses:
        if t.name == "MemoryUpsert":
            body = (t.input.get("body") or "")[:80].replace("\n", "\\n")
            print(
                f"    [MemoryUpsert] name={t.input.get('name')!r} "
                f"type={t.input.get('type')!r} body={body!r}..."
            )
        elif t.name in {"MemoryShow", "MemoryDelete"}:
            print(f"    [{t.name}] name={t.input.get('name')!r}")
        else:
            print(f"    [{t.name}]")
    if result.output.text:
        print(f"  text:         {result.output.text[:200]!r}")
    print()
    for sc in result.scores:
        v = sc.value if isinstance(sc.value, str) else f"{sc.value:.1f}"
        print(f"  {sc.dim:38}  value={v:<7}  ({sc.reason})")
    print()


def print_summary(results: list[MemoryDecisionCaseResult]) -> None:
    """End-of-run aggregate: per-sample verdicts + warm-start pass bar.

    Pass bar is expressed proportionally (≥ 80%, equivalent to "4/5"
    in Phase 16 plan T3's notation) so it scales to whatever
    warm-start count the dataset has at run time.
    """
    print("=" * 68)
    print("SUMMARY")
    print("=" * 68)

    def _sample_passes(r: MemoryDecisionCaseResult) -> tuple[bool, str]:
        """Composite per-sample verdict for the pass-bar tally."""
        notes = []
        all_pass = True
        for sc in r.scores:
            if isinstance(sc.value, str):
                if sc.value == "ERROR":
                    notes.append(f"{sc.dim}=ERROR")
                continue
            v = float(sc.value)
            if v < 1.0:
                all_pass = False
                notes.append(f"{sc.dim}={v:.1f}")
        suffix = "; ".join(notes) if notes else "all dims pass"
        return all_pass, suffix

    print()
    print(f"{'Sample':28} {'shape':14} {'verdict':10} {'notes'}")
    print(f"{'-' * 28} {'-' * 14} {'-' * 10} {'-' * 30}")
    pass_count_total = 0
    warm_pass = 0
    warm_total = 0
    for r in results:
        ok, suffix = _sample_passes(r)
        verdict = "PASS" if ok else "FAIL"
        if ok:
            pass_count_total += 1
        if r.sample.shape == "warm-start":
            warm_total += 1
            if ok:
                warm_pass += 1
        print(f"{r.sample.case_id:28} {r.sample.shape:14} {verdict:10} {suffix[:50]}")
    print()
    print(f"  Overall PASS:   {pass_count_total}/{len(results)}")
    pass_bar_ratio = 0.8
    warm_ratio = warm_pass / warm_total if warm_total else 0.0
    print(
        f"  Warm-start:     {warm_pass}/{warm_total} "
        f"({warm_ratio:.0%})  ← PASS BAR (target ≥ {pass_bar_ratio:.0%})"
    )
    print()

    if warm_total == 0:
        print("  (no warm-start samples — pass bar inapplicable; check dataset)")
    elif warm_ratio >= pass_bar_ratio:
        print("  ✓ Pass bar met. Typed memory contract reached the reference threshold.")
    else:
        print("  ✗ Pass bar NOT met. Inspect failing decision dimensions before recording.")
