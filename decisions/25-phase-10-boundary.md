# Phase 10 Boundary — Memory Subsystem (Static Read Path)

> Status: locked at Phase 10 entry, 2026-05-26.
>
> Scope note: this phase introduces the **read-side** of the memory
> subsystem. It establishes the storage layout, the on-disk schema,
> the relevance-ranking primitive, and the prompt-injection points
> for **two long-term context layers**: project-level instructions
> (CLAUDE.md cascade) and per-project durable memory (MEMORY.md
> index + relevance-scored per-condition .md files). **No agent
> writes in this phase**: memories are created either by manual
> filesystem edits or — in Phase 11 — by the durable-extraction
> secondary LLM pass.
>
> The write side, the summarization substrate, session persistence,
> and auto-dream consolidation are all explicitly deferred (see
> "Out of scope" below). Splitting reads from writes lets Phase 10
> validate three things in isolation: (1) `markdown_store/` as a
> 6th-consumer substrate, (2) the prompt-injection extension point
> reserved by `prompts.py` since P2-T5 D11.5, (3) the relevance
> scoring function as a standalone primitive.
>
> Related work references:
> - **Upstream HKUDS/OpenHarness §11** has the full memory module
>   (`memory/manager.py` + `memory/scan.py` + `memory/relevance.py` +
>   `memory/search.py` + `memory/usage.py` + `services/memory_extract/`).
>   Phase 10 reimplements only the read-side primitives independently;
>   the secondary-pass `extract_memories_from_turn` defers to Phase 11.
> - **Phase 4 retro §3.4, §6** flagged "cross-session memory" as
>   deferred and called out a known limitation: PreApiCall hook does
>   not re-run after reactive compaction, so any memory-via-hook
>   injection would be silently dropped. Phase 10's
>   build-system-prompt injection point bypasses this — memory is
>   in the prompt, not added by a hook. The hook-vs-reactive
>   interaction debt remains, but Phase 10 doesn't trip on it
>   because Phase 10 doesn't use hooks for memory.
> - **ARCHITECTURE.md** Tier 2 ⭐⭐ "记忆系统（基础）— YAML
>   frontmatter + 简单检索" is the slot this phase fills.

## Triggering observation

The system prompt assembled by `build_system_prompt`
(`prompts.py:77-111`) has been a stable contract since P2-T5
(D11.5, D6.5): "Phase 3 personalization and Phase 4 memory both
extend the body by appending more sections, not by changing the
call surface." Phase 3 used this for personalization. Phase 5c
extended it for skills. **Phase 10 is the third independent
extension test of that signature**.

Concurrently, `markdown_store/` has shipped with 3 consumers
(commands/skills/bundles) and Phase 9's plugin loader fans out to
all 5 extension axes — but **the memory store would be its 6th
consumer**. If `markdown_store/` doesn't change when memory adopts
it, the Phase 8 extraction abstraction holds under a fourth
independent test.

Finally, today's harness has a real user-experience gap: every
`oh ask` invocation has zero recall of prior interactions, and
zero awareness of project-specific conventions written in
CLAUDE.md or its variants. The two most-requested capabilities for
a "harness that knows my project" are exactly the two this phase
ships: **CLAUDE.md auto-discovery + per-project durable memory**.

---

## In scope

### D28.1 — Memory storage layout: cwd-hashed sub-dir under user data dir

Memory lives **outside the repo** at:

```
~/.openharness/memory/<basename(cwd)>-<sha1(cwd)[:12]>/
  ├── MEMORY.md           # human-navigable index (~200 lines cap)
  ├── <slug>.md           # one memory per file
  ├── <slug>.md
  └── ...
```

**Why outside the repo (deviation from existing commands/skills/bundles
pattern that uses `<cwd>/.openharness/<x>/`):** memory is private to
the user's interaction with the project and **must not be
git-tracked**. Mixing it into the repo opens two failure modes:
(a) accidental commit of personal context (potentially sensitive),
(b) memory polluting the project's collaboration surface.

**Why cwd-hashed:** ensures (a) `cd /a/foo` and `cd /b/foo` get
distinct memory dirs even though basenames collide, (b) same cwd
always maps to the same dir (deterministic), (c) `<basename>-`
prefix makes `ls ~/.openharness/memory/` human-readable.

Path resolution lives in `memory/paths.py::get_project_memory_dir(cwd)`.

### D28.2 — CLAUDE.md cascade: recursive parents + global fallback

`discover_claude_md_files(cwd)` enumerates, in order:

1. For each directory in `[cwd, *cwd.parents]` until filesystem root:
   - `<dir>/CLAUDE.md`
   - `<dir>/.claude/CLAUDE.md`
   - `<dir>/.claude/rules/*.md` (sorted)
2. `~/.openharness/CLAUDE.md` (global fallback, appended last)

**De-dup**: same absolute path appears at most once (`seen: set[Path]`).
**Termination**: `if directory.parent == directory: break` (handles
root on every platform).

**Why recursive instead of "git root only":** mirrors `.gitignore`
/ `.editorconfig` semantics; does not require a git repository;
covers the common case of running `oh ask` from `src/openharness/api/`
where the project CLAUDE.md is at `~/projects/openharness/CLAUDE.md`.

**Why global fallback last:** project-level instructions are more
specific than user-global ones, so the project section appears
*before* the global section in the assembled prompt — and the LLM's
attention-recency bias means later instructions weigh more, but
the file-path label on each section ("## /Users/.../CLAUDE.md")
lets the LLM resolve conflicts explicitly when it must.

**Injection format**: a single `# Project Instructions` heading
followed by one `## <absolute path>` subsection per file, each body
wrapped in a `\`\`\`md` fenced block. Bodies are truncated at
`max_chars_per_file = 12_000` with a `\n...[truncated]...` marker.

### D28.3 — Memory frontmatter schema (14 fields, single source of truth)

Each memory `.md` file is YAML frontmatter + markdown body:

```yaml
---
id: 01HXXXXXXXX                    # ULID, immutable
schema_version: 1                  # for forward-compat migrations
name: stripe-sdk-version           # safe-identifier regex (NAME_PATTERN)
description: Project uses Stripe SDK 8.x with the legacy API
type: project                      # user | feedback | project | reference
scope: private                     # Phase 10: ONLY private (D28.5)
created_at: 2026-05-26T10:00:00Z
updated_at: 2026-05-26T10:00:00Z
ttl_days: null                     # null = no expiration; int = days from updated_at
disabled: false                    # soft-delete flag
supersedes: []                     # list of id strings; Phase 10 displays, doesn't auto-collapse
tags: ["payment", "stripe"]
importance: 0.5                    # 0.0-1.0, contributes to relevance score
use_count: 0                       # incremented on every relevance-injection
last_used_at: null                 # ISO timestamp of last injection
signature: <sha256(body+type+scope)>  # for content-based dedup at write time (Phase 11)
---

Body content here. Markdown. Free-form. Cap: no hard limit at write
time; per-memory truncation cap at prompt-build time is 8000 chars
(per D28.6).
```

**Deviation from HKUDS Q2: usage tracking integrated INTO frontmatter.**
HKUDS keeps `use_count` + `last_used_at` in a separate
`.usage_index.json` to avoid touching the memory file's mtime on
every read-injection. Phase 10 inlines them into the frontmatter
for **single-source-of-truth simplicity**, accepting two trade-offs:
(a) `updated_at` semantics now mean "content OR usage last updated"
— consumers must read the dedicated `last_used_at` field for usage
recency, (b) every relevance-injection writes the file. The
write-amplification cost is acceptable for Phase 10's expected
memory counts (<100 per project); if a project grows past that, an
optional separate index can be added in a later phase without
breaking the schema.

**Why ULID for `id`:** human-sortable, monotonic, URL-safe;
collision-free without coordination across processes (relevant
when Phase 11's extraction runs concurrently with `oh memory add`
in a hypothetical future).

### D28.4 — Memory store: 6th `markdown_store/` consumer

The Memory dataclass satisfies `MarkdownDocument` Protocol
(`.name: str` + `.source_path: Path`). `parse_memory(path)` follows
the `parse_X` pattern from commands/skills/bundles, reusing
`read_frontmatter_dict()`. `FilesystemMemoryStore` subclasses
`FilesystemMarkdownStore[Memory]` exactly as `FilesystemCommandStore`
does (per D21.3).

**One asymmetry vs the other 3 stores:** memory has no `global_dir`
in Phase 10 — the cwd-hashed `project_dir` is the only layer (because
team scope, which would be the cross-project layer, is deferred per
D28.5). The constructor accepts `global_dir=None` and the existing
`FilesystemMarkdownStore._scan` already handles that (it just
iterates only the project layer).

```python
class FilesystemMemoryStore(FilesystemMarkdownStore[Memory]):
    def __init__(self, *, project_dir: Path) -> None:
        super().__init__(
            global_dir=None,
            project_dir=project_dir,
            parser=parse_memory,
            log_event_prefix="memory",
        )
```

**Test the substrate**: if `markdown_store/` requires any change to
host this 6th consumer, the Phase 8 abstraction failed a fourth
independent test — stop and reopen the boundary doc.

### D28.5 — Single scope only: `private`

Phase 10 ships only `scope: private`. No team scope, no
sub-directory split, no secret scanning, no
`check_team_memory_secrets`.

**Rationale:** team scope is a privacy + collaboration concern with
its own design surface (where do team memories sync? Git? A
separate vault? How is membership defined?). Bundling that
question with the read-path layer would conflate decisions.
Phase 11 — when the extraction secondary-pass lands — is the
natural moment to revisit, since extraction is where secrets risk
appearing.

Frontmatter still carries the `scope` field (so we don't need a
migration when team scope arrives), but `parse_memory` rejects any
value other than `private` with a warning + skip.

### D28.6 — Three-section prompt injection (additive to `build_system_prompt`)

`build_system_prompt` gains two new keyword arguments (per the
D11.5 / D6.5 extension contract — additive, never renames the
function):

```python
def build_system_prompt(
    tools: list[ToolSpec],
    env: EnvironmentInfo,
    *,
    skill_store: SkillStore | None = None,
    claude_md_content: str | None = None,        # NEW
    memory_manifest: MemoryManifest | None = None,  # NEW
) -> str:
```

When both new kwargs are `None`, the output is **byte-identical**
to today's prompt — existing tests pass unchanged.

Section order in the assembled prompt:

```
[base instructions]
## Tools
## Available Skills            (Phase 5c, when skill_store present)
## Environment
## Project Instructions        ← NEW: CLAUDE.md content (when present)
## Memory                      ← NEW: MEMORY.md index entry (when memory_manifest present)
## Relevant Memories           ← NEW: top-N relevance-scored bodies (when any survive scoring)
```

**Why CLAUDE.md before Memory:** CLAUDE.md is more stable (human-
written project conventions); Memory is more contextual (agent-
curated, query-dependent). Mirroring the natural "由稳到变" gradient
puts contextual content closer to the user message (where attention
peaks).

**Why MEMORY.md index AND Relevant Memories as separate sections:**
the index gives the LLM a "table of contents" of what memory
exists (so it knows what could be asked about); Relevant Memories
gives bodies of the currently-scored top-N. Without both, you must
choose between "show everything" (token-expensive) and "show
selection" (LLM doesn't know what else exists). HKUDS confirmed
this split in upstream §11.

### D28.7 — Relevance scoring: query-token-required, weighted hits

`memory/relevance.py::select_relevant_memories(query, memories, *, max_results)`:

1. Tokenize `query` (regex `\w+` lowercased; supports ASCII + Han).
2. For each memory header (name + description + tags + body
   preview):
   - `meta_hits` = count of query tokens appearing in
     `{name, description, tags}`.
   - `body_hits` = count of query tokens appearing in body.
3. **Exclude** any memory with `meta_hits == 0 and body_hits == 0`.
   This is the load-bearing rule — without it, every turn injects
   N most-recent memories regardless of relevance, defeating
   the point.
4. Score:
   ```
   score = meta_hits * 2.0
         + body_hits * 1.0
         + importance * 0.4
         + min(use_count, 5) * 0.1
         + recency_boost
   ```
   `recency_boost = 0.3 if updated_at ≤ 14d else 0.1 if ≤ 30d else 0`.
   `use_count` capped at 5 prevents popularity feedback loops.
5. Sort `(-score, -modified_at)`; return top `max_results`.

Defaults: `max_results = 5`, body truncation per memory at 8000
chars (matches HKUDS).

### D28.8 — Usage tracking: atomic frontmatter rewrite at injection time

After `select_relevant_memories` returns the top-N, **before** the
system prompt is finalized, `mark_memory_used(memory)` rewrites
each selected memory's file atomically:

- `use_count += 1`
- `last_used_at = datetime.now(UTC).isoformat()`
- All other fields unchanged

Atomicity via `tempfile + os.replace` (the existing pattern from
`session_storage` design and from `markdown_store/parse.py`'s
expectations).

**Failure handling**: if the rewrite fails (disk full, permission
error, file deleted mid-flight), log a warning under
`memory_usage_update_failed` and continue prompt building. Prompt
assembly **must not** block on usage updates — the user's request
is what matters.

### D28.9 — No agent write surface in Phase 10

Memories are created by exactly two paths, **neither of which lands
in Phase 10**:

- **Manual user editing** (filesystem) — drop a `.md` into the
  storage dir. Already supported by Phase 10's read path.
- **Phase 11 extraction** — `extract_memories_from_turn` secondary
  LLM pass at turn end. Deferred.

**No** `write_memory` tool, **no** `oh memory add` CLI, **no**
agent-callable write path in Phase 10. This is a deliberate
write/read split: Phase 10 validates the read substrate cleanly
before write semantics enter; Phase 11 then layers extraction on
top of a verified read layer.

### D28.10 — Settings extensions (opt-out, not opt-in)

```python
class MemorySettings(BaseModel):
    max_files: int = 5                    # top-N for Relevant Memories
    max_entrypoint_bytes: int = 8_000     # MEMORY.md index byte cap
    max_body_chars: int = 8_000           # per-memory body truncation in prompt
    max_claude_md_chars: int = 12_000     # per-CLAUDE.md-file truncation

class Settings(BaseSettings):
    ...
    enable_memory: bool = True            # opt-OUT, not opt-in
    memory: MemorySettings = Field(default_factory=MemorySettings)
```

**Why opt-out (deviation from plugins' opt-in pattern):** memory
is read-only and side-effect-free in Phase 10 (the only side effect
is `use_count++` in a private user-dir file). No code execution
risk. Disabling memory is for users who explicitly want a
stateless harness — the default should be "the harness knows your
project."

Env vars: `OPENHARNESS_ENABLE_MEMORY=false` to disable;
`OPENHARNESS_MEMORY_MAX_FILES=10` to override top-N.

### D28.11 — CLI surface: read-only inspection only

```
oh memory list                           # Tabular: name | type | score-ready-fields
oh memory list --format json
oh memory show <name-or-id>              # Full file content
oh memory path                           # Print storage dir path
oh memory path --reveal                  # macOS: open in Finder; Linux: print only
```

**Out of scope for CLI in Phase 10**: `add`, `edit`, `remove`,
`disable` — all require the write surface from D28.9 which is
deferred.

`list` accepts no filters in Phase 10 (no `--type project`, no
`--tag payment`); add if Phase 11 surfaces real demand.

---

## Cross-cutting invariant

**Phase 10 memory subsystem must not add any dispatch path or
runtime hook.** The following layers stay **zero diff** in
`src/openharness/`:

- `engine/query.py` — no memory-aware branching in the run loop
- `compaction/` — no integration; Phase 11's session_memory file
  is what compaction will consume, not the durable memory store
- `hooks/` — no new hook events; memory injection happens in
  prompt assembly, not via PreApiCall (deliberately, per Phase 4
  retro debt note)
- `permissions/` — memory dir is user-private, no permission
  checks needed
- `mcp/` — zero change
- `plugins/` — zero change (Phase 9 plugin layer doesn't see
  memory)
- `skills/` — zero change
- `commands/` — zero change
- `bundles/` — zero change
- `protocols/` — no new event types
- `markdown_store/` — **the critical invariant**: if this needs
  any change to host the Memory consumer, Phase 8's abstraction
  failed under its 4th test

Where change IS allowed (all additive):

- `src/openharness/memory/` (new package) — Memory dataclass,
  parse_memory, FilesystemMemoryStore, paths, relevance, usage
- `src/openharness/prompts/` (refactor `prompts.py` → package, OR
  add module) — `claudemd.py` (discover + load), `memory_inject.py`
  (format sections); `build_system_prompt` gains 2 kwargs per D28.6
- `src/openharness/config/settings.py` — +`enable_memory`,
  +`MemorySettings` nested model
- `src/openharness/cli.py` — bootstrap step: assemble memory store
  + load CLAUDE.md + inject into `build_system_prompt`; +`oh memory`
  subcommand series

If during build any "zero diff" layer needs editing, **stop and
re-open the boundary doc**.

---

## Out of scope (Phase 11+)

- **`extract_memories_from_turn`** — secondary LLM pass that
  proposes memories from a completed turn. Phase 11.
- **`write_memory` tool / `oh memory add` CLI** — direct write
  surface. Phase 11 (driven by extraction needs).
- **Compact L1-L4 escalation** — microcompact / context collapse /
  session_memory reuse / full compact. Phase 11.
- **`session_memory` file** (per-turn 5-slot checkpoint). Phase 11.
- **Session snapshot + `oh ask --resume`** — full message-history
  persistence + resume UX. Phase 12 (UI-layer concern, not engine).
- **Auto-dream subprocess + stale-memory GC + consolidation**.
  Phase 13.
- **Team scope + secret scanning** — deferred until extraction
  exists (Phase 11) and team-sync mechanism is defined (deferred
  indefinitely).
- **TTL sweep** — `ttl_days` is stored in frontmatter but Phase 10
  does NOT enforce expiration. A memory past TTL is still returned
  by relevance. Enforcement is a Phase 13 garbage-collector
  responsibility.
- **`supersedes` resolution** — `supersedes: [id1, id2]` is stored
  and displayed in `oh memory show`, but Phase 10 does NOT
  auto-suppress superseded memories from relevance ranking. Phase
  11 may add this when extraction starts producing supersession
  chains.
- **MCP-server-as-memory-backend** — Some agents (Codex, Cursor)
  treat memory as an MCP resource. OpenHarness sticks with
  filesystem in Phase 10 for inspectability + zero dependencies.
- **Cross-session memory injection via PreApiCall hook** — Phase 4
  retro called out this as a known debt. Phase 10 sidesteps it by
  putting memory in `build_system_prompt` not in a hook. The
  reactive-truncation interaction debt is unrelated and remains
  open for Phase 11's compaction work.

---

## Critical decisions (D28.x)

| ID | Decision | Why |
|---|---|---|
| **D28.1** | Memory at `~/.openharness/memory/<basename>-<sha1(cwd)[:12]>/`, outside repo | Private + non-git-tracked + cwd-collision-free + same-cwd-deterministic |
| **D28.2** | CLAUDE.md cascade: recursive parents + `~/.openharness/CLAUDE.md` global fallback; `.claude/CLAUDE.md` and `.claude/rules/*.md` also checked | Mirrors `.gitignore` / `.editorconfig` semantics; handles "run from `src/` subdir" common case |
| **D28.3** | 14-field frontmatter schema with `use_count` + `last_used_at` **inlined** (no separate `.usage_index.json`) | Single source of truth; accepts write-amplification cost for <100-memory projects |
| **D28.4** | `FilesystemMemoryStore` subclasses `FilesystemMarkdownStore` (6th consumer); `global_dir=None` because no cross-project layer in Phase 10 | Validates Phase 8 abstraction under 4th independent test |
| **D28.5** | Single scope only: `private`. No team, no secret scanning. | Team scope is a privacy + collaboration design surface that belongs with Phase 11 extraction |
| **D28.6** | `build_system_prompt` gains 2 additive kwargs; 3 new sections (Project Instructions / Memory / Relevant Memories) in that order | Preserves D11.5 / D6.5 extension contract; byte-identical output when both kwargs None |
| **D28.7** | Relevance: weighted hits with **zero-token-hits exclusion**; defaults: `max_results=5`, body truncation 8000 chars | Without zero-hits exclusion, relevance degrades to "most-recent N", defeating the point |
| **D28.8** | `mark_memory_used` rewrites frontmatter atomically at injection time; failure is logged, not propagated | Usage tracking enables Phase 13's stale-memory GC; failures must not block the user's request |
| **D28.9** | No agent write path in Phase 10 (no tool, no CLI add) | Validates read substrate cleanly before write semantics enter (Phase 11) |
| **D28.10** | `enable_memory: bool = True` (opt-OUT); `MemorySettings` nested for tuning caps | Memory is read-only + side-effect-free in Phase 10; default should be "harness knows your project" |
| **D28.11** | CLI: `oh memory list / show / path` only (read-only inspection) | Matches the no-write invariant from D28.9 |

---

## Dependency direction

```
memory/                              (new package)
   ├── model.py                      ← Memory dataclass + parse_memory
   ├── store.py                      ← FilesystemMemoryStore
   ├── paths.py                      ← get_project_memory_dir(cwd) -> Path
   ├── relevance.py                  ← select_relevant_memories + scoring
   └── usage.py                      ← mark_memory_used (atomic frontmatter rewrite)

prompts.py  →  prompts/              (refactor module to package)
   ├── __init__.py                   ← re-exports for back-compat
   ├── system.py                     ← existing build_system_prompt (extended kwargs)
   ├── claudemd.py                   ← discover_claude_md_files, load_claude_md_prompt
   └── memory_inject.py              ← format_memory_section, format_relevant_memories

config/settings.py                   ← +enable_memory + MemorySettings nested
cli.py                               ← bootstrap memory store + CLAUDE.md
                                       +oh memory list/show/path subcommands
markdown_store/                      ← UNCHANGED (6th consumer test)

engine/                              ← ZERO DIFF (invariant)
compaction/                          ← ZERO DIFF
hooks/                               ← ZERO DIFF
permissions/                         ← ZERO DIFF
mcp/                                 ← ZERO DIFF
plugins/                             ← ZERO DIFF
skills/                              ← ZERO DIFF
commands/                            ← ZERO DIFF
bundles/                             ← ZERO DIFF
protocols/                           ← ZERO DIFF
```

`memory/` is a leaf package — depends only on `markdown_store/`,
`observability/`, and stdlib. `prompts/` (after refactor) consumes
`memory/` types via `Protocol`-style interfaces so the dependency
direction is one-way (no circular).

---

## Sub-decisions deferred to build

Three open questions resolved tentatively now, locked at build time:

- **Tokenization for relevance scoring** — pure `re.findall(r"\w+",
  text.lower())` covers ASCII + Han characters via Python's
  Unicode-aware `\w`. Stopword list? Tentative: **no stopwords in
  v1** — for technical queries ("Stripe SDK 8.x"), stopwords are
  rare; for natural-language queries the scoring is robust to a
  few noise tokens. Revisit if false-positive rate is high in
  practice.
- **Memory file naming (slug strategy)** — when extraction writes
  a memory in Phase 11 it'll need a deterministic file name. Phase
  10 only reads, but the naming convention should be locked now to
  avoid Phase 11 churn. Tentative: `<slug>.md` where `slug` is
  `name` field sanitized to NAME_PATTERN; collisions append
  `-<id-suffix>`. The `id` field in frontmatter is the canonical
  identifier; the filename is for human navigation.
- **`MEMORY.md` index regeneration** — Phase 10 is read-only, so
  the index is whatever the user (or Phase 11 extraction)
  maintains. Tentative behavior: **relevance scans the full
  directory, not just files listed in `MEMORY.md`**. The index is
  a human convenience, not the canonical memory list. If a user
  hand-writes `foo.md` but forgets to update `MEMORY.md`, the
  relevance scorer still surfaces `foo.md`. Phase 11 extraction
  will auto-append entries to `MEMORY.md` for navigability.

---

## Acceptance for Phase 10 close-out (template)

### CLAUDE.md cascade

- [ ] Running `oh ask` from `<project>/src/openharness/api/`
  injects `<project>/CLAUDE.md` content into the system prompt as
  a `## /path/to/CLAUDE.md` subsection under `# Project Instructions`
- [ ] `~/.openharness/CLAUDE.md` (when present) is injected as the
  last `## ...` subsection (global fallback after project-level)
- [ ] `.claude/CLAUDE.md` and `.claude/rules/*.md` at any cascade
  level are discovered and included
- [ ] No `CLAUDE.md` anywhere → no `# Project Instructions` section
  appears (silent skip, not empty section)
- [ ] CLAUDE.md > 12_000 chars → truncated with
  `\n...[truncated]...` marker, all other files still included
- [ ] De-dup: a symlinked CLAUDE.md visible via two paths appears
  exactly once

### Memory storage + read

- [ ] First `oh ask` in a fresh project creates
  `~/.openharness/memory/<basename>-<hash>/` lazily (only when first
  memory is written; reads from a non-existent dir return empty)
- [ ] Same `cwd` across two `oh ask` invocations → same storage dir
- [ ] Two different `cwd` with the same `basename(cwd)` → distinct
  storage dirs (sha1 suffix differs)
- [ ] Hand-creating `<storage-dir>/foo.md` with valid frontmatter
  → `oh memory list` surfaces it; `oh memory show foo` prints body
- [ ] Memory file with invalid frontmatter → `oh memory list` shows
  it as `(invalid)` with warning log; not crashed

### Relevance scoring + injection

- [ ] Memory with `name: stripe-sdk-version`,
  `description: "Stripe SDK 8.x"`, body mentions Stripe → query
  "How do I refund with Stripe?" injects it as a `## Relevant
  Memories` subsection
- [ ] Same memory + query "What time is it?" (zero token overlap)
  → NOT injected (zero-hits exclusion holds)
- [ ] Top-N cap respected (default 5; tuned via
  `OPENHARNESS_MEMORY_MAX_FILES=3` → max 3 in prompt)
- [ ] Two memories with same meta_hits → higher `importance` wins;
  with same importance → higher `use_count` wins (capped at 5);
  with same `use_count` → more recent `updated_at` wins

### Usage tracking

- [ ] Memory selected by relevance → frontmatter `use_count`
  increments by 1 atomically (intermediate file states never
  visible to a concurrent read)
- [ ] `last_used_at` updates to current UTC ISO timestamp
- [ ] Disk full / permission error during rewrite → warning logged
  under `memory_usage_update_failed`, prompt still assembles
- [ ] `oh memory list` displays `use_count` and `last_used_at`
  reflecting the latest state

### CLI

- [ ] `oh memory path` prints the resolved storage dir for `cwd`
- [ ] `oh memory list` shows tabular: name | type | use_count |
  last_used_at | description (truncated)
- [ ] `oh memory list --format json` emits parseable JSON array
- [ ] `oh memory show <name>` accepts `name` or `id` lookup;
  prints frontmatter + body
- [ ] `oh memory show <nonexistent>` exits non-zero with a clear
  error pointing at the storage dir

### Opt-out + settings

- [ ] `OPENHARNESS_ENABLE_MEMORY=false oh ask "..."` produces a
  system prompt with NO `## Project Instructions`, NO `## Memory`,
  NO `## Relevant Memories` sections (byte-identical to today's
  prompt assembly)
- [ ] `OPENHARNESS_MEMORY_MAX_FILES=10` raises the top-N cap

### Substrate invariants

- [ ] `git diff <P9 close> -- src/openharness/markdown_store/`
  shows **zero diff** — Phase 8 abstraction holds under 4th test
- [ ] `git diff <P9 close> -- src/openharness/{engine,compaction,hooks,permissions,mcp,plugins,skills,commands,bundles,protocols}/`
  shows **zero diff** — the cross-cutting invariant
- [ ] `git diff <P9 close> -- src/openharness/prompts*` shows only
  additive changes (refactor module → package + 2 new kwargs +
  new helper modules); existing `build_system_prompt(tools, env)`
  calls (without new kwargs) produce byte-identical output
- [ ] `git diff <P9 close> -- src/openharness/cli.py` shows only:
  - memory store assembly + CLAUDE.md loading (additive bootstrap)
  - `oh memory` subcommand series (additive Typer app)
- [ ] All ≥233 existing caller tests pass without modification

### Quality gates

- [ ] mypy strict + ruff check + ruff format clean
- [ ] Coverage ≥ 95% retained (gate)
- [ ] CI green on Python 3.10 + 3.11
- [ ] New tests under `tests/memory/` covering: paths, parse,
  store, relevance scoring, usage tracking, prompt-injection
  formatting, opt-out behavior
- [ ] New tests under `tests/prompts/` covering CLAUDE.md
  discovery + injection (replacing direct edits to existing
  `tests/test_prompts.py` if it exists)

---

## Pointers

- **HKUDS upstream §11 memory module** (independent reimplementation,
  no code copy):
  [`REFERENCE.md`](../REFERENCE.md) §11
- **HKUDS upstream files referenced by the design** (read for
  validation, not copied):
  - `src/openharness/prompts/claudemd.py` — D28.2 cascade algorithm
    is functionally equivalent
  - `src/openharness/memory/paths.py` — D28.1 path hashing
  - `src/openharness/memory/relevance.py` + `memory/search.py` —
    D28.7 scoring formula is intentionally similar
- **Phase 4 retro (cross-session memory deferred, PreApiCall debt
  noted):** [`learnings/phase-4.md`](../learnings/phase-4.md) §3.4,
  §6 (lines 157, 200)
- **Phase 8 boundary (`markdown_store/` extraction — Phase 10 is
  4th independent test):** [`decisions/19-phase-8-boundary.md`](./19-phase-8-boundary.md)
- **P2-T5 boundary (D11.5 `build_system_prompt` extension contract
  Phase 10 uses):** [`decisions/06-phase-2-boundary.md`](./06-phase-2-boundary.md) D11.5
- **Phase 5c skill boundary (most recent prior consumer of the
  `build_system_prompt` extension point):**
  [`decisions/12-phase-5c-skills-boundary.md`](./12-phase-5c-skills-boundary.md)
- **ARCHITECTURE.md Tier 2 memory slot:**
  [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2 Tier 2
- **Meta-retro §3.1 — abstraction-first compounding evidence (Phase
  10 is another 7c-shape compounding test):**
  [`learnings/phase-7.md`](../learnings/phase-7.md) §3.1
