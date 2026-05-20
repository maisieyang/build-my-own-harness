# tasks/ — Phase-level Plan Trail

> Append-only history of the **17 phases that shipped OpenHarness in
> 23 days**. Each phase's plan was written *before* that phase's code,
> frozen at capability granularity (never sub-task), and never edited
> afterward — reflection happens in [`../learnings/`](../learnings),
> not by rewriting the plan.

---

## The three trail directories

This is one of three append-only trails preserved at the repo root.
Together they reconstruct every design decision in the project:

| Directory | Contains | When written |
|---|---|---|
| [`../decisions/`](../decisions) (24 files) | Boundary docs — what's in scope / out / which invariant holds | **Before** each phase |
| [`tasks/`](.) (this directory, 22 files) | Capability-level plans + Phase 0 previews | **Before** each phase |
| [`../learnings/`](../learnings) (31 files) | Per-phase retrospectives — abstractions tested, which held, what to predict next | **After** each phase ships |

Reconstruct any phase's design rationale by reading in order:
**boundary doc → plan → retro**. The canonical ship-order timeline
lives in [`../learnings/phase-7.md`](../learnings/phase-7.md) §2 (the
project-level meta-retrospective).

---

## Phase index (in ship order)

| # | Phase | Capability | Plan files |
|---|---|---|---|
| 1 | Foundation + Hello LLM | Project scaffolding, Pydantic protocols, OpenAI-compatible client, streaming, CLI | [`plan.md`](./plan.md), [`todo.md`](./todo.md) |
| 2 | Tool Loop | `BaseTool` + `ToolRegistry` + `run_query` + 5 built-in tools | [`phase-2-plan.md`](./phase-2-plan.md), [`phase-2-todo.md`](./phase-2-todo.md) |
| 3 | Safety + Observability | 3-tier permission system, 5-event hook middleware, structured logging | [`phase-3-plan.md`](./phase-3-plan.md) |
| 4 | Compaction (Microcompact) | Per-tool-result truncation + reactive `PromptTooLong` retry | [`phase-4-plan.md`](./phase-4-plan.md) |
| 5 | MCP (federated tools) | stdio transport adapter for Model Context Protocol servers | [`phase-5-plan.md`](./phase-5-plan.md), [`phase-5-preview.md`](./phase-5-preview.md) |
| 5c | Skills (lazy-loaded expertise) | Markdown skill catalog + `LoadSkill` tool | [`phase-5c-skills-plan.md`](./phase-5c-skills-plan.md), [`phase-5c-skills-preview.md`](./phase-5c-skills-preview.md) |
| 5b | Slash Commands | User-authored `~/.openharness/commands/*.md` invoked as `/<name>` | [`phase-5b-plan.md`](./phase-5b-plan.md) |
| 6 | Sub-agent (recursive dispatch) | `SpawnAgent` tool with depth limit + immutable context inheritance | [`phase-6-plan.md`](./phase-6-plan.md) |
| 7a | ExecutionEnvironment substrate | Protocol for tool execution substrate + `HostExecution` identity transform | [`phase-7-plan.md`](./phase-7-plan.md), [`phase-7-preview.md`](./phase-7-preview.md) |
| 7b | Docker sandbox | `SandboxExecution` via `aiodocker` | [`phase-7b-plan.md`](./phase-7b-plan.md) |
| 5d | ModeBundle (first cross-layer tenant) | Compose system prompt + tool whitelist + deny paths + hooks into one "mode" | [`phase-5d-plan.md`](./phase-5d-plan.md) |
| 5e | Plugin hook discovery (entry points) | Third-party Python packages contribute hooks via `openharness.hooks` group | [`phase-5e-plan.md`](./phase-5e-plan.md) |
| 8 | `markdown_store/` refactor (rule-of-three) | Extract shared frontmatter + filesystem-store primitives after 5b/5c/5d duplication | [`phase-8-plan.md`](./phase-8-plan.md) |
| 5f | Filesystem hook plugins (`*.py` discovery) | Second producer for the hook catalog | [`phase-5f-plan.md`](./phase-5f-plan.md) |
| 7c | gVisor runtime kwarg | `--sandbox-runtime runsc` opt-in (12% the LoC of 7b) | [`phase-7c-plan.md`](./phase-7c-plan.md) |
| 6+ | `oh chat` multi-turn REPL | Conversation history via new `ConversationCompleteEvent` | [`phase-6plus-plan.md`](./phase-6plus-plan.md) |
| 7 | Production polish + SPEC v1 closeout | README rewrite, `oh tools/config/hooks` subcommands, packaging, tutorial, meta-retro | [`phase-7-final-plan.md`](./phase-7-final-plan.md) |

---

## Why some phases got sub-letters

`5a/5b/5c/5d/5e/5f`, `7a/7b/7c`, `6+` — these are not after-the-fact
sub-divisions. They emerged as **independently shippable capabilities**
during the original phase's execution. Each got its own boundary doc
+ plan + retro.

- **Phase 5** ("Extensibility") split into six along the
  cross-cutting axes that surfaced (MCP / Slash / Skills / Bundles /
  Plugins / Filesystem)
- **Phase 7** ("Production polish") split when execution-substrate
  abstraction emerged before sandbox implementation (7a Protocol
  → 7b Docker → 7c gVisor)
- **Phase 6+** is an additive on top of Phase 6 (multi-turn REPL on
  top of the sub-agent recursion primitive)

Meta-retro §2 explains why this matters: tighter phase scope means
stronger refactor invariants, which means compounding abstraction
quality. The decision rule that emerged: when execution surfaces
a new capability that **can be invariantly tested separately**,
spin off a sub-letter phase rather than expanding the current scope.

---

## What this directory is NOT

- **Active todo** — once written, plans were frozen at capability
  granularity. Reflection went into `learnings/`, not into plan edits.
- **A roadmap** — Phase 7 closed SPEC v1 on 2026-05-20. See
  [`../learnings/phase-7.md`](../learnings/phase-7.md) §10 for the
  self-evaluation against the original SPEC, and the same retro's
  "Optional follow-ups" section for what was acknowledged but not
  built.
- **Sub-task decomposition** — by design, plans stay at capability
  level. Sub-tasks were resolved at runtime by Claude Code; see
  [`../CLAUDE.md`](../CLAUDE.md) "Spec at the right altitude" for
  the rationale.

---

## Reading recommendations

If you have **5 minutes**, read just the meta-retro (`../learnings/phase-7.md`).

If you have **30 minutes**, read in this order:
1. [`../SPEC.md`](../SPEC.md) — what the project committed to
2. [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §2-4 — the tier division + phase ordering
3. [`../learnings/phase-7.md`](../learnings/phase-7.md) — the meta-retro
4. Pick one phase that caught your eye from the index above; read
   its boundary doc (in `../decisions/`) + plan (here) + retro
   (in `../learnings/`) as a single triplet

If you have **a weekend**, read the project's commit history in
parallel with the boundary→plan→retro triplets above. The 195 commit
messages were written carefully and stand on their own.
