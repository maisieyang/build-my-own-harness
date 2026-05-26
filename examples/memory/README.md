# Memory examples

How to hand-write a memory file for Phase 10. **Phase 10 is read-only**
— the agent doesn't write new memories yet (deferred to Phase 11's
`extract_memories_from_turn` secondary LLM pass). Until then, you drop
markdown files into the project's memory dir and the harness reads them.

## Where memories live

Memory storage is **cwd-hashed**, outside the repo:

```
~/.openharness/memory/<basename>-<sha1(resolved-cwd)[:12]>/
```

To find this path for your current project:

```bash
oh memory path
# /Users/you/.openharness/memory/myproject-a1b2c3d4e5f6
```

The dir is created lazily on first write. You can also `mkdir -p` it
yourself, then drop `.md` files inside.

**Why outside the repo (D28.1)**: memory is private to your interaction
with the project. Putting it in the repo opens two failure modes —
accidental commit of personal context, and memory polluting the
project's collaboration surface.

**Why cwd-hashed**: ensures (a) `cd /a/foo` and `cd /b/foo` get distinct
memory dirs even though basenames collide, (b) same cwd always maps
to the same dir.

## Frontmatter schema (14 fields)

```markdown
---
id: 01HXXXXXXXX                    # ULID or any non-empty unique string
name: stripe-sdk-version           # safe identifier (alphanumeric + _-)
description: Stripe SDK 8.x with the legacy API
type: project                      # user | feedback | project | reference
scope: private                     # Phase 10 ONLY supports private
created_at: 2026-05-26T10:00:00+00:00
updated_at: 2026-05-26T10:00:00+00:00
signature: <sha256-hex>            # OPTIONAL — auto-computed if absent
importance: 0.5                    # 0.0-1.0, contributes to relevance score
tags: ["payment", "stripe"]        # also count as meta hits for relevance
ttl_days: null                     # null = no expiration; int = days
disabled: false                    # soft-delete flag
supersedes: []                     # ids this memory replaces (Phase 10 reads only)
use_count: 0                       # incremented atomically on relevance injection
last_used_at: null                 # ISO timestamp of last injection
---

Body content here. Markdown. Free-form. This is what the LLM sees
in the ``## Relevant Memories`` section when the relevance scorer
picks this memory.
```

Required fields: `id`, `name`, `description`, `type`, `scope`,
`created_at`, `updated_at`. Everything else has a default.

**`signature` is auto-computed if absent** — hand-writers don't need
to compute SHA-256. The parser fills it in via
`compute_memory_signature(body, type, scope)`.

## When does the LLM see this memory?

Each time you run `oh ask "..."` or each turn of `oh chat`, the
harness runs `select_relevant_memories(query, ...)` against all your
memories:

```
score = meta_hits   * 2.0      # query tokens in name / description / tags
      + body_hits   * 1.0      # query tokens in body
      + importance  * 0.4
      + min(use_count, 5) * 0.1
      + recency_boost          # 0.3 if updated ≤14d, 0.1 if ≤30d, else 0
```

**Zero-token-hits memories are excluded entirely** — if your query
shares no words with the memory's name / description / tags / body, it
won't surface. Tokenization is Unicode-aware (handles Han characters
alongside ASCII).

Top-N selected memories (default 5, tunable via
`OPENHARNESS_MEMORY__MAX_FILES=10`) get injected as a `## Relevant
Memories` section in the system prompt. Each picked memory's
`use_count` increments atomically (closes the loop Phase 13 stale-GC
will need).

## MEMORY.md (optional index)

If you create `<storage-dir>/MEMORY.md`, the harness injects it as a
separate `## Memory` section (the "table of contents"). Convention is
a small markdown list:

```markdown
- [stripe-sdk-version](stripe-sdk-version.md) — Stripe SDK 8.x notes
- [charge-race-condition](charge-race-condition.md) — race at L42
```

Size cap is 8 KB by default (tunable via
`OPENHARNESS_MEMORY__MAX_ENTRYPOINT_BYTES`). Files larger than the cap
are **skipped entirely** (not truncated) — a half-cut index is worse
than no index.

## Inspect what's loaded

```bash
oh memory list                  # tabular: name | type | use_count | last_used | description
oh memory list --format json    # parseable JSON array
oh memory show stripe-sdk-version  # full frontmatter + body
oh memory path                  # storage dir path
```

## Disable memory entirely

```bash
oh ask --no-enable-memory "..."
# OR
OPENHARNESS_ENABLE_MEMORY=false oh ask "..."
```

When disabled, neither `## Memory` nor `## Relevant Memories`
sections appear. The system prompt is byte-identical to the
pre-Phase-10 layout.

## What Phase 11 will add (preview)

- **`extract_memories_from_turn`** — at every user turn end, the
  harness runs a secondary LLM call that proposes 0-3 new memories
  from the turn's content. You stop needing to hand-write.
- **`team` scope** — share memories across team members (with secret
  scanning).
- **`/compact` slash command** — context overflow → L4 LLM-driven
  summary. Phase 10 already has Microcompact (Phase 4) running silently.

## Sample memory

See [`stripe-sdk-version.md`](./stripe-sdk-version.md) — a minimal
valid memory file you can copy as a template.
