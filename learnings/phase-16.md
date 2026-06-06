# Phase 16 Retro — Memory Architecture Pivot to Claude-Code Pattern

> Closed 2026-06-06 · 6 commits over 2 calendar days (2026-06-05 to
> 2026-06-06)
>
> Boundary: [`decisions/36-phase-16-memory-pivot-boundary.md`](../decisions/36-phase-16-memory-pivot-boundary.md)
> Plan: [`tasks/phase-16-plan.md`](../tasks/phase-16-plan.md)
> First-principles trail: [`docs/ideas/memory-first-principles.md`](../docs/ideas/memory-first-principles.md)

## Commit trail

```
50bc5fe  fix(permissions): Phase 16 T5 dogfood — allow Write/Edit to project memory_dir
0b6b912  test(prompts): Phase 16 T4 — byte-identical lock for 200-line MEMORY.md cap
9780a95  feat(eval): Phase 16 T3 — memory_decision gating eval (decision surface #4)
b5be31c  feat(memory): Phase 16 T2 — deprecate Phase 11 extraction + relevance call path
78f2a90  feat(memory): Phase 16 T1 — CC-style system prompt rules + MEMORY.md injection
2d09115  docs(phase-16): prep — eval coverage map + memory pivot boundary + first-principles
```

---

## §1 What worked — the contract holds end-to-end

The dogfood session (2026-06-06 13:08, second run after the
permission fix) validates the full Phase 16 contract on
qwen3.7-max. Conversation snippet that triggered the chain:

> "我是一个工程师，我现在在寻找新的工作机会..."

What the harness produced, observed in
``~/.openharness/memory/build-my-own-harness-4cbb25d41221/``:

```
MEMORY.md                            (132 bytes, just the pointer line, no frontmatter)
user_role_and_values.md              (634 bytes, valid CC frontmatter)
```

`MEMORY.md` content (verbatim):

```
- [User Role & Values](user_role_and_values.md) — 工程师，独立工作一个月，热爱 AI/开源，通过做事认识自己
```

`user_role_and_values.md` frontmatter (verbatim):

```
---
name: user-role-and-values
description: Engineer between jobs, solo-building with AI & open source, values independence and self-discovery through making
metadata:
  type: user
---
```

**Every D36 invariant verified in production**:

| Invariant | D36.X | Status |
|---|---|---|
| Main LLM self-decides write | D36.3 | ✓ judged "user introducing self" as memorable, no harness prompting |
| LLM-visible index, not glob discovery | D36.2 | ✓ MEMORY.md emitted as pointer-line index |
| Frontmatter shape (name + description + metadata.type) | D36.10 | ✓ matches schema byte-for-byte |
| MEMORY.md has no frontmatter (it's an index, not a memory) | D36.10 | ✓ pointer line only |
| LLM understands cold-start path (Write both files, no Edit needed) | D36.3 inference | ✓ correctly chose Write × 2 (MEMORY.md didn't exist yet) |
| Memory dir per-project sha1 | D36.6 (= D28.1) | ✓ `build-my-own-harness-4cbb25d41221/` |
| `[[slug]]` linking is opt-in convention, not required | D36.5 / D36.14 | ✓ LLM didn't need it for a standalone entry, didn't force one |

**The gating eval predicted real-world behavior**: T3's
`evals/memory_decision/` returned 6/6 PASS for qwen3.7-max
including 3/3 warm-start. Dogfood confirms cold-start behavior is
likewise sound, validating the eval's choice of multi-turn
infer + real tool execution as the right fidelity level (D35.5 P0
acceptance).

**The "design for strong model" philosophy
([[feedback-design-for-strong-model]]) was upheld**: no fallback
layer was added at any phase. The contract is what it is; the
model meets it; if the model didn't, the response per the
philosophy would have been to swap up the model, not to weaken the
contract.

---

## §2 What missed — three wiring gaps the plan did not predict

### Gap A: Tier 3 permission check rejected memory_dir writes (FIXED in commit 50bc5fe)

**The collision the plan did not predict**:

- D28.1 (Phase 10): memory_dir lives at `~/.openharness/memory/<hash>/`
  by design, *outside cwd* so it can't be accidentally committed.
- P3-T3.3c (Phase 3): Tier 3 mode-based permission *rejects* any
  Write/Edit call whose path is outside cwd, asking for user
  confirmation.

D36.10/D36.11 (Phase 16) made the main LLM the memory writer. Its
write target by D28.1 design is exactly the location Tier 3
rejects. Phase 16 T1 added the system prompt section and the
session-start injection but did not extend Tier 3 to recognise the
memory dir as an implicit allowed write destination.

**The first dogfood session at 2026-06-06 ~05:00Z surfaced this as
a "permission denied (requires confirmation)" error pair** — exactly
the failure mode the methodology calls dogfood out to catch. The
plan's T5 acceptance said only "未观察到 destructive MEMORY.md
overwrite" (behavior layer) and didn't list "permission tier
通路畅通" (wiring layer). Dogfood caught what plan didn't predict.

**Fix scope was deliberately narrow**: added
`_inside_project_memory_dir(path, cwd)` that resolves
`get_project_memory_dir(cwd)` (a deterministic per-cwd function)
and treats paths under it as Tier 3 allowed. Other outside-cwd
paths still ASK as before. 8 new tests lock the narrowness — the
key one being "strict tool + `/tmp/elsewhere.txt` still surfaces
the reason" so the exception cannot accidentally turn into a
general Tier 3 relaxation. Lazy import keeps the permissions
module's import graph free of memory at load time.

### Gap B: Phase 10 `FilesystemMemoryStore.discover()` parser warns on Phase 16-shaped frontmatter (DEFERRED)

Startup log from the successful dogfood:

```
[warning] memory_missing_id  name=parenting-6yo-education
          source_path=.../user_parenting_6yo.md
[warning] memory_missing_id  name=user-role-and-values
          source_path=.../user_role_and_values.md
```

**Root cause**: Phase 10's `Memory` model (`memory/model.py`)
requires an `id` field in frontmatter. The CC-style frontmatter
mandated by D36.10 has `name + description + metadata.type` only —
*no `id`*. Phase 10's parser warns and discards these memories
when constructing the in-memory store.

**Why this didn't block T5**: the warnings are observability-level
only. The Write path (LLM emits tool_use → Write tool executes →
file lands on disk) does not go through the parser. MEMORY.md
injection at next session start also does not go through the
parser — it's a direct file read in
`_load_memory_index_for_injection`. So the *production* memory
write contract works end-to-end.

**What this does block**: the `oh memory list/show/path` CLI
subcommands consume `FilesystemMemoryStore.discover()` output. They
won't see Phase 16-written memories until the parser is relaxed.

**Scope decision**: per boundary D36 §五 ("不动: frontmatter parser
/ Memory model"), this is *explicitly out of scope* for Phase 16.
The cleanup is deferred to a future phase whose scope is
"Phase 10/11 substrate alignment to D36 schema". Recording this
here so the deferred-frontier list survives the phase boundary.

### Gap C: Phase 10 discover scans MEMORY.md as if it were a memory (DEFERRED)

Same startup log:

```
[warning] memory_missing_frontmatter
          source_path=.../MEMORY.md
```

**Root cause**: `FilesystemMemoryStore.discover()` globs `*.md` and
tries to parse every match — including MEMORY.md. But per D36.10
MEMORY.md is *deliberately* an index, *not* a memory — it has no
frontmatter by design. The discover code should skip it.

**Same scope rationale as Gap B**: boundary D36 §五 said "不动:
frontmatter parser". The fix would be a one-line skip in
`discover()`. Deferred to the same future cleanup phase as Gap B.

---

## §3 Predictions for next phase

1. **The deferred parser/discover cleanup will be cheap**. A
   ``Phase 17 — Memory substrate alignment`` boundary doc would
   need three small fixes:
   (a) drop the `id` requirement from `Memory` model (or make it
       computed from a hash of `name`);
   (b) teach `discover()` to skip MEMORY.md by filename;
   (c) update `oh memory list/show` to render the CC schema.
   Approximate cost: a single phase of the same size as Phase 16 T4
   (~ 50-100 LOC + tests + 0.5 day calendar).

2. **The next wiring audit will surface 1-3 more gaps of similar
   shape**. The pattern observed in Gap A (D28.1 ⊥ P3-T3.3c
   collision invisible at boundary-doc time, surfaced at dogfood
   time) is *unlikely to be unique to permissions*. Candidates to
   audit before next phase boundary doc lands:
   - hook layer: does any registered hook reject writes outside
     cwd? (parallel to Tier 3)
   - snapshot writer: does it correctly omit memory_dir contents
     when capturing session state?
   - compaction pass: does it preserve memory writes' tool_use
     blocks when truncating long conversations?

   Mitigation: before the next phase boundary doc commits, add an
   explicit ``§六 Wiring audit`` section listing every layer the
   new contract touches at runtime + a one-sentence verdict
   per-layer. This is a CLAUDE.md `framework altitude` discipline
   that Phase 16 prep didn't do; it would have caught Gap A
   pre-dogfood.

3. **No model-tier change required for Phase 17**. qwen3.7-max held
   the contract end-to-end in dogfood; the
   [[feedback-design-for-strong-model]] philosophy didn't have to
   activate. Next phase can stay on the same model unless its
   acceptance criteria explicitly call for stronger capability.

4. **Phase 11 extraction can be safely deleted in the cleanup
   phase**. T2 left it as a flag-gated safety net; six commits later
   no production path touches it and no rollback was needed. The
   code is dead weight at this point — the Phase 17 cleanup is the
   right place to drop the module entirely.

---

## §4 Abstractions tested

### Substrate reuse (D35.6) — ✓ confirmed

T3 built `memory_decision`'s eval **in parallel** to focus_state's
Stage 1-5 substrate rather than refactoring focus_state's typing
into a generic shape. Shared pieces (`Score` dataclass,
`CassetteStore`, `RunMetadata` persistence) reused directly with
zero change. Divergent pieces (`Sample` schema, infer call, scorer
implementations) shipped parallel. Total code reused:
roughly 60% of substrate (the genuinely cross-consumer parts);
parallel work: roughly the focus_state-specific layer. Validates
D35.6's "substrate reuse ≥ rebuild" without forcing premature
generic typing — generic typing waits for a third consumer to
make the pattern stable enough to refactor against.

### Multi-turn eval as fidelity floor — ✓ confirmed

The 2026-06-05 single-turn eval iteration produced 0/3 warm-start
PASS on qwen3.7-max because the model's defensive Read-first
strategy cut the chain off before any Write. The single-turn
result conflated "model gap" with "eval scaffold gap" — the
finding was unattributable. Multi-turn + real tool execution
produced 3/3 warm-start PASS and the dogfood (full production
path) likewise succeeded. Lesson: **single-turn observation has a
ceiling for any decision surface that requires the LLM to act on
intermediate tool results**. Future evals for decision surfaces #2,
#3, #5, #6 (per D35) should default to multi-turn unless the
surface is provably single-turn (e.g., focus_state is one
structured JSON output, no chain).

### Boundary-doc anti-scope as forcing function — ✓ confirmed for what it covered, ✗ insufficient for what it didn't

D36 §四 listed 8 explicit anti-scope items ("don't introduce glob
discovery as LLM-visible", "don't add mechanical-trigger fallback",
etc.). All 8 held throughout T1-T4. No drift, no scope creep.

But the anti-scope did *not* enumerate "audit every runtime layer
this contract crosses". Gap A's collision happened *between* two
correct designs (D28.1 + P3-T3.3c) at a layer (Tier 3 permission)
neither boundary doc mentioned. The anti-scope discipline catches
deliberate scope expansion; it does *not* catch invisible
collisions across already-shipped layers.

**Concrete improvement** for future phase boundary docs: add a
``§六 Wiring audit`` section after §五 (implementation contract). For
each runtime layer the new contract touches (permissions, hooks,
snapshot, compaction, observability), state the verdict: "unchanged
/ requires extension / requires bypass". This makes the cross-layer
collisions explicit at *boundary doc time* instead of *dogfood
time*. Cost: ~10-20 lines per boundary doc; pay-off:
catches Gap A-shaped issues before any code is written.

### Dogfood as final attribution layer — ✓ unconditionally confirmed

T5 caught Gap A. T5 was the only step in the loop that *could
have* caught Gap A — neither the substrate eval, the unit tests,
the byte-identical fixtures, nor the multi-turn eval exercised
the production permission tier (the eval bypasses permissions
entirely by talking to the LLM via the API client directly with
tool_use observed instead of dispatched). **This validates
CLAUDE.md's "Review is a walkthrough, not a stamp" principle at
the *phase* level, not just commit level**: tests + evals are
necessary but not sufficient. The final attribution always needs
real-world execution of the full stack.

---

## Closing the phase

All five tasks per [`tasks/phase-16-plan.md`](../tasks/phase-16-plan.md) shipped:

- ✓ T1 — system prompt section + session-start MEMORY.md injection
- ✓ T2 — Phase 11 extraction + relevance.py deprecation
- ✓ T3 — memory_decision gating eval (warm-start 100% on qwen3.7-max)
- ✓ T4 — byte-identical regression locks
- ✓ T5 — dogfood + this retro

Acceptance per plan T5 — "未观察到 destructive MEMORY.md overwrite"
— *trivially met* (no overwrites occurred; the dogfood was
cold-start, both writes were correct Write calls per D36.3
inference).

Deferred to Phase 17 (cleanup):

- Drop `id` requirement from Phase 10 `Memory` model
- Teach `FilesystemMemoryStore.discover()` to skip MEMORY.md
- Delete the flag-gated Phase 11 extraction code entirely
- Add `§六 Wiring audit` section to future phase boundary docs as
  default discipline
