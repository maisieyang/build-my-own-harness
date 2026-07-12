# memory_decision Eval Dataset Card

> Phase 16 T3 substrate dataset · 2026-06-05
>
> Stage 1-5 substrate consumer for **decision surface #4 (inline
> decision class side-effects)** per [`decisions/35-eval-coverage-map.md`](../../decisions/35-eval-coverage-map.md)
> §D35.5 P0. Acceptance bar (Phase 16 plan T3): **qwen3.7-max
> warm-start PASS ≥ 4/5**; below threshold falls back to Claude
> Sonnet validation per D35.8.

---

## Reference policy(D41.5 增补,2026-07-12)

参照模型 **qwen3.7-max**(cassette 基线所在;acceptance bar
"warm-start PASS ≥ 4/5" 即在该模型上 ratify)。弱模型上的红 = 信息,
不是 gate 红(D41.5)。pre-D41.5 建档,本声明为追补。

## D35.3 three-claim header

### Capability claim

This eval verifies that the OpenHarness harness, under the
Claude-Code-style memory architecture committed in
[`decisions/36-phase-16-memory-pivot-boundary.md`](../../decisions/36-phase-16-memory-pivot-boundary.md),
lets the main LLM complete the inline write chain
**(judge → Write the `.md` body → Edit MEMORY.md index)** on
single-turn user messages without falling into either of the two
canonical failure modes the 2026-06-05 spike surfaced:

1. **Index drift** — model writes a target `.md` but silently leaves
   MEMORY.md untouched, so subsequent sessions never see the new
   entry in their startup context injection (D36.4 + D36.11).
2. **Destructive overwrite** — model emits `Write` (not `Edit`) on a
   pre-populated MEMORY.md and drops the existing entries, silently
   deleting previously-saved memory references.

The eval also verifies the LLM correctly **skips** writing for
trivial / ephemeral user messages (the "DO NOT save" discipline).

### Input spec

**Population**: N = 6 synthetic single-turn samples, hand-authored.

**Shape distribution** (per Phase 16 plan T3 acceptance):

- **2 cold-start** — empty memory dir, no MEMORY.md. Tests the
  initial-write path.
- **3 warm-start** — pre-populated with 3 seed `.md` files + a
  MEMORY.md indexing them. Tests the production-realistic path where
  the canonical failure modes manifest.
- **1 trivial-skip** — warm fixture but the user message is a
  non-memorable question; tests the LLM's restraint.

**Sample fields** (`MemoryDecisionSample`):

| Field | Source / Purpose |
|---|---|
| `case_id` | Stable identifier `M1`–`M6`; cassette key. |
| `capability` | Selects the type-classification rubric: `M-judge-preference` / `-correction` / `-project` / `-reference` / `M-trivial-skip`. |
| `shape` | `cold-start` / `warm-start` / `trivial-skip` — drives scorer dispatch (warm-start activates `NoDestructiveOverwriteScorer`). |
| `user_msg` | The conversation's only user message. The eval system prompt is the production CC-style `## Memory` rules section + the seeded MEMORY.md as `### Memory Index` (or empty placeholder when cold). |
| `expect_write` | Programmatic baseline for the `JudgmentScorer`. |
| `expected_memory_type` | Baseline for the type LLM-judge. The rubric accepts multiple defensible readings — this field is the *canonical* one, not the only valid answer. |
| `pre_populated_files` | Warm-start fixture: dict of `filename → content` seeded into the per-case temp dir before the LLM call. |
| `notes` | Per-sample design rationale. |

**Conversation transcript** (per sample): system prompt = CC memory
rules + injected MEMORY.md → single user message. NO multi-turn
follow-up. Spike showed single-turn is sufficient to discriminate
PASS / FAIL on the two canonical failure modes; multi-turn fidelity
is deferred to a later phase if dogfood surfaces demand.

### Judgment spec

5 scorer dimensions, deliberately non-collapsed (per
[`docs/ideas/eval-mentor-playbook.md`](../../docs/ideas/eval-mentor-playbook.md)
§四 4.4 — never reduce multi-dim verdicts to a single number):

| Dim | Scorer | Returns | What it catches |
|---|---|---|---|
| `memory_decision_judgment` | `JudgmentScorer` | 1.0 / 0.0 | Wrong-direction calls (wrote when shouldn't, skipped when should). Programmatic, exact. |
| `memory_decision_frontmatter` | `FrontmatterValidScorer` | 1.0 / 0.0 / NA | Malformed `.md` content (missing fields, wrong shape). Locked via shared `FRONTMATTER_RE`. |
| `memory_decision_index_update` | `IndexUpdateScorer` | 1.0 / 0.5 / 0.0 / NA | Cold-start accepts Write or Edit on MEMORY.md; warm-start requires Edit (Write gets PARTIAL=0.5, paired with the overwrite scorer for full picture). Drift (no touch) = 0.0. |
| `memory_decision_no_overwrite` | `NoDestructiveOverwriteScorer` | 1.0 / 0.0 / NA | Warm-only. Inspects Write-on-MEMORY.md content for preservation of seed anchors; <50% preserved = destructive overwrite FAIL. |
| `memory_decision_type_judge` | `MemoryTypeLLMJudgeScorer` | 1.0 / 0.0 / NA / ERROR | Per-capability LLM rubric judges defensibility of the chosen `type` field, accepting multiple valid readings where the taxonomy overlaps (preferences → `user` OR `feedback`). |

**Per-sample PASS rule** (composite for the pass bar):

A sample is considered **PASS** for the pass-bar computation when ALL
non-NA dims it produces evaluate to 1.0 (or 0.5 with no
NoDestructiveOverwrite FAIL for warm-start Write paths).

Phase 16 plan T3 pass bar:

- **qwen3.7-max warm-start PASS ratio ≥ 80%** (plan stated this as
  "≥ 4/5"; we read it proportionally so the dataset's 3 warm-start
  samples map to ≥ 3/3 — round-up because of the small N) →
  contract reaches the threshold the design assumes; Phase 16 GA
  path opens.
- **Fallback 1**: qwen3.7-max < 4/5 → re-run with Claude Sonnet. If
  Sonnet ≥ 4/5 → labeled as "model gap" and recorded in
  `learnings/phase-16.md`; Phase 16 GA still passes (the contract is
  sound, current model is below threshold per
  [[feedback-design-for-strong-model]]).
- **Fallback 2**: Sonnet also < 4/5 → contract genuinely broken;
  return to [`docs/ideas/memory-first-principles.md`](../../docs/ideas/memory-first-principles.md)
  for re-derivation. Phase 16 does NOT pass.

The single trivial-skip sample (M6) is evaluated independently of the
4/5 bar — its dim is whether the model correctly refrained from
writing.

---

## What this dataset deliberately does NOT cover

Per D35.3 + the [`first-principles`](../../docs/ideas/memory-first-principles.md) §十一 "deferred frontier"
list — out-of-scope by design:

1. **Multi-turn write fidelity** — LLM reading an existing memory
   then deciding to update it. Spike showed single-turn is sufficient
   for the canonical failure modes.
2. **Memory conflict resolution** — two memories saying contradictory
   things.
3. **Auto-GC / age-out** — MEMORY.md staying under the 200-line cap
   over time.
4. **`[[slug]]` linking semantics** — rubric is about the type
   classification, not the body's cross-references.
5. **Cross-model comparison** — D35.8 lifts the self-preference
   constraint and replaces the rubric model with a fixed third-party
   judge; this dataset's LLM-judge is single-model (D32.5 inherited).

If dogfood surfaces a real driver for any of these, the right move is
a new dataset (or a new scorer dim on this one if it fits the same
input shape), not retrofitting M1–M6.

## Discipline (mirror focus_state's dataset_card §"改 dataset 的纪律")

- **Do NOT** delete a fail case to make the score climb.
- **Do NOT** loosen a rubric to suppress a known correct-but-fail
  reading — push back via LLM-judge calibration or a new dim.
- Adding a new sample requires: explicit `capability` ID + pre-
  registered `expect_write` + `expected_memory_type` (or null) +
  rationale in `notes`.

## References

- [`decisions/35-eval-coverage-map.md`](../../decisions/35-eval-coverage-map.md) — eval coverage map (this dataset is P0)
- [`decisions/36-phase-16-memory-pivot-boundary.md`](../../decisions/36-phase-16-memory-pivot-boundary.md) — the contract under test
- [`docs/ideas/memory-first-principles.md`](../../docs/ideas/memory-first-principles.md) — first-principles derivation
- [`scripts/spike_memory_capability.py`](../../scripts/spike_memory_capability.py) — pre-T3 spike (cold + warm); informed scenario shapes
- [`src/openharness/eval/memory_decision.py`](../../src/openharness/eval/memory_decision.py) — Sample / Output / infer / runner / loader
- [`src/openharness/eval/memory_decision_scorers.py`](../../src/openharness/eval/memory_decision_scorers.py) — 5 scorers
- [`src/openharness/eval/rubrics.py`](../../src/openharness/eval/rubrics.py) — `M-judge-*` rubric registrations
