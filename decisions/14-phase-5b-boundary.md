# Phase 5b Boundary — Slash Commands (User-Facing Template Shortcuts)

> Status: locked at Phase 5b entry, 2026-05-16.
>
> Scope note: **this boundary covers Slash Commands only**. ModeBundle
> (catalog filter + permission override + hook injection per slash
> command) is **explicitly deferred to Phase 5d** — see Out of Scope
> below. Phase 5a MCP boundary: `decisions/11`; Phase 5c Skills:
> `decisions/12`; Phase 6 Sandbox: `decisions/13`.
>
> Rationale + framing:see `tasks/phase-5-preview.md` D15 +
> Three-Axis on D15.1 + D15.5 captured in chat 2026-05-16.

## Triggering observation

Phase 5a (MCP) and Phase 5c (Skills) both validated the same
cross-cutting invariant: extensions to the harness add ZERO new
dispatch paths — they land as tenants of existing infrastructure
(BaseTool / tool_registry / system prompt / messages).

Slash commands are the **first user-facing UX extension** — different
from MCP / Skills which were LLM-facing. The triggering question:
**can user-facing UX extensions land with the same zero-invariant-
impact discipline?**

Yes, and even more cleanly: slash commands resolve **before** the
LLM ever sees the prompt. They never touch the engine, permission,
hook, or observability layers. They're purely a **CLI input
transformation**: `oh ask "/review last commit"` → `cli.py` looks up
`review.md`, substitutes `{args}`, and the resulting string becomes
the normal user message. From `run_query`'s perspective, nothing
about slash commands exists.

This makes Phase 5b the **smallest tenant test yet** — the test of
whether the harness can accept extensions that **don't even reach the
LLM-facing infrastructure**.

---

## In scope

**C1 — Complexity: pure prompt template only.**

Phase 5b implements the simplest slash command shape:a markdown file
with YAML frontmatter is loaded, args substituted, body becomes the
user message. **No system prompt override, no catalog filtering, no
hook injection, no permission override**. ModeBundle's full power
(per `tasks/phase-5-preview.md` D15.1 option C) is deferred to Phase
5d — slash command MVP is intentionally narrow.

```markdown
---
name: review
description: Review pending changes
---
Please review the following changes for correctness, readability, and
security:

{args}

Focus on edge cases and security implications.
```

Invocation:`oh ask "/review last 3 commits"` → resolves to the body
above with `{args}` → `last 3 commits`. The resolved string is what
the LLM sees as the user message.

**C2 — Storage: two layers, project overrides global.**

| Layer | Path | When used |
|---|---|---|
| Global | `~/.openharness/commands/<name>.md` | User-wide UX shortcuts |
| Project | `<cwd>/.openharness/commands/<name>.md` | Project-local conventions |

Project entry with same `name` wins (same mechanic as Skills L2,
Phase 5c). Identical to git config layering.

**C3 — Args: `{args}` placeholder substitution + tail append fallback.**

The body MAY contain a `{args}` placeholder. The CLI substitutes:

- If body contains `{args}`:replaced with the args string verbatim.
- If body does NOT contain `{args}`:args appended on a new line at
  the end of body (so users never accidentally lose their args).
- If user invokes `/cmd` with no args:`{args}` substituted with the
  empty string (or no append in the fallback path).

Python `str.format(args=...)` underneath:simple, learnable, no
template engine dependency.

**C4 — Lifecycle: all one-shot.**

Every slash command invocation is one-shot — it expands to a user
message and the agent loop runs as usual. **No persistent "mode"
across invocations**. Reasoning:

- `oh ask` is one-shot by nature; persisting state across invocations
  needs the Phase 7 `oh chat` mode.
- Users wanting "stay in plan mode" can re-issue `/plan ...` on each
  query.
- frontmatter MUST NOT carry a `mode:` field (forward-compat: if 5d
  adds it, parsers gracefully ignore — but 5b emits no semantic for
  it).

**C5 — Hook integration: NONE.** ⭐

Slash commands in Phase 5b DO NOT inject hooks, override permissions,
or filter the tool catalog. The body becomes a user message and that's
it. The user-facing distinction is preserved:

- **Skills** (Phase 5c) = LLM-facing knowledge — catalog in system
  prompt, body via tool call. LLM consumes.
- **Slash commands** (Phase 5b) = User-facing UX shortcut — resolved
  pre-LLM, never appears in catalog. User consumes.

If a user wants `/security-review` to actually constrain tool
behavior, they must use orthogonal primitives:`OPENHARNESS_DENY_PATHS`,
`--dry-run`, or programmatically registered hooks. The slash command
itself is **just templating**.

ModeBundle (per-command catalog filter + permission override + hook
injection) is the genuine "mode-as-trigger" feature and is **Phase
5d's deliverable**. Building it now would couple too many concepts
(hook-by-name registry + per-call hook stack + permission overlay) and
inflate Phase 5b past its zero-invariant-impact discipline.

---

## Cross-cutting invariant

Phase 5b is the **fourth test** of the Phase 3 cross-cutting invariant
(after MCP 5a, Skills 5c, and the upcoming Sandbox 6). Stronger
prediction than previous: slash commands shouldn't touch any
LLM-facing layer at all.

The following files must remain unchanged:

- `permissions/checker.py` + `permissions/tier_based.py`
- `hooks/executor.py` + `hooks/registry.py`
- `engine/query.py` (dispatch loop)
- `engine/context.py` (NO new field — commands don't reach `run_query`)
- `observability/logging.py`
- `prompts.py` (catalog is for LLM-facing extensions; commands aren't
  one)
- `tools/` (no new BaseTool subclass — commands aren't LLM-callable)

Where change IS allowed:

- `commands/` — new package:`Command` dataclass + `parse_command` +
  `FilesystemCommandStore` (mirrors `skills/` structure verbatim;
  ~100 LoC) + `expand_command(prompt, store) -> str` (~30 LoC)
- `cli.py` — bootstrap step:instantiate command store; before
  building user message, call `expand_command(prompt, store)`;
  optional `--no-commands` flag

Estimated total: **~150 LoC production**. Same order of magnitude as
Phase 5c Skills.

---

## Out of scope (Phase 5d+)

- **ModeBundle / full mode-as-trigger.** Per-command system prompt
  override, catalog filtering, hook injection, permission overlay.
  Phase 5d will design this top-down; building piecemeal in 5b would
  inflate scope without the unified abstraction.
- **Stateful command sessions.** `/plan` persisting "plan mode" across
  invocations. Phase 7 `oh chat` is the right venue.
- **frontmatter `mode:` field semantics.** Parser tolerates the field
  (forward-compat), but emits no behavior for it.
- **Argument schema parsing.** No frontmatter `args_schema:` — single
  `{args}` string covers 95% of real use cases. Schema parsing defers
  to ModeBundle work if ever needed.
- **Catalog injection into system prompt.** Commands are user-facing,
  LLM doesn't need to know they exist. Pure templating ≡ commands
  vanish before the LLM.
- **Built-in commands shipped with the harness.** Phase 5b is purely
  user-authored. We may ship example commands as docs in Phase 7
  polish, but they're not part of the framework.
- **Interactive command discovery / autocomplete.** No `oh
  commands list` subcommand in 5b — read your filesystem.

---

## Critical decisions (C1-C5)

| ID | Decision | Why |
|---|---|---|
| **C1** | Pure prompt template only (no ModeBundle) | Smallest tenant test; ModeBundle is Phase 5d's coupled-concept job |
| **C2** | Global + project storage, project overrides | Identical to Skills L2, identical to git config — leverages Phase 5c precedent |
| **C3** | `{args}` placeholder substitution + tail append fallback | Python `str.format`-shaped; users never lose args |
| **C4** | All invocations one-shot | `oh ask` is one-shot; persistent mode needs Phase 7 `oh chat` |
| **C5** | NO hook integration; pure templating | Preserves Skills/Commands role split (LLM-facing vs user-facing) |

---

## Dependency direction

```
commands/                         (new package, mirrors skills/)
   ├── model.py                   ← Command dataclass + parse_command
   ├── store.py                   ← CommandStore Protocol + FilesystemCommandStore + EmptyCommandStore
   └── expand.py                  ← expand_command(prompt, store) -> str

cli.py                            ← bootstrap: scan + expand before run_query
   (+ optional --no-commands flag; mirrors --no-skills)

permissions/                      ← ZERO CHANGE (invariant)
hooks/                            ← ZERO CHANGE (invariant)
engine/query.py + engine/context.py ← ZERO CHANGE (invariant — commands never reach engine)
observability/logging.py          ← ZERO CHANGE (invariant)
prompts.py                        ← ZERO CHANGE (no catalog for user-facing extensions)
tools/                            ← ZERO CHANGE (no BaseTool subclass — commands aren't LLM-callable)
```

`commands/` is **structurally parallel** to `skills/` (Phase 5c):both
parse the same markdown+frontmatter shape, both have a two-layer
storage Protocol+Sentinel default. The only difference:`commands/`
exports a `expand_command(prompt, store) -> str` function instead of
binding into a `BaseTool` — because slash commands are pre-LLM, not
LLM-callable.

---

## Acceptance for Phase 5b close-out

- [ ] Sample command `review.md` in `<project>/.openharness/commands/`
  is discovered at CLI bootstrap
- [ ] `oh ask "/review last commit"` resolves to the template body
  with `{args}` substituted; LLM sees the substituted string as
  user message
- [ ] `oh ask "/review"` (no args) substitutes `{args}` with empty
  string;invocation works
- [ ] Body without `{args}` placeholder + user supplied args → args
  appended on new line (never lost)
- [ ] `oh ask "/nonexistent something"` produces a clear error UX
  (lists available commands; exit code 1)
- [ ] Project-level command with same `name` overrides global;
  verified by fixture test
- [ ] Invalid frontmatter → warning log at bootstrap, command
  skipped, others still loadable (same never-raise discipline as
  Skills)
- [ ] `oh ask "regular prompt without leading slash"` → unchanged
  behavior (no command resolution attempted)
- [ ] `--no-commands` flag short-circuits command scanning + leaves
  slash prefix in the prompt verbatim
- [ ] `permissions/checker.py`, `permissions/tier_based.py`,
  `hooks/executor.py`, `hooks/registry.py`, `engine/query.py`,
  `engine/context.py`, `observability/logging.py`, `prompts.py`,
  `tools/__init__.py` — **structurally verified zero diff** (test
  reads source + greps for `Command` identifier)
- [ ] mypy strict + ruff clean + coverage ≥ 95 % retained

---

## Pointers

- Phase 5b preview source (D15 section): [`tasks/phase-5-preview.md`](../tasks/phase-5-preview.md)
- Phase 5a MCP boundary (cross-cutting invariant template): [`decisions/11-phase-5-boundary.md`](./11-phase-5-boundary.md)
- Phase 5c Skills boundary (parallel structure): [`decisions/12-phase-5c-skills-boundary.md`](./12-phase-5c-skills-boundary.md)
- Phase 5c retro (why pure templating beats hook integration in MVP): [`learnings/phase-5c-skills.md`](../learnings/phase-5c-skills.md) §3.2 + §6
- LLM-as-RPC framing (commands are pre-RPC, never reach the LLM-as-RPC client): [`learnings/phase-1-and-2.md`](../learnings/phase-1-and-2.md) §6
