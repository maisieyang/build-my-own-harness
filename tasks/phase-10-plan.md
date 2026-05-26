# Phase 10 Implementation Plan — Memory Subsystem (Static Read Path)

> Boundary contract: [`decisions/25-phase-10-boundary.md`](../decisions/25-phase-10-boundary.md).
> Builds the read-only foundation for memory; writes (extraction
> secondary pass), session persistence (snapshot + resume), and
> consolidation (auto-dream + stale GC) all defer to Phase 11/12/13.

## Overview

**Phase 10 goal**: Ship a **CLAUDE.md cascade + per-project durable
memory** read path that gives the harness two new long-term context
layers, both injected into the system prompt via the
`build_system_prompt` extension point reserved since P2-T5 (D11.5).

The **cross-cutting invariant** (4th compounding test of the Phase 8
abstraction + 3rd of the D11.5 prompt-extension contract):

- `markdown_store/` — **zero diff** when hosting the 6th consumer
  (`FilesystemMemoryStore`)
- `prompts.build_system_prompt(tools, env)` — **byte-identical** output
  when the new memory kwargs are omitted (existing callers + 233 tests
  pass unchanged)
- 10 other layers (`engine / compaction / hooks / permissions / mcp /
  plugins / skills / commands / bundles / protocols`) — **zero diff**

The conceptual lesson Phase 10 cashes: **abstraction-first compounding
works on the read substrate of a new domain**. Memory's read path is a
thin shell over already-shipped primitives; writes (Phase 11) get a
verified substrate to land on instead of building together with the
write semantics.

**Total scope**: ~3 days, 6 capabilities, ~12-18 commits, ~500-700
lines production code.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/25-phase-10-boundary.md`](../decisions/25-phase-10-boundary.md) | D28.1 cwd-hashed storage outside repo; D28.2 CLAUDE.md cascade (parents + `~/.openharness/CLAUDE.md`); D28.3 14-field frontmatter with `use_count`/`last_used_at` inlined; D28.4 `FilesystemMemoryStore` subclasses `FilesystemMarkdownStore`; D28.5 `scope: private` only; D28.6 `build_system_prompt` gains 2 additive kwargs, 3 new sections; D28.7 relevance scoring with zero-hits exclusion; D28.8 atomic frontmatter rewrite on injection; D28.9 no agent-write path; D28.10 `enable_memory: bool = True` (opt-out); D28.11 CLI `list/show/path` only |

---

## Task list

### P10-T1: `memory/` package foundation — model + paths 🔜 NEXT

**Description**: Pure data + parsing layer for memory. `Memory`
frozen dataclass satisfying `MarkdownDocument` Protocol; `parse_memory`
following the established `parse_X` pattern; `get_project_memory_dir`
path resolver. No store, no relevance, no usage — just the data the
next tasks plug into. Same shape as P9-T1 (PluginManifest) and P5c-T1
(Skill).

**Acceptance**:
- [ ] `memory/model.py` — `Memory` frozen dataclass with 14
  frontmatter fields per D28.3:
  - Required: `id` (ULID), `name` (NAME_PATTERN regex),
    `description` (non-empty), `type` (enum: user / feedback /
    project / reference), `scope` (must be `private` per D28.5)
  - Timestamps: `created_at`, `updated_at`, `last_used_at` (nullable)
  - Body-derived: `signature` (sha256 hex), `body` (the markdown body)
  - Lifecycle: `ttl_days` (nullable int), `disabled` (bool),
    `supersedes` (list of id strings)
  - Tuning: `importance` (0.0-1.0), `tags` (list of str), `use_count` (int)
  - Plus `source_path: Path` for the Protocol (file location on disk)
- [ ] `memory/model.py` — `parse_memory(path: Path) -> Memory | None`:
  - Uses `markdown_store.read_frontmatter_dict(path, logger_name="memory")`
  - Returns `None` (with warning log) on: missing required field,
    invalid name regex, invalid type/scope enum, malformed timestamps,
    `scope != "private"` (Phase 10 rejection per D28.5)
  - Computes `signature` if missing (older files written by hand
    won't have one)
- [ ] `memory/paths.py` — `get_project_memory_dir(cwd: str | Path) -> Path`:
  - Resolves to `Path.home() / ".openharness" / "memory" / f"{cwd.name}-{sha1(str(cwd.resolve()))[:12]}"`
  - Creates parent dir lazily (only on first write; reads from
    non-existent dir return empty — no eager mkdir)
- [ ] `memory/errors.py`:
  - `MemoryParseError(OpenHarnessError)` — raised by `mark_memory_used`
    when rewrite fails (caught at injection time, logged)
  - `UnknownMemoryError(OpenHarnessError)` — raised by CLI `show` /
    `path` when target not found
- [ ] `memory/__init__.py` — re-exports
- [ ] Tests (`tests/memory/test_model.py`, ~15 cases):
  - Happy path: minimal valid frontmatter parses
  - Happy path: all 14 fields parse + survive round-trip
  - Missing required field → None + warning (one case per required field)
  - Invalid `scope: team` → None + warning (Phase 10 rejection)
  - Invalid `type: bogus` → None + warning
  - Malformed YAML → None + warning
  - Missing `signature` → computed from body
  - `disabled: true` → still parses (consumer decides what to do)
- [ ] Tests (`tests/memory/test_paths.py`, ~5 cases):
  - Same cwd → same dir
  - Different cwd same basename → different sub-dir (sha1 differs)
  - Symlinked cwd → resolves to canonical path before hashing
  - Non-existent storage dir → returned path doesn't trigger mkdir
  - `~` expansion in Path.home() works on macOS + Linux

**Files**:
- `src/openharness/memory/__init__.py` (new)
- `src/openharness/memory/model.py` (new, ~180 lines)
- `src/openharness/memory/paths.py` (new, ~30 lines)
- `src/openharness/memory/errors.py` (new, ~20 lines)
- `tests/memory/__init__.py`, `tests/memory/test_model.py`,
  `tests/memory/test_paths.py` (new)

**Sub-units**:
- 1a — `Memory` dataclass + enums + `__post_init__` validation + tests
- 1b — `parse_memory` via `read_frontmatter_dict` + tests
- 1c — `get_project_memory_dir` + error types + tests

---

### P10-T2: `FilesystemMemoryStore` — 6th `markdown_store/` consumer

**Description**: Thin store subclass that adopts the shared
`FilesystemMarkdownStore[T]` primitive. **The critical substrate
test**: if any change to `markdown_store/` is needed, Phase 8's
abstraction failed under its 4th independent consumer.

**Acceptance**:
- [ ] `memory/store.py` — `FilesystemMemoryStore(FilesystemMarkdownStore[Memory])`:
  - Constructor: `__init__(*, project_dir: Path)` only — no
    `global_dir` parameter (Phase 10 has no cross-project layer per
    D28.5). Internally calls `super().__init__(global_dir=None, ...)`.
  - `parser=parse_memory`, `log_event_prefix="memory"`
- [ ] `memory/store.py` — `EmptyMemoryStore(EmptyMarkdownStore[Memory])` —
  one-line subclass for the "no memory configured" sentinel (matches
  EmptyCommandStore / EmptySkillStore pattern)
- [ ] **D28.5 enforcement double-check**: confirm `parse_memory`
  (from T1) rejects `scope != "private"` — covered by T1 tests but
  re-verify at store-discovery level with a fixture file
- [ ] Tests (`tests/memory/test_store.py`, ~8 cases):
  - Empty dir → `discover() == {}`
  - One valid memory → `discover()[name] == Memory(...)`
  - Two memories with same name → second-loaded wins + collision log
    (default `FilesystemMarkdownStore` behavior)
  - Malformed file mixed with valid → valid loaded, malformed skipped
  - File with `scope: team` → skipped + warning (D28.5)
  - `get(name)` returns single memory or None
  - `discover()` cached on first call (second call doesn't re-scan)
  - `EmptyMemoryStore.discover() == {}` (sentinel)

**Critical invariant verification** ⭐:
- [ ] `git diff <P9 close> -- src/openharness/markdown_store/`
  shows **zero diff** — substrate held under 4th test

**Files**:
- `src/openharness/memory/store.py` (new, ~80 lines)
- `tests/memory/test_store.py` (new)

**Sub-units**:
- 2a — `FilesystemMemoryStore` + `EmptyMemoryStore` + tests
- 2b — `markdown_store/` zero-diff verification (1-line commit-msg
  attestation; no code)

---

### P10-T3: Relevance scoring + usage tracking

**Description**: The two read-side primitives that turn a passive
memory pile into a query-relevant injection. `select_relevant_memories`
implements the D28.7 scoring; `mark_memory_used` does the atomic
frontmatter rewrite from D28.8.

**Acceptance**:
- [ ] `memory/relevance.py` — `select_relevant_memories(query, memories, *, max_results=5) -> list[Memory]`:
  - Tokenization: `re.findall(r"\w+", text.lower())` on
    `{name, description, tags joined, body}` per memory
  - Per-memory `meta_hits = |{tok for tok in query_tokens if tok in
    meta_text_tokens}|`; `body_hits` similarly for body
  - **Zero-hits exclusion** (D28.7 load-bearing rule): if
    `meta_hits == 0 and body_hits == 0`, exclude entirely (do not
    fall through to "most recent N")
  - Score:
    ```
    score = meta_hits * 2.0
          + body_hits * 1.0
          + importance * 0.4
          + min(use_count, 5) * 0.1
          + recency_boost
    ```
    where `recency_boost = 0.3 if (now - updated_at) ≤ 14d else 0.1
    if ≤ 30d else 0.0`
  - Sort by `(-score, -updated_at)`; return first `max_results`
  - `disabled=True` memories excluded regardless of score
- [ ] `memory/usage.py` — `mark_memory_used(memory: Memory) -> None`:
  - Reads current frontmatter from `memory.source_path`
  - Increments `use_count`, sets `last_used_at = datetime.now(UTC).isoformat()`
  - Atomic write: `tempfile.NamedTemporaryFile` in same dir →
    `os.replace(tmp_path, memory.source_path)`
  - On failure (disk full, permission, file gone): log
    `memory_usage_update_failed` warning with `source_path` + error;
    **do not raise** — prompt building must continue
  - Concurrency: relies on `os.replace` atomicity; no explicit lock
    (deferred to Phase 11 when extraction can write concurrently)
- [ ] Tests (`tests/memory/test_relevance.py`, ~12 cases):
  - Empty memory list → empty result
  - Query "stripe" + memory with name "stripe-sdk" → selected
  - Query "stripe" + memory with NO token match → excluded (D28.7
    zero-hits rule, regression-critical)
  - Two memories, higher meta_hits wins over higher body_hits
  - Two equal meta_hits, higher `importance` wins
  - Two equal score, more recent `updated_at` wins (tiebreaker)
  - `max_results=3` caps at 3 even with 10 matching memories
  - `disabled=True` excluded regardless of high score
  - Recency boost: memory updated 7d ago beats same-score 60d ago
  - Han-character query "支付" + Han-character memory body → match
    (Python `\w` covers Han via Unicode category)
  - `use_count` capped at 5 (memory at use_count=100 doesn't dominate)
  - Query with stopword-only tokens (e.g., "what is the") + memory
    with no overlap → excluded (no special stopword handling, just
    natural zero-hits)
- [ ] Tests (`tests/memory/test_usage.py`, ~6 cases):
  - `mark_memory_used` increments use_count atomically
  - `mark_memory_used` updates `last_used_at` to ISO format with UTC
  - All other frontmatter fields unchanged after rewrite
  - Body unchanged after rewrite (whitespace-exact)
  - File deleted between read and write → warning logged, no raise
  - Read-only filesystem → warning logged, no raise

**Files**:
- `src/openharness/memory/relevance.py` (new, ~100 lines)
- `src/openharness/memory/usage.py` (new, ~60 lines)
- `tests/memory/test_relevance.py` (new)
- `tests/memory/test_usage.py` (new)

**Sub-units**:
- 3a — Tokenization helper + `select_relevant_memories` + tests
- 3b — `mark_memory_used` atomic rewrite + failure handling + tests

---

### P10-T4: `prompts/` refactor + CLAUDE.md cascade + memory injection

**Description**: The widest-blast-radius capability. Refactor
`prompts.py` → `prompts/` package; add CLAUDE.md discovery + loading;
extend `build_system_prompt` with 2 additive kwargs; wire memory
manifest + relevant memories formatters. **The byte-identical
invariant** lives here: 233+ existing tests must pass unchanged.

**Acceptance**:
- [ ] `src/openharness/prompts.py` → `src/openharness/prompts/` package:
  - `prompts/__init__.py` re-exports the existing public API
    (`build_system_prompt`, `EnvironmentInfo`, `detect_environment`) so
    `from openharness.prompts import build_system_prompt` still works
  - `prompts/system.py` — existing `build_system_prompt` body
    (extended with new kwargs per D28.6)
  - `prompts/claudemd.py` — `discover_claude_md_files(cwd)` +
    `load_claude_md_prompt(cwd, *, max_chars_per_file=12_000)` per D28.2
  - `prompts/memory_inject.py` — `format_memory_index_section(manifest)` +
    `format_relevant_memories_section(memories, *, max_body_chars=8_000)`
- [ ] `build_system_prompt` signature gains 2 keyword-only kwargs
  (per D28.6):
  ```python
  def build_system_prompt(
      tools: list[ToolSpec],
      env: EnvironmentInfo,
      *,
      skill_store: SkillStore | None = None,
      claude_md_content: str | None = None,         # NEW
      memory_manifest: MemoryManifest | None = None,  # NEW
  ) -> str: ...
  ```
  Section order (D28.6): base / Tools / Available Skills / Environment /
  **Project Instructions** (CLAUDE.md) / **Memory** (MEMORY.md index) /
  **Relevant Memories** (scored bodies)
- [ ] `MemoryManifest` dataclass (in `memory/model.py` or
  `prompts/memory_inject.py`):
  - `entrypoint_content: str | None` — MEMORY.md raw text, capped at
    `max_entrypoint_bytes`
  - `relevant: list[Memory]` — already-scored top-N from
    `select_relevant_memories`
  - When both fields empty → injection helpers return None (no
    sections emitted)
- [ ] **Critical invariant** ⭐ — `build_system_prompt(tools, env)`
  (calling form without the new kwargs) produces **byte-identical**
  output to today. This is the test that catches accidental section
  reordering / whitespace shifts.
- [ ] `Settings.enable_memory: bool = True` field added to
  `src/openharness/config/settings.py` (D28.10)
  - Env var: `OPENHARNESS_ENABLE_MEMORY=false`
  - CLI flag: `--enable-memory / --no-enable-memory`
- [ ] `Settings.memory: MemorySettings` nested model:
  ```python
  class MemorySettings(BaseModel):
      max_files: int = 5
      max_entrypoint_bytes: int = 8_000
      max_body_chars: int = 8_000
      max_claude_md_chars: int = 12_000
  ```
  - Env vars: `OPENHARNESS_MEMORY__MAX_FILES=10` etc. (pydantic-settings
    nested-delimiter convention)
- [ ] Bootstrap wiring in `cli._run_ask` (and `cli._run_chat`):
  - After existing skill_store / command_store construction
  - If `enable_memory` resolved True:
    - `memory_store = FilesystemMemoryStore(project_dir=get_project_memory_dir(Path.cwd()))`
    - `memories = memory_store.discover().values()`
    - `claude_md = load_claude_md_prompt(Path.cwd(), max_chars_per_file=settings.memory.max_claude_md_chars)`
    - For each user turn (or on first message): compute
      `relevant = select_relevant_memories(query=user_prompt_text, memories=memories, max_results=settings.memory.max_files)`
    - Call `mark_memory_used(m)` for each m in relevant
    - Construct `MemoryManifest(entrypoint_content=load_memory_md(...), relevant=relevant)`
    - Pass `claude_md_content=claude_md, memory_manifest=manifest` to
      `build_system_prompt`
- [ ] Tests (`tests/prompts/test_byte_identical.py`, ~3 cases):
  - `build_system_prompt(tools, env)` → same string as today
  - `build_system_prompt(tools, env, skill_store=store)` → same as today
  - All inputs but new kwargs default → byte-identical
- [ ] Tests (`tests/prompts/test_claudemd.py`, ~10 cases):
  - `discover_claude_md_files(cwd)` from `<project>/src/x/y/` finds
    `<project>/CLAUDE.md`
  - `~/.openharness/CLAUDE.md` appended last in cascade
  - `.claude/CLAUDE.md` at any level included
  - `.claude/rules/*.md` sorted + included
  - Symlinked CLAUDE.md de-duped (appears once)
  - No CLAUDE.md anywhere → returns empty list
  - `load_claude_md_prompt` returns None on empty discovery
  - File > 12_000 chars truncated with `[truncated]` marker
  - Wrapping format: `# Project Instructions` heading + per-file
    `## <abspath>` subsections + ` ```md ` fenced bodies
  - Permission-denied file (chmod 000) → skipped + warning, others
    still included
- [ ] Tests (`tests/prompts/test_memory_inject.py`, ~8 cases):
  - `format_memory_index_section(MemoryManifest(entrypoint=None,...))`
    → returns None
  - With entrypoint content → returns `## Memory\n\n<content>`
  - `format_relevant_memories_section([])` → returns None
  - With 3 memories → returns `## Relevant Memories\n\n## <name>\n\n<body>...`
  - Body > 8_000 chars truncated with marker
  - `build_system_prompt(..., memory_manifest=MemoryManifest(None, []))`
    → no Memory or Relevant Memories sections emitted (empty manifest
    behaves like None)
  - `build_system_prompt(..., claude_md_content="...", memory_manifest=...)`
    → Project Instructions BEFORE Memory BEFORE Relevant Memories
  - All sections joined by `\n\n` per existing convention

**Files**:
- `src/openharness/prompts.py` → DELETE
- `src/openharness/prompts/__init__.py` (new, re-exports)
- `src/openharness/prompts/system.py` (new, ~120 lines — existing
  body + 2 new kwargs)
- `src/openharness/prompts/claudemd.py` (new, ~80 lines)
- `src/openharness/prompts/memory_inject.py` (new, ~80 lines)
- `src/openharness/config/settings.py` (+`enable_memory` +
  `MemorySettings`)
- `src/openharness/cli.py` (+memory bootstrap in `_run_ask` +
  `_run_chat`)
- `tests/prompts/test_byte_identical.py` (new — critical invariant)
- `tests/prompts/test_claudemd.py` (new)
- `tests/prompts/test_memory_inject.py` (new)

**Sub-units**:
- 4a — `prompts/` package refactor (move existing code; re-export);
  byte-identical tests added FIRST (TDD: write the assertion that
  catches drift, then refactor under that net)
- 4b — `claudemd.py` discover + load + tests
- 4c — `memory_inject.py` formatters + tests
- 4d — `build_system_prompt` 2 new kwargs + integration tests
- 4e — `Settings.enable_memory` + `MemorySettings` + env var tests
- 4f — `cli._run_ask` + `_run_chat` bootstrap wiring + smoke test

---

### P10-T5: CLI surface — `oh memory list / show / path`

**Description**: User-facing read-only inspection. No `add` / `edit` /
`remove` per D28.9 (writes wait for Phase 11). Mirrors P9-T4 shape
but smaller surface.

**Acceptance**:
- [ ] `oh memory list` (text default, `--format json`):
  - Scans `get_project_memory_dir(cwd)`, parses each `.md`
  - Output columns: name | type | use_count | last_used_at | description
    (truncated to 60 chars)
  - Sorted by `(-use_count, name)` — most-used first, then alphabetical
  - Invalid memory files shown as `(invalid: <reason>)` row
  - Empty dir → `(no memories — storage at <path>)` message
- [ ] `oh memory show <name-or-id>`:
  - Lookup by `name` first, then by `id` if name not found
  - Prints frontmatter (YAML) + body
  - Unknown → exit 1 + `UnknownMemoryError` with available names
- [ ] `oh memory path`:
  - Prints resolved storage dir to stdout
  - Exit 0 even if dir doesn't exist (path is computable)
- [ ] `--enable-memory` / `--no-enable-memory` flags added to
  `oh ask` and `oh chat` (override `Settings.enable_memory`)
- [ ] Tests (`tests/cli/test_memory_cli.py`, ~10 cases):
  - `list` on empty storage → "(no memories)" message
  - `list` on 3 memories → all 3 rows + correct sort
  - `list --format json` → valid JSON array
  - `list` with 1 malformed file → other 2 valid still listed,
    malformed shown as `(invalid)`
  - `show <name>` → frontmatter + body printed
  - `show <id>` → resolves to same memory
  - `show <unknown>` → exit 1 + available names
  - `path` → prints absolute path
  - `path` when dir doesn't exist → still prints path, exit 0
  - `--no-enable-memory oh ask "..."` → CLAUDE.md and memory absent
    from system prompt (verifiable via `--dry-run` or trace)

**Files**:
- `src/openharness/cli.py` (+`memory_app` Typer subapp with 3 commands;
  +`--enable-memory` flag on `ask` + `chat`)
- `tests/cli/test_memory_cli.py` (new)

**Sub-units**:
- 5a — `list` + `show` (read-only) + tests
- 5b — `path` + `--enable-memory` flag wiring + tests

---

### P10-T6: E2E smoke + invariant verification + retro

**Description**: End-to-end verification that a hand-written
`memory/foo.md` flows through discovery → relevance → injection →
usage-tracking. Cross-cutting invariant explicitly verified via git
diff. Retro written.

**Acceptance**:
- [ ] `examples/memory/README.md`:
  - Documents how a user / agent would hand-write a memory file
  - Shows the storage path resolution
  - Cross-links Phase 11 future-write surface
- [ ] Integration test (`tests/memory/test_e2e.py`):
  - Setup: write `<storage-dir>/stripe.md` with valid frontmatter
    (name=stripe-sdk-version, description mentions Stripe + Refund,
    body contains "Stripe SDK 8.x" + "refund API")
  - Run: `oh ask --dry-run "How do I issue a refund via Stripe?"`
  - Assert: system prompt contains `## Project Instructions` if a
    CLAUDE.md is present
  - Assert: system prompt contains `## Relevant Memories` with
    `stripe-sdk-version` body
  - Assert: after run, frontmatter `use_count == 1`,
    `last_used_at` is a recent ISO timestamp
  - Second run with same query: `use_count == 2`
  - Different query "what time is it?" → memory NOT in prompt; use_count
    unchanged (zero-hits exclusion holds)
- [ ] **Cross-cutting invariant `git diff <P9 close>` verification**:
  - `markdown_store/` **zero diff** ⭐ (4th compounding test)
  - `engine/` zero diff
  - `compaction/` zero diff
  - `hooks/` zero diff
  - `permissions/` zero diff
  - `mcp/` zero diff
  - `plugins/` zero diff
  - `skills/` zero diff
  - `commands/` zero diff
  - `bundles/` zero diff
  - `protocols/` zero diff
  - `prompts/` shows ONLY: move from `prompts.py` (delete) + 4 new
    files (`__init__.py`, `system.py`, `claudemd.py`, `memory_inject.py`)
    + 2 new kwargs on `build_system_prompt`; existing callers
    byte-identical (verified by `test_byte_identical.py`)
  - `config/settings.py` shows ONLY: `enable_memory` field +
    `MemorySettings` nested model + env-var bindings
  - `cli.py` shows additive only: memory bootstrap in
    `_run_ask`/`_run_chat` + `oh memory` subcommand + `--enable-memory`
    flag declarations
- [ ] `learnings/phase-10.md` retro:
  - 1. Data points table (commits / tests / coverage / LoC / time spent
    per task)
  - 2. Per-task takeaway (T1-T6 one-liners)
  - 3. ⭐ **Invariant verification result** — 4th compounding test of
    Phase 8 `markdown_store/` abstraction (held / held with caveat /
    broken); 3rd compounding test of D11.5 `build_system_prompt`
    extension contract (byte-identical hold?)
  - 4. Conceptual lesson: read substrate before write semantics — did
    splitting Phase 10 from Phase 11 pay off, or did Phase 10 leak
    write concerns it shouldn't have?
  - 5. Real踩坑 (predict 2-3): `prompts.py → prompts/` refactor breaking
    something subtle / atomic rewrite races in parallel test workers /
    relevance scoring picking wrong memory due to stopword absence
  - 6. Phase 11 predictions: extraction LLM pass infrastructure /
    secondary-pass `EXTRACTION_SYSTEM_PROMPT` design / signature-based
    dedup interaction with hand-edited memories
- [ ] Coverage ≥ 95% retained
- [ ] CI green on Python 3.10 + 3.11
- [ ] mypy strict + ruff check + ruff format clean
- [ ] DoD checklist all green (decisions/25 §Acceptance for Phase 10
  close-out)

**Files**:
- `examples/memory/README.md` (new)
- `tests/memory/test_e2e.py` (new)
- `learnings/phase-10.md` (new)

**Sub-units**:
- 6a — Example memory README + sample frontmatter file
- 6b — E2E integration test
- 6c — Invariant git-diff verification + commit-msg attestation
- 6d — `learnings/phase-10.md` retro
- 6e — DoD closeout + README / PLAYBOOK updates (if needed)

---

## Checkpoints

After each capability: **human review** of the resulting trace +
zero-diff verification. The two critical checkpoints:

- **T2 close**: `markdown_store/` zero diff confirms Phase 8
  abstraction holds under 6th consumer. If it doesn't,
  **stop and re-open the boundary doc** — this is the 4th independent
  abstraction test failing, not a Phase 10 implementation detail.
- **T4 close**: `test_byte_identical.py` GREEN confirms the
  `build_system_prompt` extension contract from D11.5 holds. If 233+
  existing caller tests show ANY diff in prompt output, the new
  kwargs leaked into the default code path — **stop and fix the
  refactor, not the tests**.

The review-before-commit walkthrough applies per usual: after GREEN
on each task, walk through the diff against the acceptance criteria
before any `git commit`.

## Risks

| Risk | Mitigation |
|---|---|
| `prompts.py → prompts/` refactor breaks `from openharness.prompts import X` imports somewhere | `prompts/__init__.py` re-exports all public names; `test_byte_identical.py` catches any behavior drift; T4 sub-unit 4a explicitly runs full test suite BEFORE adding new kwargs |
| `mark_memory_used` atomic rewrite races with parallel pytest workers reading the same file | Use `pytest --forked` or per-test-tmpdir fixtures so each test gets isolated memory dir; atomic-write tests use real `os.replace` (no mock) but in tmpdir |
| Relevance scoring picks wrong memory because of stopword pollution (e.g., query "what is the stripe key") | Phase 10 ships without stopwords (per D28.7 sub-decision); risk acknowledged; revisit in retro if false-positive rate is real |
| User hand-writes memory `.md` with invalid frontmatter and runs `oh ask` — does it crash? | `parse_memory` returns None + warning; `discover()` skips Nones (existing `FilesystemMarkdownStore` behavior); `oh memory list` shows it as `(invalid)`; `oh ask` proceeds with other memories |
| `Settings.memory` nested model has pydantic-settings env-var binding gotcha (`OPENHARNESS_MEMORY__MAX_FILES` vs `OPENHARNESS_MEMORY_MAX_FILES`) | T4 sub-unit 4e explicitly tests both forms; document the chosen convention in settings docstring |
| CLAUDE.md cascade reads `~/.openharness/CLAUDE.md` even on first run from a foreign cwd → unexpected global injection | Documented behavior per D28.2; users who don't want global fallback simply don't create that file; covered by acceptance test "no CLAUDE.md anywhere → no section" |
| `prompts/` refactor + memory injection together → large T4 commit hard to review | T4 sub-units split refactor (4a) from feature add (4b-4f); 4a commits standalone (byte-identical green), then 4b-4f layer on top |
| Frontmatter rewrite in `mark_memory_used` accidentally drops fields it doesn't know about (forward-compat break) | Implementation reads full frontmatter dict, mutates only `use_count` + `last_used_at`, writes full dict back; T3 test covers "all other fields unchanged" round-trip |

## Risks specifically NOT mitigated (Phase 11+)

- **Concurrent write contention** — two parallel `oh ask` invocations
  in the same cwd both update the same memory's `use_count` at the
  same time. `os.replace` atomicity means one of the increments may
  be lost (last-writer-wins). Acceptable for Phase 10's expected
  single-user-single-process pattern; Phase 11 may add file locking
  when extraction can write concurrently.
- **`supersedes` chain resolution** — Phase 10 reads the field but
  doesn't suppress superseded memories. Phase 11 may add.
- **`ttl_days` expiration enforcement** — stored but not enforced.
  Phase 13's stale-memory GC owns this.
- **MEMORY.md index auto-regeneration** — Phase 10 reads whatever
  the user wrote; Phase 11 extraction will append to it.
- **Memory body search index** — relevance does linear scan over all
  memories every turn. Fine for <100 memories; if a project grows
  past that, Phase 11+ may add a token-index sidecar.
- **Cross-session memory injection via PreApiCall hook + reactive
  truncation interaction** — the Phase 4 retro debt. Phase 10
  sidesteps by injecting in `build_system_prompt` not via hook. Debt
  remains open for Phase 11 compaction.

## Pointers

- Boundary: [`decisions/25-phase-10-boundary.md`](../decisions/25-phase-10-boundary.md)
- Phase 8 boundary (`markdown_store/` extraction — 6th consumer test
  in P10-T2): [`decisions/19-phase-8-boundary.md`](../decisions/19-phase-8-boundary.md)
- Phase 4 retro (cross-session memory deferred from P4, PreApiCall debt
  noted): [`learnings/phase-4.md`](../learnings/phase-4.md) §3.4, §6
- P2-T5 / D11.5 (`build_system_prompt` extension contract that P10-T4
  exercises): [`decisions/06-phase-2-boundary.md`](../decisions/06-phase-2-boundary.md)
- Phase 5c boundary (most recent prior consumer of D11.5):
  [`decisions/12-phase-5c-skills-boundary.md`](../decisions/12-phase-5c-skills-boundary.md)
- Phase 9 plan (template followed by this plan):
  [`tasks/phase-9-plan.md`](./phase-9-plan.md)
- Meta-retro §3.1 — abstraction-first compounding evidence (Phase 10
  is another 7c-shape compounding test):
  [`learnings/phase-7.md`](../learnings/phase-7.md) §3.1
- HKUDS upstream reference (independent reimplementation, no code copy):
  [`REFERENCE.md`](../REFERENCE.md) §11
