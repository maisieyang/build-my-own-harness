# Phase 17 Retro — Memory Substrate Cleanup + Methodology Evolution

> Closed 2026-06-06 · 5 commits over half a calendar day
>
> Boundary: [`decisions/37-phase-17-boundary.md`](../decisions/37-phase-17-boundary.md)
> Plan: [`tasks/phase-17-plan.md`](../tasks/phase-17-plan.md)
> Phase 16 retro that motivated this phase: [`learnings/phase-16.md`](./phase-16.md)

## Commit trail

```
9f6a109  feat(cli): Phase 17 T4 — oh memory list/show new schema rendering (D37.4)
3734ccc  fix(memory): Phase 17 T3 — discover() skips MEMORY.md (D37.5)
5454975  refactor(memory): Phase 17 T2 — Phase 11 extraction full deletion (D37.3)
934b044  feat(memory): Phase 17 T1 — parser accepts CC frontmatter (D37.1 + D37.2)
57b6d48  docs(phase-17): prep — boundary doc + capability plan + CLAUDE.md methodology evolution
```

Code net: **-1598 lines** (T1 +113, T2 -1849, T3 +36, T4 +102 across source + tests). The big T2 number is the deletion-heavy refactor that ripped out the Phase 11 extraction stack — code, config, CLI surface, tests.

---

## §1 What worked

### The user-facing payoff landed cleanly

The whole point of Phase 17 was that ``oh memory list`` couldn't see the memories the Phase 16 dogfood wrote. T4 closed that — with the project's real memory dir on disk, the user now sees::

```
parenting-6yo-education  user  User has a ~6-year-old child …
user-role-and-values     user  Engineer between jobs, solo-bu…
```

Both are Phase 16-dogfood-written D36.10 CC-schema files. Both required all four of T1-T4 to be visible:

- **T1** taught the parser to accept ``id``-less CC frontmatter and pull ``type`` from the nested ``metadata.type``.
- **T2** removed the Phase 11 ``ExtractionSettings`` machinery so the model couldn't be tempted to fork between two write paths.
- **T3** silenced the startup ``memory_missing_frontmatter`` warning on MEMORY.md.
- **T4** trimmed ``oh memory list`` to the three D36.10-aligned columns and switched the sort key to alphabetical.

No re-dogfood needed — the existence of the on-disk Phase 16 memories was itself the regression case T4 verified against.

### §六 Wiring audit prediction held — zero cross-layer collisions during execution

The Phase 16 retro's main methodology recommendation was to add a ``§六 Wiring audit`` section to every future phase boundary doc enumerating runtime layers + verdicts. D37 §六 audit predicted:

- 8 layers ``unchanged`` (permissions, hooks, snapshot writer, session_memory checkpoint, compaction L1-L4, API client, skill/plugin/sub-agent, eval substrate)
- 1 layer ``requires extension`` (CLI surface — ``oh memory list/show`` adapts; ``--no-extract`` deleted)
- 1 layer ``requires update`` (observability — the ``memory_missing_id`` + extraction-related events disappear)

**Execution validated all 10 predictions.** No Phase 16-style Gap A surfaced. The collisions T2 had to handle (test files passing ``extract_enabled=False`` to ``QueryContext``) were *test surface* fallout from removing a runtime-layer kwarg, NOT a missed cross-layer audit. The audit was at the right granularity.

This is the first phase to ship under the §六 discipline. One data point, not statistical evidence — but the prediction-execution match is exactly what the discipline is supposed to produce.

### The parallel-substrate pattern from Phase 16 T3 paid off in Phase 17 T3

Phase 16 T3 chose to build ``memory_decision``'s eval substrate **in parallel** to focus_state's instead of refactoring focus_state's into a generic shape. Phase 17 T3 demonstrated the same hands-off discipline: the boundary doc D37.5 said "filter the glob in ``discover()``", but ``discover()``'s actual glob lives in the parent class ``FilesystemMarkdownStore._merge_dir`` shared by 5 stores (commands / skills / bundles / hooks / memory). Filtering there would couple substrate to memory's reserved-name convention. The cleaner implementation moved the early-return into ``parse_memory(path)`` — a memory-specific parser — leaving the substrate content-agnostic. **The substrate boundary held even when boundary doc text suggested touching it.**

### The phase shipped fast

Five commits, half a calendar day. Cleanup phases scale faster than capability phases because the requirement is "make this delta land" not "design this contract" — anti-scope discipline + the §六 audit narrowed the workspace to almost exactly what was actually touched.

---

## §2 What missed

### LoC prediction was off by ~5×

D37 boundary doc estimated "≈ -300 LoC". Actual code net: **-1598 LoC**.

Where the prediction missed:

- Source code (services/extract.py + cli.py + engine + config edits): roughly -500 source LoC, **close to the prediction**.
- Test surface: the ``-1098`` delta beyond prediction came almost entirely from test deletion:
  - ``test_extract.py`` (≈ 350 lines, deleted whole)
  - ``test_e2e_phase11.py`` (≈ 280 lines, deleted whole)
  - ``test_extract_engine_integration.py`` (≈ 200 lines, deleted whole)
  - ``test_compact_extraction_settings.py`` (7 ExtractionSettings test classes ≈ 80 lines removed)
  - ``test_compact_repl.py`` (3 ``--no-extract`` tests ≈ 50 lines removed)
  - 5 engine wiring test files sed-stripped of ``extract_enabled=False,`` kwarg lines (≈ 5 lines but across 5 files)

**Lesson**: future boundary doc LoC predictions should split into "source LoC" and "test surface LoC". The latter dominates on deletion-heavy phases — sometimes 2-3× the source delta. Phase 16 T2 deletion would have been a calibration data point if we'd asked then.

### Plan T2 acceptance missed listing the test fallout

The plan T2 acceptance checklist enumerated which source files to delete + which to edit, but didn't list the *indirect* test cleanup needed: five engine/services test files used ``extract_enabled=False,`` as a kwarg in their ``QueryContext`` fixture constructors and broke at runtime when the kwarg disappeared. A sed loop fixed it in seconds, but the work wasn't anticipated.

The pattern: **when removing a struct field, any test that constructs that struct breaks**. Acceptance checklists for similar "remove a field" tasks should add a ``grep`` step naming the test files that touch the field, executed at plan-write time.

### D37.1 text was narrower than the actual T1 scope

The boundary doc's D37.1 said "Memory.id field becomes optional". The actual T1 code change covered four things:

- ``id`` becomes optional, auto-generated when missing (the literal D37.1)
- ``scope``, ``created_at``, ``updated_at`` auto-fill from sensible defaults (D37.2 spirit, but not enumerated in D37.1)
- ``type`` accepted from both top-level ``type:`` and the CC ``metadata.type`` nested location (not in the boundary doc at all)
- ``signature`` already auto-computed from body+type+scope (pre-existing behavior, kept)

The ``metadata.type`` nesting was discovered by inspecting the 2026-06-06 dogfood-written file mid-T1. **The boundary doc should have grep'd a real D36.10 file before listing D37.1's contract**. The fix is mechanically the same — one ``if`` block in ``parse_memory`` — so the scope expansion was zero-cost, but the boundary doc text didn't match reality.

**Lesson**: when the contract under change is "accept a real-world artifact shape", the boundary doc should be **drafted with the artifact open in another buffer**. D37.1 read like a thought experiment; the eventual implementation was empirical.

---

## §3 Predictions for next phase

There may not be one immediately. Phase 17 closes the Phase 16 retro's deferred frontier (Gap B + Gap C + Phase 11 extraction cleanup + the §六 methodology change). The harness is in a clean state with no known capability gaps blocking the next dogfood. Per [[feedback-stop-at-marginal-decline]]:

- **D35 P1 (#2 tool selection eval + #3 agentic loop eval)** — high ROI in absolute terms, but **no current dogfood driver**. The user isn't reporting tool-selection bugs or loop-control surprises.
- **Phase 17-style cleanup loops** — nothing left to clean unless dogfood produces another Gap A-shaped finding.
- **Non-code work** — the user has mentioned a job search; the harness's actual value for the next 1-2 weeks may be as a portfolio artifact, not a build target.

The disciplined move is **pause and let a real driver surface**. The next phase doesn't have to be next week; it can be next month or next quarter. Phase 17's existence proves the loop can absorb a small retro-driven cleanup phase any time the deferred frontier needs flushing.

If a driver does surface, expect it to look like Phase 16's dogfood finding: a real user trying something the system mostly supports but where one runtime layer wasn't wired. The §六 audit discipline shipping now means that finding gets caught at boundary-doc time.

---

## §4 Abstractions tested

### §六 Wiring audit as a forcing function (NEW in this phase)

Tested: ✓ confirmed at one data point. D37 §六 enumerated 10 runtime layers + 10 verdicts; execution matched 10/10. The discipline cost was about 15 lines in the boundary doc — a tiny overhead for the prediction value it delivered.

**Open question**: is the predicted-verdicts approach robust across phases with different shapes? Phase 17 was deletion-heavy (lots of layers obviously ``unchanged`` because deletion doesn't propagate). Phase 18-whatever may have a contract that crosses more layers. The discipline will be more strenuously tested then.

### Anti-scope discipline (Phase 16 D36-shaped)

Tested: ✓ Held during T2 (didn't expand into "redesign frontmatter schema"); held during T3 (didn't push the filter up into the parent class); held during T4 (didn't add a ``--columns`` flag or ``oh memory edit``). The anti-scope items in D37 §四 stayed binding.

### Test-fallout grep at plan time

Tested: ✗ Not done in advance for T2; the consequence was 5 minutes of catch-up cleanup. Low cost, but a methodology gap. **Next-phase boundary doc should add a "test files referencing X" grep step under D37.3-shaped acceptance checklists.**

### Real-artifact-driven boundary doc text

Tested: ✗ D37.1's text was conceptually right but textually narrower than reality. The gap surfaced at code-write time, not at boundary-doc time. The methodology adjustment: **when D37.X mentions a schema-shape, include a worked example of the actual on-disk artifact in the boundary doc** (not just the spec'd shape). The Phase 17 retro can't fix it retroactively — but Phase 18+ should.

### LoC prediction at boundary-doc time

Tested: ✗ ~5× off. **Next boundary doc should split LoC into source vs. test predictions** OR adopt a rule like "deletion phases predict 2-3× the source delta". Both refinements are cheap.

---

## Manual verification record (per plan T5 acceptance)

After all five Phase 17 commits, on the project's real memory dir (``~/.openharness/memory/build-my-own-harness-4cbb25d41221/``) with the Phase 16 dogfood-written ``user_role_and_values.md`` and ``user_parenting_6yo.md`` files in place:

```
$ uv run oh memory list
parenting-6yo-education  user  User has a ~6-year-old child …
user-role-and-values     user  Engineer between jobs, solo-bu…

$ uv run oh memory show user-role-and-values
---
name: user-role-and-values
description: Engineer between jobs, solo-building with AI & open source, ...
metadata:
  type: user
---

工程师，当前在寻找新的工作机会，已经独处并持续独立工作了约 1 个月 ...
```

No warnings on startup. The Phase 16 retro's Gap B + Gap C are closed.

### Addendum: emit-only-``user`` type observation (2026-06-06 dogfood)

A second dogfood right after T5 closed surfaced a follow-on
finding worth recording — it's not a Phase 17 regression, it's
a *new* observation for whichever future phase reopens the
memory-eval frontier.

The user shared three distinct kinds of memorable content in a
single ``oh chat`` session: project state ("my project is
called build-my-own-harness, finishing in two days"), a
correction ("I said 打气加油 — encouragement — not 打球, play
ball"), and continued user-facts ("MIT-licensed open source,
solo-built a commercial Python project"). qwen3.7-max wrote all
three by *editing the existing* ``user_role_and_values.md`` three
times in the same turn — never creating a new ``project-*.md``
or ``feedback-*.md`` file, never branching the taxonomy.

What this means at the contract layer:

- D36.10 defines four memory types (user / feedback / project /
  reference). The system prompt enumerates all four with
  examples.
- The Phase 16 ``memory_decision`` gating eval's M-judge-*
  rubrics check that ``type=X`` is **defensible** given the user
  input — they do NOT check that the model **chose** the right
  type to begin with, or that the model **created a separate
  file** when the new content belongs to a different type than
  the existing memory.
- So the model is contract-compliant ("wrote a memory, type was
  ``user``, frontmatter valid, MEMORY.md indexed correctly") while
  also producing a **single growing ``user_role_and_values.md``**
  that absorbs everything because there's no rubric pressure to
  separate.

Three independent failure modes hide inside this same observation:

1. **Type bias** — qwen3.7-max may have a prior that ``user`` is
   the safe default. Could test by giving a clearly-project-only
   user message and checking whether type=user still wins.
2. **File hygiene** — the model treats "memory exists for this
   user" as license to extend that file rather than spawning a
   new one per topic. Could test by checking whether new
   content with different ``type`` than the existing file
   creates a new ``.md`` or grows the old one.
3. **Topic coupling** — even within ``user`` type, three
   thematically different topics (engineer role / fever / project
   completion) ended up in one file. Could test by checking the
   per-file topical cohesion.

Phase 18+ memory-eval iteration candidates this surfaces:

- A new sample family ``M-type-discrimination`` where the user
  message is unambiguously ``project`` or ``reference``; rubric
  scores whether the chosen ``type`` matches the unambiguous
  ground truth.
- A new programmatic scorer
  ``MemoryFileSeparationScorer`` that compares the post-write
  state against the pre-write fixture and verifies that
  topic-distinct content lands in a new file rather than
  extending an existing one.
- A "memory hygiene" rubric for warm scenarios where the model
  must decide between editing an existing file and creating a
  new one.

This is **not actionable inside Phase 17** — Phase 17's
acceptance was "the parser accepts CC files and the CLI shows
them"; the hygiene of the model's *writing* behavior is upstream
of Phase 17 scope. Recording here so the deferred frontier picks
it up if Phase 18+ reopens the eval coverage map at decision
surface #4.

---

## Closing the phase

All five plan tasks shipped:

- ✓ T1 — parser accepts CC frontmatter (D37.1 + D37.2)
- ✓ T2 — Phase 11 extraction full deletion (D37.3)
- ✓ T3 — ``discover()`` skips MEMORY.md (D37.5)
- ✓ T4 — ``oh memory list/show`` D37.4 rendering
- ✓ T5 — CLAUDE.md §六 Wiring audit discipline (shipped in prep commit 57b6d48) + this retro

Phase 17 GA: green. Test suite at 2057 / 2057 pass + 8 skipped (Docker/gVisor integration). No known wiring gaps. Phase 16 retro's deferred frontier flushed.

The harness is in the cleanest state it's been in across the recent phase loop — Phase 16 pivoted the architecture, Phase 17 finished the cleanup, and there's no next-phase commitment forced by the retro. The right move is **stop and wait for a real driver** before opening Phase 18.
