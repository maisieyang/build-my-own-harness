# Phase 17 Implementation Plan — Memory Substrate Cleanup + Methodology Evolution

> Boundary contract: [`decisions/37-phase-17-boundary.md`](../decisions/37-phase-17-boundary.md).
> Phase 16 retro (deferred frontier): [`learnings/phase-16.md`](../learnings/phase-16.md) §3.

## Overview

**Phase 17 goal**: close the three deferred items from Phase 16
retro's deferred frontier + propagate the §六 Wiring audit
discipline into the project methodology. This is a cleanup-only
phase with no new capability surface.

**Cross-cutting invariant** (per D37 §六 Wiring audit):

- `permissions/` — zero diff (T5's memory_dir exception preserved)
- `hooks/` — zero diff
- `services/snapshot.py` + `services/session_memory.py` —
  zero diff
- `services/compact.py` — zero diff
- `prompts/` — zero diff (the Phase 16 system prompt + injection
  stays exactly as-is)
- `protocols/` — zero diff (no new types)
- `tools/` — zero diff
- All Phase 16 commits (78f2a90 / b5be31c / 9780a95 / 0b6b912 /
  50bc5fe / be22459) — zero modification

Expected net diff: roughly **-300 LoC** (deletion-dominant; Phase 11
extraction + tests is ~250 LoC, the new D37.1 + D37.4 + D37.5 +
CLAUDE.md additions are ~50 LoC).

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/37-phase-17-boundary.md`](../decisions/37-phase-17-boundary.md) | D37.1 Memory.id optional + auto-generation; D37.2 other fields stay; D37.3 Phase 11 extraction full deletion (services/extract.py + engine call sites + QueryContext fields + ExtractionSettings + CLI flag + tests); D37.4 oh memory list/show new schema; D37.5 discover() skip MEMORY.md; D37.6 CLAUDE.md adds §六 Wiring audit discipline |
| [`decisions/36-phase-16-memory-pivot-boundary.md`](../decisions/36-phase-16-memory-pivot-boundary.md) | All 14 D36 invariants preserved (Phase 17 cleanup leaves Phase 16 contract intact) |

---

## Task list

### P17-T1: Memory.id optional + parser auto-generation (D37.1, D37.2)

**Description**: ``Memory`` dataclass changes ``id: str`` to
``id: str | None = None`` (with default factory or post-init
auto-generation). ``parse_memory(path)`` no longer warns on missing
``id``; instead computes ``id = sha1(name + str(path.resolve()))[:16]``
when the frontmatter omits it. Other Phase 10/11 fields
(``importance`` / ``use_count`` / ``last_used_at`` / ``tags`` /
``scope`` / ``created_at`` / ``updated_at``) become optional with
sensible defaults if missing from frontmatter (D37.2 — no field
deletions). Files that DO have these fields (e.g., Phase 10/11-era
files) continue to parse byte-identical.

**Acceptance**:

- [ ] ``Memory`` dataclass ``id`` is optional (``str | None = None``)
- [ ] ``parse_memory(path)`` accepts CC-style frontmatter
  (``name + description + metadata.type`` only) without warning
- [ ] When ``id`` is missing, parse_memory generates it via
  ``hashlib.sha1((memory.name + str(path.resolve())).encode()).hexdigest()[:16]``;
  generated id is deterministic across reparses of the same file
- [ ] Phase 10/11-era files with full schema continue to parse
  byte-identical (all fields preserved; ``id`` taken from frontmatter,
  NOT regenerated)
- [ ] Optional fields with no frontmatter value default to:
  ``importance=0``, ``use_count=0``, ``last_used_at=None``,
  ``tags=[]``, ``scope=MemoryScope.PRIVATE``, ``created_at=now()``,
  ``updated_at=now()``
- [ ] New unit tests cover: CC-style frontmatter parses cleanly;
  generated id is deterministic; full Phase 10 frontmatter parses
  byte-identical
- [ ] ``memory_missing_id`` WARN log event is removed from
  ``parse_memory`` (no longer emitted)
- [ ] ``memory_missing_frontmatter`` WARN stays (still fires for
  truly malformed files — D37 keeps the malformed-file diagnostic
  signal)

### P17-T2: Phase 11 extraction full deletion (D37.3)

**Description**: Delete the entire Phase 11 extraction stack per
D37.3 table. ``services/extract.py`` removed; ``_maybe_extract_memories``
function + invocation site in ``engine/query.py`` removed;
``QueryContext.extract_enabled`` / ``extract_max_records`` /
``extract_timeout_s`` fields removed (and their wiring through
``cli.py`` ``_run_ask`` / ``_run_chat``); ``ExtractionSettings`` class
removed from ``config/settings.py`` along with ``Settings.extraction``
field; ``--no-extract`` typer.Option removed from CLI; affected tests
deleted.

**Acceptance**:

- [ ] ``rm src/openharness/services/extract.py``
- [ ] ``rm tests/services/test_extract.py``
- [ ] ``rm tests/services/test_e2e_phase11.py``
- [ ] ``grep -rn "extract_memories_from_turn\|ExtractionSettings\|_maybe_extract_memories" src/openharness/`` → zero hits
- [ ] ``grep -rn "\-\-no-extract" src/openharness/`` → zero hits
- [ ] ``engine/query.py`` no longer awaits or imports any extraction
  symbol; turn-end flow is just ``_maybe_write_turn_end_metadata``
- [ ] ``engine/context.py`` ``QueryContext`` has zero ``extract_*``
  fields; existing ``QueryContext(...)`` callers in ``cli.py`` are
  updated to no longer pass these
- [ ] ``config/settings.py`` ``Settings`` has zero ``extraction``
  field; existing references in ``cli.py`` are removed
- [ ] ``cli.py`` ``oh ask`` and ``oh chat`` typer signatures no
  longer accept ``--no-extract``; the body no longer reads or
  forwards extraction config
- [ ] ``tests/config/test_compact_extraction_settings.py`` —
  ``CompactSettings`` test classes preserved; ``ExtractionSettings``
  test classes removed (the file becomes
  ``test_compact_settings.py`` if a rename feels cleaner; rename
  optional)

### P17-T3: FilesystemMemoryStore.discover() skip MEMORY.md (D37.5)

**Description**: One-line filter in ``FilesystemMemoryStore.discover()``
(or ``_scan`` whichever does the glob) that excludes any file whose
filename equals ``MEMORY.md`` (case-sensitive). Hard-coded skip per
D37.5 rationale — explicit > "skip files without frontmatter"
heuristic which would silently drop genuine bug cases.

**Acceptance**:

- [ ] ``FilesystemMemoryStore.discover()`` no longer attempts to
  parse files named ``MEMORY.md``
- [ ] ``memory_missing_frontmatter`` WARN no longer fires on
  ``MEMORY.md`` even when the file exists
- [ ] Unit test: directory containing ``MEMORY.md`` + 1 valid memory
  file → discover() returns the 1 memory, with zero warnings
- [ ] Unit test: directory containing ``MEMORY.md`` + 0 other files
  → discover() returns empty dict, with zero warnings
- [ ] The skip targets the bare filename only — a memory file named
  ``something-MEMORY.md`` (unlikely but legal) still parses

### P17-T4: oh memory list/show new schema (D37.4)

**Description**: ``oh memory list`` renders the three D36.10 fields
(name / description / type) per memory, alphabetized by name.
``oh memory show <name>`` prints frontmatter + body. Field fallbacks
per D37.4: missing description → ``(no description)``; missing type
→ ``(unknown)``.

**Acceptance**:

- [ ] ``oh memory list`` output columns: ``name`` (max 40 chars,
  truncated with ``…`` if longer), ``type``, ``description`` (max
  60 chars, truncated)
- [ ] Memories sorted by ``name`` (case-insensitive, alphabetical)
- [ ] ``oh memory show <name>`` prints the file contents verbatim
  (frontmatter + body)
- [ ] ``oh memory path <name>`` unchanged (still prints the file
  path)
- [ ] ``oh memory list`` exits cleanly when memory_dir is empty or
  doesn't exist (prints ``(no memories yet)``)
- [ ] Memories written by the Phase 16 dogfood (the
  ``user_role_and_values.md`` and ``user_parenting_6yo.md`` files in
  the project's memory dir) appear in ``oh memory list`` after this
  task (manual verification, recorded in the retro)
- [ ] CLI tests cover: list with mixed CC + legacy memories,
  list with empty dir, show with non-existent name (clean error),
  field fallback rendering when description/type is missing

### P17-T5: CLAUDE.md methodology + Phase 17 retro

**Description**: Update ``CLAUDE.md`` to add the §六 Wiring audit
discipline as part of the four-step phase loop's step-1 boundary doc
expectations (D37.6). The change is a tight insertion describing what
§六 must capture (each runtime layer the contract touches +
one-sentence verdict per layer: unchanged / requires extension /
requires bypass) and why (Phase 16 retro Gap A meta-lesson). After
all tasks land + dogfood validation passes, draft
``learnings/phase-17.md`` retro per the standard four-section
structure.

**Acceptance**:

- [ ] CLAUDE.md gets a new subsection introducing the §六 Wiring
  audit expectation. Placement: after "The four-step phase loop"
  table, before "Spec at the right altitude"
- [ ] Subsection lists the default candidate layers to audit:
  permissions, hooks, snapshot / session_memory, compaction,
  observability, CLI surface, eval substrate
- [ ] Subsection cites D37 §六 as the reference implementation
- [ ] Subsection cites Phase 16 retro Gap A as the motivating
  incident (one sentence)
- [ ] ``learnings/phase-17.md`` written after T1-T4 land,
  following the §1 What worked / §2 What missed / §3 Predictions /
  §4 Abstractions tested structure
- [ ] Retro records whether the §六 Wiring audit in D37 itself
  caught anything during execution (was the "no cross-layer
  collision" prediction correct?)
- [ ] Retro records the actual LoC delta vs the boundary doc's
  prediction (~ -300 LoC)

---

## Open frontier (deferred past Phase 17)

Per [`docs/ideas/memory-first-principles.md`](../docs/ideas/memory-first-principles.md) §十一 + Phase 16 retro:

1. Memory conflict resolution
2. Auto-GC / MEMORY.md age-out (200-line cap behavior over time)
3. Multi-session concurrent writes
4. Team-scope user paths (D36.13 ``team/`` directory but no real
   user yet)
5. Plugin / skill-triggered memory writes (decision surface #5 per
   D35)
6. Full frontmatter schema redesign (if dogfood ever surfaces a
   demand to drop the unused Phase 10/11 fields outright)

These stay deferred until a real driver surfaces. Phase 17 closing
should produce a clean baseline where each of these can be a
single-purpose phase if/when justified.
