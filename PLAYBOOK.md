# PLAYBOOK — Rebuilding a Production System Solo, with AI

> The operating model behind [OpenHarness](./README.md): one developer + Claude Code, an
> LLM agent harness built from scratch to production-grade standards and still iterating —
> `mypy --strict`, `ruff` clean, ≥95% coverage on CI. This is not a prompting guide. It's the working model
> that made that pace sustainable instead of reckless: **the human owns the contract, the
> agent drives the implementation, and a few hard disciplines keep the speed honest.**
>
> The code is specific to a harness. The model below is not — it transfers to any domain
> where you'd otherwise be tempted to "vibe-code" a serious system.

---

## 1. The thesis: learn by rebuilding

*What I cannot create, I do not understand.*

The fastest way I've found to actually understand a domain is to rebuild a strong reference
implementation of it — not read a tutorial, not skim the docs, but reconstruct the thing
and own every trade-off along the way. Reading teaches you *what* a system does. Rebuilding
forces you to confront *why* every part exists, because you have to make it work.

Picking the reference target is most of the bet. Three criteria:

1. **It sets or tracks the industry bar.** OpenHarness here mirrors the vocabulary of
   [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness), which itself targets Claude
   Code. You learn the real shape of the problem, not a toy's.
2. **You have a user's feel for it.** You've used it (or its peer) enough to sense what good
   looks like. Taste is what stops you from accepting a plausible-but-wrong design.
3. **It's a living project.** Learn from something still evolving, not a corpse.

The discipline that keeps a rebuild from ballooning: **only encode what the substrate
lacks.** Everything the foundation already gives you — language, framework, tooling, an
existing skill library — you assemble and reuse; you do not rewrite it. That's the same
conviction the harness itself is built on ("the harness should be thin"), applied one level
up to how the project gets built.

---

## 2. The operating model: human owns the contract, AI drives the implementation

The split that makes AI-first development work is a clean line of ownership:

- **The human stays at the contract layer** — scope, interfaces, trade-offs, acceptance
  criteria, and the call on anything irreversible. *What* gets built and *why*.
- **The agent drives the implementation** — decomposing the work, writing the code, writing
  the tests, iterating to green. *How* it gets built.

This isn't a personal preference; it's where the industry landed by 2026. Anthropic's own
framing of the operating model is **delegate, review, and own**: agents handle first-pass
execution, scaffolding, implementation, testing and documentation, while engineers review
outputs for correctness and risk and keep ownership of architecture, trade-offs, and
outcomes. The leverage comes from holding that line — not from delegating the contract, and
not from babysitting the implementation.

The failure mode on each side:

- Delegate the contract, and you get fluent code that solves the wrong problem.
- Seize the implementation (hand the agent file-by-file sub-tasks), and you've thrown away
  its main advantage — it reads the code as it exists; your plan only guesses at it — while
  demoting yourself from architect to ticket-writer.

---

## 3. The module loop

A rebuild is too large to design up front. It runs as a loop, once per module, in
dependency order:

```
  reverse-spec  ───────────────────────────────►  REFERENCE.md
  (once, up front)                                (frozen cognition map: §1–§4 + §5 module split)
       │
       │  then, per module — in the dependency order §5 fixes:
       ▼
  ┌──────────────┐      ┌───────────────────────┐
  │    DESIGN     │ ──►  │      IMPLEMENT        │ ──► commit
  │ interview-me  │      │  the solo coding loop  │
  │   + plan      │      │  (§4: TDD to green)    │
  └──────────────┘      └───────────────────────┘
```

**Build the reference frame first (`reverse-spec`).** Before writing a line, reverse the
target into `REFERENCE.md` — a cognition map, not an engineering contract. It answers "what
core elements is a professional system in this domain made of, and what problem does each
solve?" through three lenses: the annotated **directory tree** (how a serious team slices
the system), the **data flow** (how one input travels end to end), and the **core-element
concept map** (the essential problems the system must solve, discovered from the data flow —
*not* copied from the directory names). A final §5 splits those elements into an ordered
list of build modules, sequenced by dependency: first the smallest skeleton that runs one
input end to end, then layers outward. `REFERENCE.md` is frozen — it's the map you build
against, the thing that keeps the rebuild from quietly becoming a toy.

**Design each module (`interview-me` + `plan`).** Get clear on the module's role, its core
elements, and your stance on the trade-offs — then break it into a capability-level task
list with acceptance criteria. The plan stays at *capability* altitude, never sub-task
altitude: the agent decomposes better than a plan document can, because it sees the code as
it is. The plan file is kept, not deleted — on a long, exploratory rebuild it's the anchor
that lets you wander into a tangent and still find your way back.

**Implement** via the coding loop in §4.

---

## 4. The disciplines that keep the speed honest

Speed without these is just debt accrual. Each one is here because it catches a failure that
nothing downstream — not CI, not a coverage gate — can catch.

**TDD is the spine. The test is the spec.** Write the test first, *watch it go red with your
own eyes*, then write code to green. A green you never saw fail is a false green. And when a
test fails, you fix the code — you never weaken the assertion or edit the test to
manufacture green. Under "just make it pass" pressure this is the first discipline to drift,
which is exactly why it's the one held hardest.

**Review at the commit boundary, not after.** A passing test suite is not acceptance. Before
`git commit`, walk the diff against the acceptance criteria, line by line. This is the only
mechanism that catches the three things tests miss: a quietly skipped acceptance criterion,
a test written too loose to fail on a real bug, and an unrelated side-effect change
smuggled into the commit. Delegate this step and you're no longer at the contract layer —
you're praying.

**Reuse over rebuild — the thin-layer line.** Every workaround you write for something the
substrate can't yet do becomes dead weight the moment it can. Before encoding anything, ask
whether the foundation already provides it. Assemble what exists; only build what's missing.

**The trail is the memory.** On a solo project the real risk isn't lack of help — it's that
the *past you* stops helping. Decisions made three weeks ago are forgotten today. Append-only
trails (`decisions/`, `tasks/`, `learnings/`) fix this. The point isn't that anyone reads
them front to back; it's that whenever a new thought appears, you know exactly where it
goes — and that zero-friction filing is what lets a solo project sustain its pace.

---

## 5. Evidence it worked

The model above produced OpenHarness — and is still producing it. This wasn't a sprint that
shipped once and stopped; it's been weeks of sustained, self-looping iteration by one person,
with code, methodology, and documentation evolving together. Version numbers are incidental
here — the real milestone isn't a tag, it's that all three came together. What's checkable:

- **Sustained solo iteration** — ~7 weeks, 20 subsystems (engine, tools, hooks, permissions,
  observability, MCP, skills, sub-agents, sandbox, compaction, memory, an eval substrate…),
  300+ commits. The numbers are a rough impression, not the point; the point is that it kept
  going under its own loop, by one person's will.
- **Quality bars held throughout**, enforced on CI, not locally: `mypy --strict` across
  `src/`, `ruff` lint + format clean, **≥95% coverage gate**, on Python 3.10 and 3.11.
- **The full reasoning trail is preserved** — every trade-off in [`decisions/`](./decisions),
  every retrospective in [`learnings/`](./learnings), the plan/execute trail in
  [`tasks/`](./tasks). Not just *what* was built, but *why each trade-off was made*.

Why the disciplines in §4 are not optional — the industry learned this the expensive way.
Anthropic's [April 2026 Claude Code postmortem](https://www.anthropic.com/engineering/april-23-postmortem)
documents three pure *harness-layer* changes (no model change) silently degrading quality
for ~6 weeks; heavy dogfooding didn't catch it. The remediation was to run a full evaluation
on every change that touches model-facing behavior. LangChain's
[harness-engineering work](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering)
is the clean control: model held fixed, harness changes alone moving an agent from 52.8% to
66.5% — including a plausible change ("always use max reasoning") that *measurement* showed
made things worse. The lesson that generalizes: when a system's behavior is part code and
part probabilistic model, "it looks fine" and "it still passes" are not the same as "it
hasn't regressed." Verification has to be deliberate. (This project ships an eval substrate
with two consumers; turning it into a disciplined regression baseline for *every*
probabilistic change is still in progress — see §6.)

---

## 6. Honest limits

Where this model does **not** apply, stated plainly:

- **Solo only.** The whole thing assumes one person holds the contract. The moment you have
  multiple stakeholders, reviewers, or role boundaries (a PM, an architect, compliance), you
  need heavier coordination machinery this deliberately omits.
- **Needs a reference to rebuild.** "Learn by rebuilding" presupposes a strong target to
  reverse. For genuinely greenfield problems with no peer to study, the `reverse-spec` step
  has nothing to bite on — you're doing real R&D, not a rebuild.
- **The probabilistic-behavior layer is still forming.** Deterministic tests can't prove a
  prompt or memory change didn't degrade emergent behavior (§5). A disciplined regression
  baseline for that is something I'm still working out through practice rather than something
  I'd hand you as settled method.

---

## Pointers

- [`README.md`](./README.md) — project entry point and architecture
- [`REFERENCE.md`](./REFERENCE.md) — the frozen cognition map of the study target
- [`decisions/`](./decisions) · [`learnings/`](./learnings) · [`tasks/`](./tasks) — the reasoning trail
- [PLAYBOOK-PM.md](./PLAYBOOK-PM.md) — the same project through a product lens
