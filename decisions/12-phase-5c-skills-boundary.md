# Phase 5c Boundary — Skills (Lazy-Loaded Expertise)

> Status: locked at Phase 5c entry, 2026-05-15.
>
> Scope note: **this boundary covers Skills only**. Slash command (Phase 5b)
> stays in `tasks/phase-5-preview.md`; Phase 5a MCP boundary is in
> `decisions/11`.
>
> Rationale + framing: see `tasks/phase-5c-skills-preview.md` (the deep
> first-principles unification — Skill loading = LLM-as-RPC + tool
> dispatch pattern, NOT a new mechanism).

## Triggering observation

After Phase 5a (MCP) and Phase 6 (Sandbox preview) both validated the
**cross-cutting invariant** (permission/hook/engine zero-change for new
substrates), Skills emerge as the **third independent test** of the same
abstraction:

- MCP: external **tools** federated via the same BaseTool interface
- Sandbox: external **execution environment** swapped in via QueryContext
- Skills: external **knowledge** lazy-loaded via the same tool dispatch

If Skills land cleanly under the same invariant — and they will, because
the unifying insight is that all three are instances of the same
LLM-as-RPC pattern — then Phase 3's hook/permission abstractions are
empirically proven stable, not over-designed.

---

## In scope

**L1 — Skill file format: markdown + YAML frontmatter.**

```markdown
---
name: react-testing-patterns
description: When to write React component tests; what patterns to use
version: 1                       # optional
---

When writing tests for React components, follow these principles:
1. Test behavior through user interactions, not implementation
2. ...
```

Same shape as Claude Code Skills, test-gen-agent Skills, and the
broader markdown-with-frontmatter convention. `name` and `description`
are required; other frontmatter fields are tolerated but ignored
(forward-compat).

**L2 — Storage: two layers, project overrides global.**

| Layer | Path | When used |
|---|---|---|
| Global | `~/.openharness/skills/<name>.md` | User-wide expertise |
| Project | `<cwd>/.openharness/skills/<name>.md` | Project-local expertise |

If both define a skill with the same `name`, project wins. Same
override semantics as git config (P5 preview D15.2 precedent).

**L3 — Catalog injection: always-on, names + descriptions only.**

At CLI bootstrap, scan both storage layers, parse all frontmatter,
build a single "Available Skills" section in the system prompt:

```
## Available Skills (call LoadSkill to expand)

- react-testing-patterns: When to write React component tests; what patterns to use
- sql-perf-tuning: How to diagnose and fix slow Postgres queries
- ...
```

Always-on (not LLM-gated) because catalog is tiny — each line is
< 50 tokens, 100 skills = ~5K tokens. The complexity of LLM-gated
filtering doesn't pay for itself until skills count grows much larger
(at which point it's a different problem: RAG-over-skills, see Out
of Scope).

**L4 — Body loading: lazy via `LoadSkill(name)` tool.**

Skill bodies are NOT pre-loaded into system prompt. The LLM calls
`LoadSkill(name="...")` when it decides a skill is relevant. The tool's
`call()`:

1. Resolves `name` against the discovered skill catalog
2. Reads the markdown file from disk
3. Strips the frontmatter
4. Returns body as `ToolResult.output`

The body lands in `messages[]` as a `tool_result` block — subject to
Phase 4 compaction Layer 1 (head/tail truncate if oversized), subject
to Layer 2 reactive (oldest pair dropped first if prompt-too-long).
Standard tool dispatch path, no special handling.

**L5 — `LoadSkill.is_read_only = True`.**

Reading markdown files is read-only. Permission Tier 3 routes
LoadSkill through the lax path (default ALLOW). Tier 1/2 still apply
— Tier 1 hardcoded sensitive paths can't be loaded as skills (no one
would name a skill file `~/.ssh/id_rsa.md`, but defense in depth).

**L6 — Frontmatter schema.**

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Must match `^[A-Za-z][A-Za-z0-9_-]*$` (safe identifier — used as tool argument value); collision detected at bootstrap (project wins per L2) |
| `description` | yes | One-line; used in catalog injection so LLM can match relevance |
| `version` | no | Free-form string, currently informational; future migration tooling may consume |
| Other fields | tolerated | Forward-compat; ignored on parse |

**L7 — Skill body size: no skill-level limit.**

A skill author can write a 50K-token skill if they want. The harness
doesn't pre-check. At dispatch time:

- Phase 4 Layer 1 (`TruncateToolResultHook`) truncates the tool_result
  to `tool_result_cap` (default 10K tokens) — head/tail with marker
- Layer 2 reactive truncation handles cumulative blowup if multiple
  large skills are pulled in one query

This matches every other tool's behavior (Read of a huge file = same
truncation path). Skills aren't special.

---

## Cross-cutting invariant

**Phase 5c must not add a new dispatch path.** The following files must
remain unchanged in `src/openharness/`:

- `permissions/checker.py` — no `isinstance(LoadSkillTool)` branches
- `hooks/executor.py` — PreToolUse/PostToolUse fires identically for LoadSkill
- `engine/query.py` — `_dispatch_one` calls `BaseTool.call()` agnostically
- `observability/logging.py` — `tool_dispatch` automatically logs LoadSkill calls

Where change IS allowed:

- `tools/load_skill.py` — new `LoadSkillTool(BaseTool)` subclass
- `skills/` — new package: `Skill` dataclass + frontmatter parse + filesystem scan
- `prompts.py` — catalog injection into system_prompt builder
- `cli.py` — bootstrap: scan skills → register LoadSkill → inject catalog
- (optional) `engine/context.py` — possibly add `skill_store` field to QueryContext for testability

If during build any "no change allowed" layer needs editing,
**stop and re-open the boundary doc**.

This invariant is the **third tenant** of Phase 3 abstractions
(after MCP in Phase 5a, Sandbox in Phase 6 preview).

---

## Out of scope (Phase 6+)

- **LLM-gated catalog filtering** (preview-mode LLM selection of which
  skills to surface). Catalog size doesn't justify the complexity at
  Phase 5c scale.
- **Keyword / regex auto-injection of skill bodies.** Violates LLM-as-
  RPC framing — framework shouldn't decide skill relevance for the LLM.
- **Skill versioning / dependency resolution.** Skills are markdown
  documents, not packages.
- **Skill creation/edit tools** (e.g., `CreateSkill(...)`). Users edit
  with `Write` tool or directly in editor.
- **Vector embedding / semantic search over skills.** Defer until
  skill count > 50 (then it's RAG-over-skills, a different problem).
- **Skill ↔ ModeBundle integration** (Phase 5b slash command's
  `extra_skills` frontmatter). Cross-feature, defer to Phase 5b boundary.
- **Hot reload of skills mid-session.** Catalog is bootstrap-frozen,
  same as MCP catalog.

---

## Critical decisions (L1-L7)

| ID | Decision | Why |
|---|---|---|
| **L1** | markdown + YAML frontmatter | Industry-standard, Claude Code / test-gen-agent precedent, human-readable |
| **L2** | Global + project layers, project overrides | Mirrors git config; matches MCP / slash-command convention |
| **L3** | Always-on catalog injection | Tiny token cost (< 50 tokens/skill); avoids 2× LLM call complexity of LLM-gated |
| **L4** | Lazy body via LoadSkill tool | Pure expression of LLM-as-RPC + tool dispatch; no new dispatch path |
| **L5** | `is_read_only=True` | Reading markdown is read-only; Tier 3 lax path auto-applies |
| **L6** | Minimal frontmatter (name, description required) | Forward-compat; over-specifying schema kills extensibility |
| **L7** | No skill-level size limit | Phase 4 compaction handles oversized tool_result generically |

---

## Dependency direction

```
skills/                           (new package)
   ├── model.py                   ← Skill dataclass + frontmatter parse
   └── store.py                   ← FilesystemSkillStore (discovery + lookup)

tools/load_skill.py               ← LoadSkillTool(BaseTool); deps: skills/, tools/base
prompts.py                        ← +catalog injection into system_prompt builder
cli.py                            ← bootstrap: scan → register → inject

permissions/checker.py            ← ZERO CHANGE (invariant)
hooks/executor.py                 ← ZERO CHANGE (invariant)
engine/query.py                   ← ZERO CHANGE (invariant)
observability/logging.py          ← ZERO CHANGE (invariant)
```

`skills/` is downstream of nothing (pure data + parsing); upstream of
`tools/load_skill.py` (consumed by the BaseTool implementation) and
`prompts.py` (consumed by catalog injection).

---

## Acceptance for Phase 5c close-out

- [ ] Sample skill file `react-testing.md` in `<project>/.openharness/skills/`
  is discovered at CLI bootstrap and appears in the system prompt's
  "Available Skills" section
- [ ] `oh ask` invocation that triggers a skill-load: LLM emits
  `LoadSkill(name="react-testing")` → tool dispatches → markdown body
  returned as `tool_result` → LLM uses it in next turn
- [ ] `tool_dispatch` log captures LoadSkill invocations with no
  observability-layer code change
- [ ] PreToolUse + PostToolUse hooks fire on LoadSkill with no
  hook-layer code change
- [ ] PermissionChecker treats LoadSkill as read-only via Tier 3
  (no `isinstance` branch on LoadSkill anywhere in `permissions/`)
- [ ] Project-level skill with same `name` overrides global; verified
  by a fixture-level test
- [ ] Invalid frontmatter (missing required field, unparseable YAML) →
  warning log at bootstrap, skill skipped, other skills still load
- [ ] `permissions/checker.py`, `hooks/executor.py`, `engine/query.py`,
  `observability/logging.py` — show **zero diff** vs Phase 5a close
  (the invariant)
- [ ] mypy strict + ruff clean + coverage ≥ 95 % retained

---

## Pointers

- Preview (deep framing source): [`tasks/phase-5c-skills-preview.md`](../tasks/phase-5c-skills-preview.md)
- Phase 5a MCP boundary (cross-cutting invariant template): [`decisions/11-phase-5-boundary.md`](./11-phase-5-boundary.md)
- Phase 1+2 retro (LLM-as-RPC-client framing): [`learnings/phase-1-and-2.md`](../learnings/phase-1-and-2.md) §6
- Phase 4 compaction (handles oversized tool_result generically): [`decisions/10-phase-4-boundary.md`](./10-phase-4-boundary.md)
- Phase 3 hook contract Skills reuse: [`decisions/08-phase-3-boundary.md`](./08-phase-3-boundary.md) D13.1
