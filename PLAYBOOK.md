# PLAYBOOK — A Harness, a Finance Plugin, and the Method Behind Both

> One developer + Claude Code, seven weeks, three repos: a production agent **harness** built
> from scratch, a **methodology** codified into reusable skills, and a **vertical-industry
> plugin** taken into finance. This is the operating model and the convictions behind all
> three — not a prompting guide. **The human owns the contract, the agent drives the
> implementation, and a few hard disciplines keep the speed honest.**

---

## 1. What I actually believe

I don't learn by reading. I learn by rebuilding — and I turned that into a method, not a
habit. Over seven weeks I ran the same move at two altitudes: I rebuilt a production agent
**harness** from scratch, and I rebuilt a **vertical-industry plugin** from scratch.

Here's the conviction that came out of doing both. There's really **one thing — a capability
the model can call — wearing three packagings:**

> **tool · skill · plugin**
> - a **tool** is that capability **always resident** — the LLM's syscall (in this harness, `BaseTool`).
> - a **skill** is that capability **lazy-loaded** — expert context whose body is summoned on demand (here, *through* a tool: `LoadSkill`), not a tool itself.
> - a **plugin** is that capability **packaged to ship** — a skill bundled with a manifest, a version, a permission surface, and a marketplace entry: the packaging where it can be versioned, gated, and sold.

Same capability, three packagings — and the third is where engineering becomes *product*. That
packaging is how a horizontal LLM platform reaches a high-ACV vertical; it's what I read as the
bet behind Anthropic's `model + harness + plugin` push. I didn't read about it — I built all
three packagings, and ran the shipping one on top of the resident one.

Most people can describe a plugin as "a way to extend the agent." Few can name what it really
is: the packaging where a capability stops being just code and becomes something you can
version, gate, and sell. I can name it because I built the loader **and** the plugin, and saw
exactly where the technical artifact ends and the product wrapper begins.

Three things I'll defend, each with the repo that earns it:

- **Master a domain by rebuilding its best reference.** — ran twice (harness, finance).
- **The harness should be thin; the model is the product.** — [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness).
- **In a vertical, the moat isn't the model — it's the plugin as the unit you version, gate, and sell.** — [finance-skills](https://github.com/maisieyang/finance-skills).

---

## 2. The arc — one method, two altitudes, three repos

```
   study                      build                     distill
     │                          │                          │
 platform  OpenHarness    →  build-my-own-harness   →  this PLAYBOOK
 vertical  Anthropic       →  mybank-credit-risk     →  finance-skills/PLAYBOOK
           financial-services
```

Same shape, two altitudes: study the best reference, rebuild it from scratch, distill the
principles. Picking the reference is most of the bet — it has to set the industry bar, be
something you've used enough to have *taste* about, and still be alive. Three repos came out
of it, and they map onto the three packagings:

- **tool** → [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness) — the
  base: a production harness rebuilt from scratch (reference: [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness)).
- **skill** → [my-skills](https://github.com/maisieyang/my-skills) — the method itself, codified
  into reusable skills (a fork of agent-skills; I only encode what the base lacks). This is what
  makes the loop *repeatable*, not lucky.
- **plugin** → [finance-skills](https://github.com/maisieyang/finance-skills) — the vertical: the
  same move run into finance (reference: Anthropic's open-source `financial-services`; my
  from-scratch build: [`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk)).

---

## 3. The vertical chapter — verifying the plugin thesis

The harness answered "how is a horizontal platform built." It left a question open: **how does
that platform get *into* an industry — and what, exactly, is a plugin?** Anthropic's play, as
I read it, is `model + harness + plugin`, aimed at high-ACV verticals (finance, legal,
healthcare). I wanted to verify it the only way I trust — by building one.

So I ran the same move one altitude up. I studied Anthropic's open-source `financial-services`
design, then built [`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk)
from scratch — a China-bank consumer-credit-risk plugin: an agent, one `SKILL.md` written
*deep* (the judgment a ten-year underwriter never writes down) alongside thinner ones, a shared
connectors plugin, explicit trade-offs. The full vertical playbook lives in
[finance-skills](https://github.com/maisieyang/finance-skills); I'm not repeating it here.

What I *earned* by building it is the third packaging: **a plugin is, underneath, still the same
capability** — its value isn't the mechanism, it's the wrapper. The plugin is the packaging
where a capability becomes **versioned, gated, and sellable**: where engineering turns into
product, and why a horizontal platform can reach a vertical without rewriting its core.

And I closed the loop where it counts — at the seam. I taught my own harness to load
Claude-Code-format plugins (a dual-format `PluginLoader`), dropped the finance plugin into
`~/.openharness/plugins/`, and triggered `/credit-report-reviewer__parse-credit-report` on my
own runtime. The plugin loaded, the skill fired, and the model did the *right* thing: it asked
for the credit data source instead of inventing one (no bureau MCP was wired up). So **layer 1
genuinely loads and dispatches a layer-3 plugin** — the hosting path runs end-to-end on my own
harness. (Honest scope: that proves the plugin *mechanism*, not a finished credit review; the
gaps are written up in [`learnings/phase-19.md`](./learnings/phase-19.md).)

*(An accidental finding, but honest to say: doing the vertical also showed me the FDE day-job —
eliciting methodology out of domain experts — isn't where my center is. I'm a platform builder.
I only know that because I did the other thing.)*

---

> The rest of this PLAYBOOK is that method in detail — *how* I actually build, shown on the
> harness (the resident layer, where the discipline is strictest).

## 4. The operating model: human owns the contract, AI drives the implementation

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

## 5. The module loop

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
  │   + plan      │      │  (§6: TDD to green)    │
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

**Implement** via the coding loop in §6.

---

## 6. The disciplines that keep the speed honest

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
(This is the same conviction as "the harness should be thin," applied to how the project
itself gets built — and it's exactly what `my-skills` encodes.)

**The trail is the memory.** On a solo project the real risk isn't lack of help — it's that
the *past you* stops helping. Decisions made three weeks ago are forgotten today. Append-only
trails (`decisions/`, `tasks/`, `learnings/`) fix this. The point isn't that anyone reads
them front to back; it's that whenever a new thought appears, you know exactly where it
goes — and that zero-friction filing is what lets a solo project sustain its pace.

---

## 7. Evidence it holds up

The method produced all three rungs — and is still producing them. This wasn't a sprint that
shipped once and stopped; it's been weeks of sustained, self-looping iteration by one person,
with code, methodology, and documentation evolving together. Version numbers are incidental —
the real milestone isn't a tag, it's that the whole arc came together. What's checkable:

- **The arc is real, not narrated** — three shipped repos (harness / skills / vertical), and the
  vertical plugin actually *loaded and dispatched* on the harness (§3 — the hosting path, not a
  finished credit review). The method generalized across two very different altitudes; that's
  the strongest evidence it's a method and not a one-off.
- **Sustained solo iteration** — ~7 weeks, 20 subsystems in the harness alone (engine, tools,
  hooks, permissions, observability, MCP, skills, sub-agents, sandbox, compaction, memory…),
  300+ commits. The numbers are a rough impression; the point is it kept going under its own
  loop, by one person's will.
- **Quality bars held throughout**, enforced on CI, not locally: `mypy --strict` across `src/`,
  `ruff` lint + format clean, **≥95% coverage gate**, on Python 3.10 and 3.11.
- **The full reasoning trail is preserved** — every trade-off in [`decisions/`](./decisions),
  every retrospective in [`learnings/`](./learnings), the plan/execute trail in
  [`tasks/`](./tasks). Not just *what* was built, but *why each trade-off was made*.

Why the disciplines in §6 are not optional — the industry learned this the expensive way.
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
probabilistic change is still in progress — see §8.)

---

## 8. Honest limits

Where this model does **not** apply, stated plainly:

- **Solo only.** The whole thing assumes one person holds the contract. The moment you have
  multiple stakeholders, reviewers, or role boundaries (a PM, an architect, compliance), you
  need heavier coordination machinery this deliberately omits.
- **Needs a reference to rebuild.** "Learn by rebuilding" presupposes a strong target to
  reverse. For genuinely greenfield problems with no peer to study, the `reverse-spec` step
  has nothing to bite on — you're doing real R&D, not a rebuild.
- **The probabilistic-behavior layer is still forming.** Deterministic tests can't prove a
  prompt or memory change didn't degrade emergent behavior (§7). A disciplined regression
  baseline for that is something I'm still working out through practice rather than something
  I'd hand you as settled method.

---

## Pointers

The arc, in three repos:

- **tool** → [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness) (you are here) — the base harness
- **skill** → [my-skills](https://github.com/maisieyang/my-skills) — the method codified into reusable skills
- **plugin** → [finance-skills](https://github.com/maisieyang/finance-skills) — the same move run into a finance vertical

Inside this repo:

- [`README.md`](./README.md) — project entry point and architecture
- [`REFERENCE.md`](./REFERENCE.md) — the frozen cognition map of the study target
- [`decisions/`](./decisions) · [`learnings/`](./learnings) · [`tasks/`](./tasks) — the reasoning trail
- [PLAYBOOK-PM.md](./PLAYBOOK-PM.md) — the same project through a product lens
