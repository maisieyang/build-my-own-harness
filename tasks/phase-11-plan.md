# Phase 11 Implementation Plan — Summarization Substrate

> Boundary contract: [`decisions/26-phase-11-boundary.md`](../decisions/26-phase-11-boundary.md).
> Builds on Phase 10's memory read path + Phase 4's microcompact +
> reactive PTL retry. Ships the **write-side** of memory (via
> extraction secondary pass) + the **stateful summarization
> workflows** (compact L2-L4 + session_memory checkpoint).

## Overview

**Phase 11 goal**: Ship a **shared `summarize()` primitive** that
three otherwise-unrelated features all use at runtime — full compact
(L4), `extract_memories_from_turn`, and `/compact` slash command —
plus two deterministic sub-systems that participate in compact
escalation but don't call the LLM (L2 context-collapse + L3
session_memory reuse).

The **cross-cutting invariant** (5th compounding test of the
abstraction-first compounding pattern from Phase 7c retro §3.1):

- `markdown_store/` — zero diff (Phase 11 reuses Phase 10's memory
  store + frontmatter schema without modification)
- `prompts/` — zero diff (`build_system_prompt` signature unchanged;
  compaction lives in `services/`, not in prompt assembly)
- `skills / commands / bundles / plugins / mcp / permissions /
  protocols` — zero diff (extraction read-only sandbox uses existing
  PermissionMode semantics; no new permission tier)
- `memory/model.py` — zero diff (frontmatter schema locked at D28.3;
  Phase 11 only WRITES via `MemoryStore.add_or_update`, doesn't
  redefine)

The conceptual lesson Phase 11 cashes: **`summarize()` as a substrate
primitive is the 5th independent compounding test of "design the
abstraction at the N-th rule, not the 1st"**. HKUDS upstream's three
trigger sites all share one LLM-dispatch primitive — if Phase 11's
implementation can do the same, the pattern holds.

**Total scope**: ~3-4 days, 7 capabilities, ~15-25 commits, ~1,500-
2,000 lines production code + tests.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/26-phase-11-boundary.md`](../decisions/26-phase-11-boundary.md) | D29.1 new `services/` package; D29.2 `summarize()` shared primitive; D29.3 L0-L4 escalation, L4 only LLM-calling; D29.4 5-slot deterministic checkpoint per project; D29.5 extraction as secondary LLM pass (not in-loop tool); D29.6 `/compact` REPL + `--compact-threshold` + `--no-extract` flags; D29.7 PreApiCall reactive re-run opt-in (closes Phase 4 debt); D29.8 nested `CompactSettings` + `ExtractionSettings`; D29.9 stopword set + `meta_hits >= 1` threshold; D29.10 `team` scope + 6-regex secret scan |

---

## Task list

### P11-T1: `services/` package foundation + `summarize()` primitive 🔜 NEXT

**Description**: The substrate. Pure async LLM dispatch + retry +
timeout machinery. No trigger-specific logic. Three downstream
consumers (T2 compact / T4 extract / future) construct their own
prompts + parse their own outputs; this primitive only owns the
LLM call.

**Acceptance**:
- [ ] `src/openharness/services/__init__.py` (new package marker)
- [ ] `src/openharness/services/summarize.py` — `summarize()`:
  ```python
  async def summarize(
      *,
      messages: list[ConversationMessage],
      system_prompt: str,
      model: str,
      api_client: ApiClient,
      max_tokens: int = 2048,
      timeout_seconds: float = 25.0,
      tools_disabled: bool = True,
  ) -> str: ...
  ```
  - Returns raw text response (caller parses)
  - 2 streaming retries on `OpenHarnessApiError`
  - 3 PTL retries on the summarize request itself, each dropping
    oldest 1/5 of input messages (turtles-all-the-way-down)
  - `tools_disabled=True` → empty `tools` arg forwarded to client
  - `asyncio.wait_for` timeout enforcement
- [ ] `services/__init__.py` re-exports `summarize`
- [ ] Tests (`tests/services/test_summarize.py`, ~10 cases):
  - Happy path: single call, returns text
  - Streaming retry: 1 transient failure → retries → succeeds
  - Streaming retry: 3 failures → propagates
  - PTL retry: large input that PTLs → drops oldest 20% → succeeds
  - PTL retry: 3 PTLs → propagates
  - Timeout: slow stub client → raises LoopTimeoutError (or similar)
  - tools_disabled honored: stub records empty tools list
  - System prompt passed through verbatim
  - Streaming events filtered to text only (not yield)
  - Empty messages list edge case → still calls LLM

**Files**:
- `src/openharness/services/__init__.py` (new)
- `src/openharness/services/summarize.py` (new, ~120 lines)
- `tests/services/__init__.py`, `tests/services/test_summarize.py` (new)

**Sub-units**:
- 1a — `services/` package + `summarize()` signature + happy path + tests
- 1b — Streaming retry layer + tests
- 1c — PTL retry (drop-oldest-1/5) + tests
- 1d — Timeout enforcement + tools_disabled honoring + tests

---

### P11-T2: Session memory 5-slot checkpoint writer + reader

**Description**: The deterministic L3 input. Written every user-turn
end (no LLM call), read only when compact L3 triggers. Single file
per project. Built from `tool_metadata` fields the engine maintains.

**Acceptance**:
- [ ] `src/openharness/services/session_memory.py`:
  - `get_session_memory_dir(cwd) -> Path` — mirrors `get_project_memory_dir`
    layout: `~/.openharness/session-memory/<basename>-<sha1(cwd)[:12]>/`
  - `update_session_memory_file(cwd, tool_metadata, messages) -> Path`
    — writes the checkpoint atomically (same `tempfile + os.replace`
    pattern as `mark_memory_used`)
  - `read_session_memory(cwd) -> str | None` — returns content or None
  - `_render_5_slot(tool_metadata, messages) -> str` — pure function
    that builds the markdown
- [ ] 5-slot schema:
  ```
  # Session Memory

  ## Current State
  <goal from tool_metadata["task_focus_state"], or "(none)">

  ## Next Step
  <next from tool_metadata, or "(awaiting user direction)">

  ## Verified Work
  - <last 10 from tool_metadata["verified_work"]>

  ## Active Artifacts
  - <last 10 from tool_metadata["recent_files"]>

  ## Recent Conversation
  - [User] <last 80 one-line summaries>
  - [Claude] ...
  ```
- [ ] 12,000-char cap; truncate Recent Conversation oldest-first,
  then Active Artifacts
- [ ] Single file per project, overwritten each call (atomic)
- [ ] Path.home() called at function-call-time (NOT module-scope —
  HOME-isolation fixture concern, same as `get_project_memory_dir`)
- [ ] Tests (`tests/services/test_session_memory.py`, ~10 cases):
  - Same cwd → same path
  - Different cwd same basename → distinct paths
  - First write creates dir + file
  - Second write overwrites
  - Atomic: no half-written file visible mid-write
  - Empty tool_metadata → "(none)" placeholders, valid markdown
  - 5-slot section markers all present
  - Overflow truncates Recent Conversation oldest
  - Read returns None when no file
  - Read returns full content when file exists
  - Symlinked cwd resolves to canonical (matches memory dir behavior)

**Files**:
- `src/openharness/services/session_memory.py` (new, ~180 lines)
- `tests/services/test_session_memory.py` (new)

**Sub-units**:
- 2a — `get_session_memory_dir` + path resolution + tests
- 2b — `_render_5_slot` deterministic markdown builder + tests
- 2c — `update_session_memory_file` atomic write + cap + tests
- 2d — `read_session_memory` + nil cases + tests

---

### P11-T3: Compact L2 + L3 + L4 escalation pipeline

**Description**: The biggest task. Wires L2 (context collapse) + L3
(session_memory reuse) + L4 (full compact LLM call) on top of Phase
4's L1 microcompact. Each layer only fires when the prior layer
doesn't free enough tokens. Caller invokes once per LLM request via
`auto_compact_if_needed`.

**Acceptance**:
- [ ] `src/openharness/services/compact.py`:
  - `auto_compact_if_needed(messages, model, settings, session_memory_path, api_client) -> tuple[messages, CompactResult]`
  - L0: token estimation via `count_tokens` (existing Phase 4) +
    per-message wrapper accounting for tool_use / tool_result /
    image blocks (3072 budget per image per HKUDS)
  - L2: `try_context_collapse(messages)` — long text bodies (≥2400
    chars) → head 900 + `[collapsed N chars]` + tail 500. Pure
    string transformation, idempotent.
  - L3: `try_session_memory_compaction(messages, session_memory_path)` —
    if file exists AND fresh (modified within last hour), splice older
    messages with a `[Session memory checkpoint:\n<file content>]`
    synthetic user message. No LLM call.
  - L4: `full_compact(messages, model, api_client)` — calls
    `summarize()` with the 9-slot system prompt (D29 sub-decision:
    verbatim from HKUDS). Output parsed via `<summary>...</summary>`
    regex (analysis tags stripped). Splice via boundary marker
    "[Conversation history summarized below — older messages
    elided]".
- [ ] 9-slot system prompt constant `_L4_COMPACT_SYSTEM_PROMPT`
  matching HKUDS verbatim: Primary Request / Key Technical Concepts /
  Files and Code / Errors and Fixes / Problem Solving / All User
  Messages / Pending Tasks / Current Work / Optional Next Step
- [ ] Escalation order: L0 estimate → L1 (already hook, skip) → L2 →
  L3 → L4; each layer only fires if prior didn't reduce below
  threshold
- [ ] Threshold per `settings.compact.threshold_ratio * context_window(model)`
- [ ] `CompactResult` dataclass: `applied_levels: tuple[int, ...]`,
  `original_tokens: int`, `final_tokens: int`, `compact_kind: str`
  ("none" / "context_collapse" / "session_memory" / "full")
- [ ] Engine integration: `engine/query.py` calls `auto_compact_if_needed`
  BEFORE each LLM request when `settings.compact.enabled`. Result
  emitted as observability log + (future) `CompactProgressEvent`.
- [ ] Existing Phase 4 reactive PTL retry stays as last-resort
  safety net (not replaced)
- [ ] Tests (`tests/services/test_compact.py`, ~15 cases):
  - L0 below threshold → no-op
  - L2 alone sufficient → no LLM call
  - L3 with stale checkpoint (>1h) → skips L3
  - L3 with fresh checkpoint → splice + no LLM call
  - L4 invoked when L0-L3 insufficient
  - L4 9-slot output parsed correctly
  - L4 boundary marker present in result
  - `<analysis>` tags stripped, only `<summary>` kept
  - Failure isolation: L4 LLM error → log + return un-compacted
  - `summarize` PTL retry triggered (mock LLM PTLs)
  - Image block token estimation: 3072 per image
  - `compact.enabled = False` skips L2-L4 entirely (L1 hook stays)
  - Per-image budget configurable via env var
  - Engine integration: `auto_compact_if_needed` invoked before each
    LLM request
  - CompactResult propagates `applied_levels` correctly

**Files**:
- `src/openharness/services/compact.py` (new, ~400 lines)
- `src/openharness/engine/query.py` (additive: pre-request hook call)
- `tests/services/test_compact.py` (new)

**Sub-units**:
- 3a — Token estimation L0 + per-block wrapper + tests
- 3b — L2 context-collapse pure function + tests
- 3c — L3 session_memory reuse (file read, splice) + tests
- 3d — L4 full compact (summarize call + 9-slot prompt + parse) + tests
- 3e — `auto_compact_if_needed` escalation + CompactResult + tests
- 3f — Engine integration + observability log + smoke test

---

### P11-T4: Extraction secondary pass + `MemoryStore.add_or_update`

**Description**: Closes the agent-write loop on Phase 10's memory
store. Every user turn end (after `update_session_memory_file`),
the harness runs a focused secondary LLM call that proposes 0-3
durable memories. Read-only tool sandbox; JSON output parsed and
written to memory via signature-dedup.

**Acceptance**:
- [ ] `src/openharness/services/extract.py`:
  - `EXTRACTION_SYSTEM_PROMPT` constant (Claude-Code-style guidance:
    "Save only stable, future-useful facts not derivable from current
    files, git, or docs. Don't save secrets. Prefer updating existing
    memories over duplicating. Return JSON of 0-3 records.")
  - `ExtractionResult` dataclass: `written: list[Memory]`,
    `skipped: bool`, `reason: str | None`, `error: str | None`
  - `extract_memories_from_turn(*, cwd, api_client, model, messages, settings) -> ExtractionResult`
  - JSON output parsed; malformed records dropped + warned
  - `max_records=3` cap (extra dropped + warned)
  - Skip conditions:
    - `len(messages) < 2`
    - `has_memory_writes_since(messages, memory_dir)` returns True
    - `not settings.extraction.enabled`
  - Each accepted record → `MemoryStore.add_or_update(memory)`
  - Failure isolation: outer try/except logs `memory_extract_failed`,
    returns ExtractionResult(error=...)
- [ ] `has_memory_writes_since(messages, memory_dir) -> bool`:
  - Scans messages for `write_file` / `edit_file` tool_use blocks
    targeting paths under `memory_dir`
  - Returns True if any found in this turn
- [ ] `MemoryStore.add_or_update(memory) -> None` (new method on
  `FilesystemMemoryStore`):
  - Signature-dedup: if existing memory with same signature, overwrite
  - Else: write new file via atomic `tempfile + os.replace`
  - Filename: `<slug>.md` where slug is `memory.name`; collision →
    append `-<id-suffix>`
  - **Phase 11 introduces this method — Phase 10 was read-only**
- [ ] Read-only tool sandbox: extract.py passes
  `tools_disabled=True` to summarize for now (Phase 11 sub-decision:
  inline allowlist deferred to Phase 12+ if real demand surfaces)
- [ ] `MemoryScope.TEAM` enum value added (Phase 10 D28.5 deferred)
- [ ] Team memories stored under `<storage-dir>/team/` subdirectory
- [ ] `check_team_memory_secrets(content) -> str | None`:
  - 6 regex patterns (PEM key / AWS `AKIA[0-9A-Z]{16}` / GitHub
    `gh[pousr]_.../OpenAI sk-[A-Za-z0-9_-]{20,}` / Anthropic
    `sk-ant-...` / generic `(secret|token|api_key|password)\s*[:=]\s*['"]?[^'"\s]{12,}`)
  - Returns None if clean, error string naming rule labels otherwise
- [ ] Extraction respects scope: if LLM classifies record as team,
  scan secrets; on hit, drop + log `memory_team_secret_blocked` (no
  user-facing error to keep extract failures invisible)
- [ ] Engine integration: per turn end (after
  `_update_session_memory`), call
  `_extract_durable_memories(cwd, api_client, model, messages, settings)`
  in `engine/query.py`'s finally block
- [ ] Tests (`tests/services/test_extract.py`, ~15 cases):
  - Happy path: messages with stripe discussion → extracts 1 memory
  - Empty messages → skipped with reason
  - `has_memory_writes_since` True → skipped
  - `enabled = False` → skipped
  - Malformed JSON → 0 records written, log warning
  - 5 records returned → only 3 written (cap)
  - Same-signature record → overwrites existing
  - New record with novel signature → new file
  - Tests for `check_team_memory_secrets` — 6 patterns + clean case
  - Team record with secret → silently dropped + warning
  - Team record clean → written to team/ subdir
  - Test for `has_memory_writes_since`: messages mentioning
    `write_file` targeting memory_dir → True
- [ ] Tests for `MemoryStore.add_or_update` (`tests/memory/test_store.py`
  additive): signature dedup + collision filename + atomic write +
  failure handling

**Files**:
- `src/openharness/services/extract.py` (new, ~250 lines)
- `src/openharness/memory/store.py` (+`add_or_update` method)
- `src/openharness/memory/model.py` (+TEAM enum value, allow team
  in parse_memory)
- `src/openharness/memory/team.py` (new — `check_team_memory_secrets` +
  team subdir helpers, ~80 lines)
- `src/openharness/engine/query.py` (additive: extract call in finally)
- `tests/services/test_extract.py` (new)
- `tests/memory/test_team.py` (new)
- `tests/memory/test_store.py` (additive tests for `add_or_update`)

**Sub-units**:
- 4a — `MemoryStore.add_or_update` + signature dedup + tests
- 4b — `MemoryScope.TEAM` enum + parse_memory accepts + tests
- 4c — `memory/team.py` secret scanner + tests
- 4d — `EXTRACTION_SYSTEM_PROMPT` + `extract_memories_from_turn` happy path + tests
- 4e — `has_memory_writes_since` skip logic + tests
- 4f — Engine integration + smoke test

---

### P11-T5: CLI surface — `/compact` REPL + `--compact-threshold` + `--no-extract` + nested Settings

**Description**: User-facing knobs. `/compact` REPL command for
manual L4 trigger. `--compact-threshold` CLI flag for auto threshold
tuning. `--no-extract` to disable Phase 11 extraction. Nested
`CompactSettings` + `ExtractionSettings`.

**Acceptance**:
- [ ] `src/openharness/config/settings.py`:
  - `CompactSettings` nested BaseModel: `enabled` (bool=True),
    `threshold_ratio` (float=0.83), `full_compact_retries` (int=3),
    `full_compact_timeout_s` (float=25.0), `full_compact_max_tokens`
    (int=20_000)
  - `ExtractionSettings` nested BaseModel: `enabled` (bool=True),
    `max_records_per_turn` (int=3), `model` (str | None=None),
    `timeout_s` (float=30.0)
  - `Settings.compact` + `Settings.extraction` fields
- [ ] Env-var nested overrides via D28's `env_nested_delimiter="__"`:
  - `OPENHARNESS_COMPACT__THRESHOLD_RATIO=0.7`
  - `OPENHARNESS_COMPACT__ENABLED=false`
  - `OPENHARNESS_EXTRACTION__ENABLED=false`
  - `OPENHARNESS_EXTRACTION__MODEL=qwen-turbo`
- [ ] `cli.py`:
  - `--compact-threshold FLOAT` on `oh ask` + `oh chat`
  - `--no-auto-compact` on `oh ask` + `oh chat` (sets `compact.enabled=False`)
  - `--no-extract` on `oh ask` + `oh chat` (sets `extraction.enabled=False`)
  - `/compact` built-in REPL command in `_run_chat` (sibling of
    `/help` / `/clear` / `/exit`) — triggers immediate L4 compact
    on next turn
- [ ] Extraction bootstrap: `_run_ask` + `_run_chat` wire extraction
  into engine's finally block. `enable_memory` (Phase 10) + new
  `enable_extraction` separately togglable.
- [ ] Tests (`tests/config/test_compact_settings.py`,
  `tests/cli/test_compact_repl.py`, ~12 cases combined):
  - Defaults match D29.8
  - Env-var nested overrides work (single + multiple fields)
  - `--compact-threshold 0.5` overrides settings
  - `--no-extract` propagates to extraction.enabled=False
  - `--no-auto-compact` propagates to compact.enabled=False
  - `/compact` REPL command schedules immediate L4 on next turn
  - `--enable-memory --no-extract` combo (memory works but extract
    disabled)
  - CLI flag help output shows new flags (COLUMNS=200 to avoid
    Rich truncation)

**Files**:
- `src/openharness/config/settings.py` (additive nested models +
  fields)
- `src/openharness/cli.py` (additive: 3 flags + `/compact` REPL +
  extraction bootstrap)
- `tests/config/test_compact_settings.py` (new)
- `tests/cli/test_compact_repl.py` (new)

**Sub-units**:
- 5a — `CompactSettings` + `ExtractionSettings` + nested env tests
- 5b — `--compact-threshold` / `--no-auto-compact` / `--no-extract`
  CLI flag wiring + tests
- 5c — `/compact` REPL command + immediate L4 trigger + tests
- 5d — Engine bootstrap integration for extraction in
  `_run_ask` + `_run_chat`

---

### P11-T6: PreApiCall reactive re-run (Phase 4 debt) + stopwords (Phase 10 deferred)

**Description**: Tail debts from prior phases.

**6a — PreApiCall reactive re-run (D29.7)**: closes Phase 4 retro §6.
When `engine/query.py` rebuilds the message array after a PTL retry,
PreApiCall hooks with `re_run_on_reactive_rebuild=True` re-fire so
context-injecting hooks (memory in Phase 12+, custom user hooks)
survive the rebuild.

**6b — Stopwords (D29.9)**: resolves Phase 10 D28.7 sub-decision.
Minimum stopword set + `meta_hits >= 1 OR body_hits >= 2` threshold
in `select_relevant_memories`.

**Acceptance**:
- [ ] `src/openharness/hooks/registry.py` (or `hooks/spec.py`): add
  `re_run_on_reactive_rebuild: bool = False` field to `HookSpec`
- [ ] `src/openharness/engine/query.py` reactive PTL handler: after
  rebuild, iterates registered PreApiCall hooks and re-fires only
  those with `re_run_on_reactive_rebuild=True`
- [ ] Test (`tests/hooks/test_reactive_rerun.py`, ~4 cases):
  - Hook with `re_run_on_reactive_rebuild=True` invoked exactly twice
    on PTL-retry turn (once before original, once after rebuild)
  - Hook without the flag invoked exactly once (no change)
  - Multiple flagged hooks all re-run in registration order
  - PostToolUse / PreToolUse hooks NEVER re-run (only PreApiCall)
- [ ] `src/openharness/memory/relevance.py`:
  - `_STOPWORDS` constant (~25 words: a, an, the, is, are, was, were,
    of, to, for, with, on, in, at, by, and, or, but, this, that,
    these, those, do, does, did)
  - Tokenize query → subtract stopwords
  - If `len(query_tokens_after_stopwords) < 1` → return []
  - Score as before; require `meta_hits >= 1 OR body_hits >= 2` to
    surface (raise bar on body-only matches)
- [ ] Tests (`tests/memory/test_relevance.py` additive, ~5 cases):
  - Phase 10 T6 false-positive regression: query "what is the weather
    today" + stripe memory body containing "the" → NOT injected
    after stopwords enabled
  - Query "the" alone → returns [] (zero non-stopword tokens)
  - Query "stripe" + memory body containing only "stripe" → injected
    (body_hits=1, fails surface threshold... wait, body_hits >= 2
    required)
    — actually the threshold should match meta_hits OR body_hits;
    re-evaluate during build whether body_hits=1 is sufficient
  - Han characters not in stopword set (Unicode-aware regex still works)
  - All existing Phase 10 relevance tests still pass (stopwords don't
    break meta-hit-driven scoring)

**Files**:
- `src/openharness/hooks/registry.py` or `spec.py` (additive field)
- `src/openharness/engine/query.py` (additive reactive re-run loop)
- `src/openharness/memory/relevance.py` (stopwords + surface threshold)
- `tests/hooks/test_reactive_rerun.py` (new)
- `tests/memory/test_relevance.py` (additive)

**Sub-units**:
- 6a — `HookSpec.re_run_on_reactive_rebuild` field + tests
- 6b — Engine reactive re-run loop + tests
- 6c — Stopwords + surface threshold + Phase 10 regression test
- 6d — `surface threshold` tuning: confirm `meta_hits >= 1 OR body_hits >= 2`
  is right (might need adjustment if Phase 10 tests fail)

---

### P11-T7: E2E + cross-cutting invariant + retro

**Description**: Wraps up Phase 11. End-to-end integration test
proves the full loop works: extract → memory store → relevance →
injection → use_count. Compact tests show L4 LLM call fires on
threshold + `/compact` REPL. Invariant verification across 9 +
protected layers. Phase 11 retro.

**Acceptance**:
- [ ] Integration test (`tests/services/test_e2e_phase11.py`):
  - Setup: stub LLM that responds to EXTRACTION_SYSTEM_PROMPT with
    canned JSON `[{"name": "stripe-sdk", "description": "Stripe 8.x", ...}]`
  - Run: `oh ask "discuss stripe webhook implementation"` (mock
    main convo)
  - Assert: After turn, new memory file exists in memory dir
  - Run: second `oh ask "stripe refund flow"` (new invocation)
  - Assert: stripe-sdk memory injected into system prompt;
    `use_count == 1` after this run
- [ ] Integration test (compact):
  - Build a large messages array (~165k tokens estimated)
  - Stub LLM returns `<summary>...</summary>` text
  - Run `auto_compact_if_needed` → L4 fires → message count drops
  - Verify `CompactResult.compact_kind == "full"`
- [ ] Integration test (`/compact` REPL):
  - Use `_run_chat` test pattern with stubbed input → `/compact` →
    next turn's QueryContext has compacted messages
- [ ] **Cross-cutting invariant verification**:
  - `git log --oneline fc4d833^..HEAD -- src/openharness/markdown_store/`
    → 0 Phase 11 commits
  - Same for skills / commands / bundles / plugins / mcp /
    permissions / prompts
  - `git diff` for `memory/model.py` shows only TEAM enum value
    addition + parse_memory acceptance (no schema redefinition)
  - `git diff` for `memory/store.py` shows only `add_or_update`
    method (existing methods unchanged)
  - `git diff` for `engine/query.py` shows only additive: extract
    call in finally + PreApiCall reactive re-run loop
- [ ] `learnings/phase-11.md` retro:
  - 1. Data points table (commits / tests / coverage / LoC / time)
  - 2. Per-task takeaway (T1-T7 one-liners)
  - 3. ⭐ Invariant verification result — 5th compounding test of
    substrate-first pattern; `markdown_store/` still zero diff at
    5th consumer (Phase 11 doesn't add a new consumer but reuses
    Phase 10's Memory store — passive validation)
  - 4. Conceptual lesson: did `summarize()` substrate stay
    primitive-only without "if trigger ==" branches?
  - 5. Real踩坑 (predict 3-4): summarize PTL turtles-all-the-way-
    down edge cases / session_memory file race with compact L3
    read / extraction stub LLM JSON parse brittleness / stopwords
    affecting unrelated relevance tests
  - 6. Phase 12 predictions: session snapshot + `oh ask --resume`;
    snapshot includes tool_metadata white-list; UI layer concern
- [ ] All 1608+ existing tests pass
- [ ] mypy strict + ruff clean
- [ ] Coverage ≥ 95% retained

**Files**:
- `tests/services/test_e2e_phase11.py` (new)
- `tests/services/test_compact_integration.py` (new)
- `tests/cli/test_compact_repl_e2e.py` (new — if not folded into T5)
- `learnings/phase-11.md` (new)

**Sub-units**:
- 7a — E2E extract → store → relevance loop test
- 7b — Compact L4 integration test (large messages → L4 fires)
- 7c — `/compact` REPL E2E test
- 7d — Invariant git-diff verification + commit-msg attestation
- 7e — `learnings/phase-11.md` retro
- 7f — DoD closeout + README / PLAYBOOK updates if needed

---

## Checkpoints

After each capability: **human review** of the resulting trace +
zero-diff verification. Two critical checkpoints:

- **T3 close** (compact escalation): `services/compact.py` is the
  load-bearing new module. Verify L0-L4 escalation logic matches
  the boundary doc D29.3 exactly. Bad escalation order = silent
  L4 LLM cost spikes.
- **T7 close** (E2E + invariant): the 5th compounding test of the
  "design substrate at Nth consumer" pattern (Phase 8 `markdown_store/`
  4 consumers via Phase 9/10; Phase 11 `summarize()` primitive used by
  L4 + extract + future). If any "zero diff" layer needs editing,
  **stop and re-open the boundary doc**.

The review-before-commit walkthrough applies per usual.

## Risks

| Risk | Mitigation |
|---|---|
| `summarize()` PTL retry recursion (turtles-all-the-way-down) can loop forever if model keeps PTL-ing | Hard cap 3 retries; raise back to caller after; tests cover 3-retries-exhausted case |
| Stopwords break legitimate Phase 10 tests | T6-6c regression suite re-runs all Phase 10 `test_relevance.py` cases; threshold tuning at build time |
| `services/compact.py` integration with `engine/query.py` creates per-request overhead even when no compact needed | L0 token estimation is fast (tiktoken); benchmark `auto_compact_if_needed` returns <50ms for short messages |
| 9-slot compact prompt produces output that doesn't parse cleanly via `<summary>` regex | T3-3d tests pin 4-5 known good LLM responses; failure-path returns un-compacted messages + logs |
| Extraction model defaults to main → expensive when main is qwen-max | Sub-decision: settings.extraction.model overrides + retro evaluates real cost; consider qwen-turbo default |
| Session memory file race with compact L3 read | L3 reads file as single string via `read_text`; if write happens mid-read OS-level atomicity (`os.replace`) means we read either old or new, never partial |
| PreApiCall reactive re-run breaks existing hooks that assume single invocation per request | Default `re_run_on_reactive_rebuild=False` preserves existing behavior; only opt-in hooks re-run |
| Team scope secret scanning false positive (PEM regex matches docstring example) | 6 regexes are precise (BEGIN PRIVATE KEY full string, specific prefixes); silent drop + log so user can debug |
| Engine `_extract_durable_memories` finally-block call adds 2-3 sec latency per turn | Run in background task (`asyncio.create_task`) not awaited — extraction is best-effort, not blocking turn completion |

## Risks specifically NOT mitigated (Phase 12+)

- **Session snapshot + resume UX** (`oh ask --resume`)
- **Auto-dream subprocess** (periodic background consolidation,
  stale GC)
- **Plug-in compaction strategies** (user-authored L4 prompt
  templates)
- **`oh memory add` write CLI** (explicit user-driven memory
  creation; extraction makes the case weaker)
- **Per-memory access logs / audit trail**
- **Cross-session memory deduplication via auto-dream**
- **In-tool extraction read-only sandbox** (Phase 11 uses
  `tools_disabled=True`; if Phase 12+ wants extraction to grep
  the codebase, add a permission tier)

## Pointers

- Boundary: [`decisions/26-phase-11-boundary.md`](../decisions/26-phase-11-boundary.md)
- Phase 10 boundary (read path + memory store — Phase 11 builds on top):
  [`decisions/25-phase-10-boundary.md`](../decisions/25-phase-10-boundary.md)
- Phase 10 retro (T6 §3.3 stopwords + §3.4 mid-phase merge mess):
  [`learnings/phase-10.md`](../learnings/phase-10.md)
- Phase 4 boundary + retro (microcompact + reactive PTL + PreApiCall
  debt that Phase 11 closes):
  [`decisions/10-phase-4-boundary.md`](../decisions/10-phase-4-boundary.md) +
  [`learnings/phase-4.md`](../learnings/phase-4.md) §6
- Phase 8 boundary (`markdown_store/` abstraction — Phase 11 reuses
  without modification):
  [`decisions/19-phase-8-boundary.md`](../decisions/19-phase-8-boundary.md)
- Meta-retro §3.1 — abstraction-first compounding evidence:
  [`learnings/phase-7.md`](../learnings/phase-7.md) §3.1
- HKUDS upstream reference (independent reimplementation, no code
  copy): [`REFERENCE.md`](../REFERENCE.md) §16
