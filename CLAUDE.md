# CLAUDE.md — How this project was built with Claude Code

> This file documents the human-AI collaboration model used to build
> OpenHarness v0.1.0 in **23 days across 17 phases**, and to drive
> the post-v1 releases that followed on the same loop. It serves a
> dual purpose:
>
> 1. **Public case study** — a reproducible methodology for anyone
>    curious about deliberate AI-assisted development. The pattern is
>    domain-agnostic; the specific harness it produced is just the
>    artifact that proved it works.
> 2. **Live agent guidance** — Claude Code reads this file when
>    working on the repo. The rules below shape its behavior, so
>    they're written as both observations and instructions.
>
> The methodology's payoff is documented quantitatively in
> [`learnings/phase-7.md`](./learnings/phase-7.md) — the project-
> level meta-retrospective.

---

## The premise

A single developer + Claude Code as collaborator, building a
production-grade Python LLM harness as a deliberate learning project.

- **Coordination cost ≈ 0**; decisions are mostly reversible
- **Starting input**: [`REFERENCE.md`](./REFERENCE.md) — the reverse-
  engineered specification of HKUDS/OpenHarness v0.1.7 (study target,
  not copy source — see attribution at the top of that file)
- **Dual goal**: (1) ship a production-grade harness, (2) become a
  domain expert through implementing it phase by phase
- **Strategy**: capability-level spec → agent autonomous build → human
  review at the contract layer

---

## The four-step phase loop

Each phase runs the same loop:

| Step | Where it lives | Granularity | Who decides |
|---|---|---|---|
| **1. Boundary doc** | [`decisions/NN-phase-X-boundary.md`](./decisions) | What's in scope / out / which invariant holds | Human |
| **2. Plan** | [`tasks/phase-X-plan.md`](./tasks) | Capabilities + acceptance criteria (NOT sub-tasks) | Human |
| **3. Execute** | Claude Code Plan / Execute modes | Sub-tasks resolved at runtime by the agent | Agent |
| **4. Retro** | [`learnings/phase-X.md`](./learnings) | What was learned, what to predict next | Human (with agent draft) |

The loop's most counterintuitive property: **steps 1 and 4 take more
calendar time than step 3**, but step 3 absorbs that investment with
compound interest in subsequent phases. The meta-retro §3.1 documents
this with the Phase 7a/7b/7c sequence: 7c shipped in **12% the LoC of
7b** because the substrate Protocol was designed correctly in 7a.

---

## Spec at the right altitude — capability, not sub-task

The plan at step 2 must be **capability-level**, not sub-task-level.
Reasoning: sub-task decomposition is the agent's strength. If the
human pre-decomposes, the spec becomes brittle and the agent's
autonomy gets wasted on busywork.

Example at the right altitude:

> ✅ "P1-T4: `oh ask` streaming output + human-readable error
> messages + integration tests gated behind real API key"

Same capability expressed at the wrong altitude (over-specified):

> ❌ "4a: implement Settings → 4b: write mock client → 4c: real
> client → 4d: integration test → 4e: `__init__.py` exports"

The over-specified version isn't *wrong*, it's just **redundant work
for the human** — the agent would decompose it the same way anyway,
and if the agent's decomposition differs from the human's, the
agent's is usually right (it's looking at the actual code, the human
is looking at the plan doc).

---

## When the agent must stop and ask

The agent drives sub-tasks autonomously by default. It stops and
escalates when one of three categories surfaces:

1. **External contract decisions** — public API shape, environment
   variable names, new dependencies, anything visible from outside
   the package boundary
2. **Irreversible operations** — file deletions, schema migrations,
   public interface changes, `git filter-repo`, force-pushes
3. **Capability description is wrong** — the agent discovers the
   boundary doc's invariant can't hold as stated, or the plan's
   acceptance criteria are mutually inconsistent, or "the premise of
   this task is wrong"

The first two are about **blast radius**; the third is about
**epistemic honesty** — surface the wrong premise before continuing,
don't paper over it.

---

## Review before commit — never auto-commit on GREEN

After tests pass GREEN and **before any `git commit`**, the agent
walks through the diff against the acceptance criteria, one checkbox
at a time. The walkthrough is the review checkpoint; "tests GREEN →
auto-commit" is explicitly rejected.

This catches:

- The agent technically passed tests but silently skipped an
  acceptance criterion
- A test was loose enough to GREEN even though the feature is broken
  in production
- A side change drifted into the diff without belonging to the
  current capability

Once the human signs off via the walkthrough, the commit is created
in the same turn. No "I'll commit it" without showing the diff first.

---

## Document roles — the trail this repo preserves

| File / directory | Role | When written |
|---|---|---|
| [`REFERENCE.md`](./REFERENCE.md) | Starting input — HKUDS/OpenHarness v0.1.7 reverse-engineered spec (study target) | Once, at Phase 0 |
| [`SPEC.md`](./SPEC.md) | Project contract — what's built, what's not, the behavioral rules | Rarely changes |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Strategy — tier division, phase ordering, dependency graph | Adjusted between phases |
| [`tasks/`](./tasks) | Current-phase plan at **capability** granularity, never sub-task | Before each phase |
| [`decisions/`](./decisions) | **Only** external constraints + irreversible / non-obvious trade-offs (not "we used `uv` instead of `pip`" trivia) | At decision time |
| [`learnings/`](./learnings) | Per-phase retrospectives — abstractions tested, which held, which broke, predictions for next phase | After each phase ships |

The point of the trail is **not** that future readers will re-read it
linearly. The point is that **at every moment during the project,
the human always knows where to add a new thought** without thinking
about where it goes. That low friction is what enabled v0.1.0's
17-phase ship in 23 days and keeps subsequent releases on the same
cadence.

---

## Tone — staying at framework-builder altitude

Conversations about the project happen at the contract / abstraction
layer. Once the human has internalized framework-builder mindset, the
agent does not regress conversation to "add a field here" detail level —
it engages at the boundary-doc / invariant / trade-off layer.

Example of the right tone (the agent surfaces ratification points):

> User: Let's plan Phase 6.
>
> Agent: Phase 6 (Sub-agent) is the third compounding test of Phase
> 3's abstraction. Before I draft the boundary doc, here are the
> 4 design ratifications you need to make: (1) is
> `ToolExecutionContext.parent_query` an acceptable expansion point
> for sub-agent's recursion? (2) Which 3 fields does the sub-context
> override? (3) Depth bound = 3 or other? (4) Default-register the
> `Agent` tool or opt-in? Once you ratify, I write D16.1-D16.8.

Example of the wrong tone (regressing to detail level):

> User: Let's plan Phase 6.
>
> Agent: I'll create a file at `src/openharness/tools/spawn_agent.py`
> with a class `SpawnAgent(BaseTool[SpawnAgentInput])` that has an
> async `execute` method. Should I make the `name` field default to
> "Agent"?

The first response is at framework altitude — design space + trade-
offs. The second is at implementation altitude — file paths + class
names. **The human's time is worth the first kind of conversation,
not the second.**

---

## Why this works

See [`learnings/phase-7.md`](./learnings/phase-7.md) §3 — five
framework-level lessons backed by quantitative evidence:

1. **Abstraction-first compounds** — Phase 7c shipped at 12% of
   7b's LoC because the substrate Protocol was designed once in 7a
2. **Layered model holds cross-cutting load** — Phase 5d touched 4
   layers; 11 protected directories showed zero diff
3. **Additive kwarg = right extension shape** — Phase 5e + 6+:
   default = old behavior, opt-in = new feature, existing tests
   byte-identical
4. **Source-agnostic catalog** — Phase 5f added a second producer
   at 60% the cost of the first
5. **API-level zero-diff = right refactor invariant** — Phase 8
   extracted a shared module; 233 caller tests unchanged

These are not aspirations. They are quantitative facts produced by
running the four-step phase loop seventeen times. The methodology
documented in this file is the load-bearing assumption underneath
those numbers.

---

## For someone applying this pattern to a different project

The methodology transfers if you preserve these three properties:

1. **The human stays at the contract layer.** Boundary docs and
   acceptance criteria; never sub-task decomposition or specific
   class names. The moment the human writes a sub-task plan, the
   agent's autonomy is wasted on it.
2. **Every phase ends with a retro before the next phase opens.**
   The retro forces honest "what did I learn / what to predict for
   the next phase" reflection. Skipping it accumulates technical and
   architectural debt that's invisible until phase N+3.
3. **Review is a walkthrough, not a stamp.** Test pass alone is not
   acceptance; the human reading the diff against acceptance criteria
   is. This is the only mechanism that catches "tests passed but
   feature is wrong" — and that mechanism cannot be delegated.

Everything else (specific file names, the four-step structure, the
boundary-doc / plan / retro split) is implementation detail of these
three properties. Adapt the implementation to your domain; keep the
properties.
