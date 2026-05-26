# Phase 11 Boundary — Summarization Substrate

> Status: locked at Phase 11 entry, 2026-05-26.
>
> Scope note: Phase 11 introduces the **summarization substrate** that
> three otherwise-unrelated features share at runtime. All three "凝结
> 认知"(condense cognition)at different time-scales using the same
> LLM-as-summarizer + prompt-template machinery:
>
> 1. **Full compact L4** — context overflow → LLM produces a 9-slot
>    summary of older messages, prepended via boundary marker.
> 2. **`extract_memories_from_turn`** — per user-turn end, secondary
>    LLM pass proposes 0-3 durable memories from the turn's content,
>    written into the Phase 10 memory store.
> 3. **`/compact` slash command** — manual user trigger for L4.
>
> Plus two deterministic sub-systems that DON'T use the LLM but
> participate in the compact escalation pipeline:
>
> 4. **L2 context-collapse** — head/tail truncate long text bodies
>    (compaction/-level primitive).
> 5. **L3 session_memory reuse** — load deterministic 5-slot
>    markdown checkpoint written every turn, splice in lieu of L4
>    (saves the L4 LLM call when checkpoint is fresh).
>
> Phase 10's read path + memory store + relevance scoring all
> remain in place; Phase 11 only adds the **write surface**
> (extraction) + the **summarization machinery**. The Phase 4
> reactive PreApiCall debt also gets closed (per Phase 4 retro
> §6).
>
> Related work references:
> - **Upstream HKUDS/OpenHarness §16** has the 4-layer compact
>   escalation + `services/compact/` + `services/session_memory/` +
>   `services/memory_extract/`. Phase 11 reimplements the read-side
>   contract (5-slot checkpoint shape + 9-slot summary schema +
>   extraction JSON output) independently.
> - **Phase 10 D28.7 sub-decision** flagged stopwords TODO; Phase
>   11 retro from P10 §3.3 documented the false-positive case
>   E2E test surfaced. Phase 11 resolves the stopwords question.
> - **Phase 10 D28.5** deferred `team` scope; Phase 11 evaluates
>   alongside extraction's write path (secret scanning lives here).
> - **Phase 4 retro §6** documented the PreApiCall + reactive-
>   truncation interaction limitation. Phase 11 closes it.
> - **Claude Code's `/compact` slash command** is the user-visible
>   model; we ship the same trigger surface without copying its
>   prompt template.

## Triggering observation

Three high-value features (compact L4 / extraction / /compact) all
need the **same primitive operation**: hand a slice of conversation
to the LLM with a structured-output system prompt, parse the result,
splice back into context or memory store. Without a shared substrate
each one duplicates:

- Token estimation (which model? what counts as context?)
- Prompt-template assembly (fixed-slot output schema?)
- LLM client call (max_tokens? timeout?)
- Failure handling (retry on streaming error? PTL retry on the
  summarization request itself?)
- Output parsing (JSON for extract, structured tags for compact L4)

HKUDS upstream confirmed the 3-trigger / 1-substrate model in their
`services/compact/__init__.py`. Phase 11 ships the same shape: **one
`summarize()` primitive, three trigger sites that each construct
their own prompt + parse their own output**.

Phase 4 already shipped:

- L1 microcompact (`TruncateToolResultHook` — PostToolUse hook clears
  old `tool_result` content)
- Reactive PTL retry in `engine/query.py` (3 retries, drops oldest
  tool_use/tool_result pairs)

Phase 11 stacks L2 / L3 / L4 on top of L1 — the 4-layer escalation
becomes complete, and the **`/compact` user-visible verb** finally
has a backend.

Concurrently, the Phase 10 memory store has a read path but no
agent-driven write path. `extract_memories_from_turn` closes that
loop: every turn-end the harness runs a focused secondary LLM call
that proposes durable memories. The Phase 10 `mark_memory_used`
atomic write infrastructure is reused for the new file creation.

---

## In scope

### D29.1 — `services/` package: new home for summarization workflows

Phase 11 introduces a new `src/openharness/services/` package:

```
src/openharness/services/
├── __init__.py
├── summarize.py        # The shared primitive — LLM dispatch + retry
├── compact.py          # 4-layer escalation (L1 ref existing, L2+L3+L4 new)
├── session_memory.py   # 5-slot checkpoint writer + reader
└── extract.py          # extract_memories_from_turn secondary pass
```

**Why `services/` (not extending `compaction/`)**: separation of
concerns. `compaction/` houses **deterministic byte-level primitives**
(token estimation, head/tail truncation, microcompact hook).
`services/` houses **stateful workflows that orchestrate LLM calls +
files**. The L1 microcompact stays in `compaction/`; L2 context-
collapse helper *could* live in either but goes in `compaction/`
since it's pure-string. L3+L4 + extraction live in `services/`.

This matches HKUDS upstream's split (their `services/compact/` ≈ our
`services/compact.py` + `services/session_memory.py`).

### D29.2 — `summarize()` primitive: 3 triggers, 1 LLM dispatch

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
) -> str:
    """Call the LLM with a structured-output system prompt.

    Returns the raw text response. Caller parses (extract uses JSON,
    compact uses <summary> tags).
    """
```

**Each trigger constructs its own ``system_prompt`` and parses its
own output.** The primitive only does LLM dispatch + retry +
timeout. No "if trigger == compact" branches — the primitive doesn't
know what trigger called it.

3 retry levels (matching HKUDS):
- **Streaming retry**: 2 attempts on transient API failures
- **PTL retry on summarize itself**: 3 attempts dropping oldest 1/5
  of input messages each retry (turtles-all-the-way-down — if the
  summarization request is too big, summarize a smaller chunk)
- **Caller responsible for ladder retry**: e.g., L4 calls summarize
  once; on failure the caller decides what to do (e.g., emit error
  via CompactProgressEvent and continue with un-compacted prompt)

`tools_disabled=True` means the secondary pass cannot trigger tool
calls — same as HKUDS upstream's `tools=[]`. Extraction has a
separate read-only-tools relaxation (see D29.4).

### D29.3 — Compact L2/L3/L4 escalation

Append to the existing L1 microcompact + L2 reactive (from Phase 4):

| Layer | Mechanism | LLM call? |
|---|---|---|
| L0 | Token estimation (`count_tokens` from Phase 4 + per-message wrapper) | ❌ |
| L1 | Microcompact: clear old `tool_result` content | ❌ (Phase 4 hook) |
| **L2** | Context collapse: long-text head/tail truncate with marker | ❌ (new, deterministic) |
| **L3** | Session memory reuse: substitute older messages with the 5-slot checkpoint file | ❌ (new, file read) |
| **L4** | Full compact: LLM call producing 9-slot summary, splice via boundary marker | ✅ (new) |

Escalation logic in `services/compact.py`:

```python
async def auto_compact_if_needed(
    *, messages, model, settings, session_memory_path, api_client, ...
) -> tuple[list[ConversationMessage], CompactResult]:
    estimated = estimate_message_tokens(messages, model=model)
    if estimated < threshold(model, settings):
        return messages, CompactResult.no_op()
    # L1: already done by PostToolUse hook; skip here (no double-clear)
    # L2:
    messages, l2_result = try_context_collapse(messages)
    if estimated_after_l2 < threshold: return messages, l2_result
    # L3:
    if session_memory_path.exists():
        messages, l3_result = try_session_memory_compaction(
            messages, session_memory_path
        )
        if l3_result.applied: return messages, l3_result
    # L4: LLM call
    messages, l4_result = await full_compact(
        messages, model=model, api_client=api_client
    )
    return messages, l4_result
```

**Caller**: `engine/query.py` invokes `auto_compact_if_needed`
**before** each LLM request (proactive). The existing reactive PTL
retry stays as last-resort safety net.

**Trigger frequency**: every request when `estimated > threshold`.
Threshold per D29.8.

### D29.4 — Session memory file (5-slot deterministic checkpoint)

Storage layout (parallel to memory dir per D28.1):

```
~/.openharness/session-memory/<basename>-<sha1(cwd)[:12]>/
└── checkpoint.md     # SINGLE file per project, overwritten every turn
```

**5-slot markdown schema**:

```markdown
# Session Memory

## Current State
Find bug in charge.py

## Next Step
(Awaiting user direction on fix approach)

## Verified Work
- Confirmed race condition at charge.py:42

## Active Artifacts
- charge.py

## Recent Conversation
- [User] find the bug in charge.py
- [tool] read_file charge.py
- [Claude] Found a race condition at line 42
```

Built deterministically from:
- `tool_metadata["task_focus_state"]` (current goal + next step) — new
  field introduced in Phase 11 (engine writes via Phase 4 metadata
  hook). For Phase 11 minimum, fall back to "(none)" if metadata
  empty.
- `tool_metadata["verified_work"]` — recent verified actions (engine
  writes when tool succeeds without error). Cap last 10.
- `tool_metadata["recent_files"]` / `recent_artifacts` — last
  ~10 files touched.
- `messages[-N:]` — last 80 message one-liners (per HKUDS upstream).

**No LLM call.** Written at turn end via `update_session_memory_file`,
called from engine's per-turn-end hook (extends Phase 10's
`/* memory only */` hook surface).

Cap: 12,000 chars per HKUDS upstream. If overflow, truncate Recent
Conversation slot first (oldest one-liners), then Active Artifacts.

### D29.5 — `extract_memories_from_turn` secondary pass

Per user turn end, after `update_session_memory_file` runs, call:

```python
async def extract_memories_from_turn(
    *, cwd, api_client, model, messages, max_records=3,
) -> ExtractionResult:
    """Run a focused LLM call that proposes 0-3 durable memories.

    Returns ExtractionResult(written=[...Memory], skipped=bool, reason).
    """
```

**Specification**:
- **System prompt**: `EXTRACTION_SYSTEM_PROMPT` — Claude-Code-style
  guidance ("Save only stable, future-useful facts not derivable from
  current files / git / docs. Don't save secrets. Return JSON of 0-3
  records.")
- **Output**: JSON list `[{type, name, body, scope}, ...]`
- **Max records**: 3 per turn (cap)
- **Tools**: read-only (read_file / grep / glob / `bash` whitelist
  for `ls / cat / pwd / git log / git diff`) — same sandbox HKUDS
  uses
- **Model**: same as main conversation by default (Phase 11 sub-
  decision: cheaper variant via separate Settings key, default to
  same model)
- **Skip conditions**:
  - `len(messages) < 2` (no turn happened)
  - `has_memory_writes_since(messages, memory_dir)` — main convo
    already wrote memory via filesystem tools; extracting again would
    duplicate
  - `not settings.enable_extraction`
- **Write**: each record → `MemoryStore.add_or_update(memory)` —
  signature-dedup against existing memories (Phase 10 `compute_memory_signature`
  already in place)
- **Validation**: rejected records logged but don't fail the
  extraction (one bad record doesn't poison the others)

**Closing the read/write loop**: each new memory written here will
be picked up by Phase 10's `select_relevant_memories` on subsequent
turns — `use_count` then increments via existing `mark_memory_used`.

### D29.6 — `/compact` slash command + CLI

User-visible:

- **Inside `oh chat`**: typing `/compact` triggers an immediate L4
  compact of the conversation history, regardless of token threshold.
- **Inside `oh ask`**: not applicable (single-turn) — no `/compact`
  needed.
- **CLI flag**: `oh ask --compact-threshold 0.5` (float, fraction
  of context budget). Default 0.83. `--no-auto-compact` disables
  L2/L3/L4 entirely (L1 microcompact stays — Phase 4 contract).
- **CLI flag**: `oh ask --no-extract` disables Phase 11 extraction
  for this invocation. Default ON via `enable_extraction` setting.

Built-in `/compact` is implemented as a built-in REPL command in
`_run_chat` (similar to `/help` / `/clear` / `/exit`), NOT as a
Filesystem command. Users can override behavior by writing their own
`~/.openharness/commands/compact.md` IF they want a custom prompt
template — but the default behavior is hard-coded.

### D29.7 — PreApiCall reactive-rebuild fix (Phase 4 retro debt)

Phase 4 retro §6 documented: "PreApiCall hook does not re-run after
reactive compaction, so memory-injection-via-hook would be silently
dropped by the time the retried request fires."

Phase 11 fix:

- `engine/query.py`'s reactive PTL handler re-runs **only** the
  `PreApiCall` hooks (NOT `PreToolUse` etc.) after rebuilding the
  message array. Hooks that idempotently inject context (like memory)
  re-fire and survive the compact rebuild.
- New hook flag: `re_run_on_reactive_rebuild: bool = False`. Default
  False to preserve all Phase 4 / Phase 5e hook semantics. Memory
  injection (currently in `build_system_prompt`, not a hook) doesn't
  need this — but if Phase 12+ moves memory to a PreApiCall hook,
  the flag is ready.

**Acceptance**: a `PreApiCall` hook that records each invocation
should fire **twice** on a turn that triggers reactive PTL retry —
once before the original request, once after rebuild.

### D29.8 — Settings extensions

```python
class CompactSettings(BaseModel):
    enabled: bool = True              # L2-L4 toggle; L1 always on
    threshold_ratio: float = 0.83     # fraction of context budget that triggers
    full_compact_retries: int = 3     # PTL retries on the summarize req itself
    full_compact_timeout_s: float = 25.0
    full_compact_max_tokens: int = 20_000


class ExtractionSettings(BaseModel):
    enabled: bool = True
    max_records_per_turn: int = 3
    model: str | None = None          # None → use main conversation's model
    timeout_s: float = 30.0


class Settings(BaseSettings):
    ...
    compact: CompactSettings = Field(default_factory=CompactSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
```

Env vars (using P10's `env_nested_delimiter="__"`):
- `OPENHARNESS_COMPACT__THRESHOLD_RATIO=0.7`
- `OPENHARNESS_EXTRACTION__ENABLED=false`
- `OPENHARNESS_EXTRACTION__MODEL=qwen-turbo`
- etc.

### D29.9 — Stopwords (Phase 10 D28.7 sub-decision resolved)

Phase 11 ships a **minimum stopword set** + **`meta_hits >= 1`
threshold**:

```python
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were",
    "of", "to", "for", "with", "on", "in", "at", "by",
    "and", "or", "but", "this", "that", "these", "those",
})
```

In `select_relevant_memories`:
- Tokenize query, **subtract stopwords**
- If query has fewer than 1 non-stopword token → return [] (no
  signal)
- Score as before; require `meta_hits >= 1 OR body_hits >= 2` to
  surface (raise the bar for body-only matches since they're
  noisier)

**Why minimum set** (not full NLTK):
- Pure stdlib, no new dependency
- ~25 words cover 80% of false-positive cases (per Phase 10 retro)
- Doesn't break technical queries where stopwords carry no signal
- Easy to evaluate and adjust per `learnings/phase-11.md`

### D29.10 — `team` scope (Phase 10 D28.5 deferred — Phase 11 enables)

Phase 11 evaluates `team` scope alongside extraction's write path
because secret scanning belongs in the write layer:

- `MemoryScope.TEAM` added to enum
- `parse_memory` accepts `scope: team` (no longer rejects)
- Team memories live in `<storage-dir>/team/` subdirectory
- Write path (`extract.py` or future `oh memory add`) runs
  `check_team_memory_secrets(content)` — regex scan for 6 patterns
  (PEM key / AWS key / GitHub token / OpenAI key / Anthropic key /
  generic `secret=`)
- Secret hit → drop the record, log warning under `memory_team_secret_blocked`
- Relevance scoring treats `team` memories same as `private`
  (D29.9 stopwords apply to both)

Phase 11 ships scope=team support **only via extraction's auto-
classification**. The agent decides `scope=team` based on the
EXTRACTION_SYSTEM_PROMPT guidance (e.g., "use team when the fact
applies to the whole team, not just you"). Phase 12+ may add an
explicit `oh memory add --scope team` CLI.

---

## Cross-cutting invariant

Phase 11 must not change the following layers (zero diff vs
fc4d833^ at Phase 10 close):

- `markdown_store/` — substrate held through Phase 10 (4 consumers);
  Phase 11 reuses but doesn't modify
- `skills/` / `commands/` / `bundles/` / `plugins/` / `mcp/` —
  zero diff
- `permissions/` — Phase 11's extraction read-only sandbox uses
  existing PermissionMode semantics (no new permission tier)
- `prompts/` — Phase 10's `build_system_prompt` signature stays
  exactly as is; Phase 11 doesn't add new kwargs (compaction lives
  in `services/`, not in prompt assembly)
- `memory/model.py` — frontmatter schema is locked at Phase 10
  D28.3; Phase 11 only WRITES via extraction, doesn't redefine
- `protocols/` — no new event types from Phase 11 (compact uses
  existing `CompactProgressEvent` style if needed; extraction is
  silent — observability log only)

Where change IS allowed (all additive):

- `src/openharness/services/` — new package
- `src/openharness/compaction/` — additive L2 helper if it fits
  the deterministic-byte-level rubric; otherwise L2 lives in
  `services/compact.py`
- `src/openharness/engine/query.py` — extends the finally-block
  call sites (`_update_session_memory` / `_extract_durable_memories`
  / `_schedule_auto_dream` deferred to Phase 13). One PTL retry
  block extended with PreApiCall re-run loop.
- `src/openharness/memory/` — add `add_or_update` write API used
  by extraction. Phase 10's read API + `mark_memory_used` stay.
- `src/openharness/config/settings.py` — +`CompactSettings` +
  `ExtractionSettings` nested models
- `src/openharness/cli.py` — `--no-extract` / `--compact-threshold`
  flags + `/compact` REPL command + extraction bootstrap

If any "zero diff" layer needs editing, **stop and re-open the
boundary doc**.

---

## Out of scope (Phase 12+)

- **Session snapshot + `oh ask --resume`** — full conversation
  history persistence + resume UX. Phase 12 (UI-layer concern,
  per HKUDS architecture).
- **Auto-dream subprocess** — periodic background memory
  consolidation + stale GC. Phase 13 (depends on snapshot dir for
  multi-session scanning).
- **`oh memory add` write CLI** — explicit user-driven memory
  creation. Phase 12+ (extraction makes the case weaker; revisit).
- **Compact compaction L4 retry-on-failure ladder** — Phase 11
  ships the basic 3-PTL-retry; if real-world failure rate
  surfaces a need for outer-loop retry-with-alternate-model,
  Phase 12+ adds.
- **Per-memory access logs / audit trail** — beyond `use_count` +
  `last_used_at`, Phase 13 may add.
- **Cross-session memory deduplication** — when two sessions
  extract similar memories with different bodies, Phase 13's
  auto-dream consolidates.
- **`/memory` slash command** that opens an editor — Phase 12+.
- **Plug-in compaction strategies** — users authoring their own
  L4 prompt templates via `~/.openharness/compact/template.md`.
  Phase 12+.

---

## Critical decisions (D29.x)

| ID | Decision | Why |
|---|---|---|
| **D29.1** | New `services/` package for stateful LLM-orchestration workflows | Separates from `compaction/` (deterministic byte-level); matches HKUDS upstream split |
| **D29.2** | `summarize()` primitive: 3 triggers / 1 LLM dispatch; no "if trigger ==" branches | Avoids substrate growth into trigger-specific cases; caller owns prompt + parse |
| **D29.3** | Compact L0-L4 escalation; L4 is the only LLM-calling layer | Token-cheap layers (L1-L3) tried first; L4 only when necessary |
| **D29.4** | Session memory file: single 5-slot markdown checkpoint per project, overwritten every turn, **no LLM call** | Provides L3 reuse target without per-turn LLM cost; cheap insurance |
| **D29.5** | `extract_memories_from_turn` is a **secondary LLM pass** at turn-end, NOT a tool in the main loop | Cleaner separation; agent doesn't have to decide "should I write a memory now" in the main loop; failure isolated |
| **D29.6** | `/compact` is a built-in REPL command; flags `--compact-threshold` + `--no-extract` | Manual control alongside auto; threshold matches HKUDS upstream |
| **D29.7** | PreApiCall hook re-runs on reactive rebuild **only if `re_run_on_reactive_rebuild=True`**; default False | Closes Phase 4 debt without breaking existing hooks |
| **D29.8** | Nested `CompactSettings` + `ExtractionSettings` per the P10 `env_nested_delimiter="__"` convention | Consistent with `memory: MemorySettings` pattern |
| **D29.9** | Minimum stopword set (≈25 words) + `meta_hits >= 1` threshold | Resolves Phase 10 D28.7 sub-decision based on T6 E2E false positive |
| **D29.10** | `team` scope enabled via extraction's auto-classification + 6-regex secret scan | Phase 10 D28.5 deferred; extraction is the natural moment |

---

## Dependency direction

```
services/                              (new package)
├── __init__.py
├── summarize.py                       ← primitive: LLM dispatch + retry
├── compact.py                         ← L2 + L3 + L4 escalation
├── session_memory.py                  ← 5-slot checkpoint writer/reader
└── extract.py                         ← extract_memories_from_turn

compaction/                            ← extended for L2 helper (additive)
├── truncate.py                        ← +context_collapse helper (if here)
└── (rest unchanged)

memory/
├── store.py                           ← +add_or_update for write path
└── (rest unchanged from Phase 10)

engine/query.py                        ← extends finally block calls
                                        + PreApiCall reactive re-run

config/settings.py                     ← +CompactSettings + ExtractionSettings

cli.py                                 ← +/compact REPL command
                                        + --compact-threshold + --no-extract
                                        + extraction bootstrap in _run_ask + _run_chat

prompts/                               ← ZERO DIFF
markdown_store/                        ← ZERO DIFF
skills/ commands/ bundles/ plugins/    ← ZERO DIFF
mcp/ permissions/ protocols/           ← ZERO DIFF
```

---

## Sub-decisions deferred to build

Three open questions resolved tentatively now, locked at build time:

- **L4 9-slot summary schema exact wording**: HKUDS uses Primary
  Request / Key Technical Concepts / Files and Code / Errors and
  Fixes / Problem Solving / All User Messages / Pending Tasks /
  Current Work / Optional Next Step. Phase 11 lean: **copy this
  schema verbatim** — it's been validated in production. Revisit
  in Phase 11 retro if specific slots cause friction.
- **Extraction read-only tool sandbox shape**: limit to which
  tools? Tentative: `read_file / grep / glob` + bash whitelist
  for `ls / cat / pwd / head / tail / wc / git log / git diff /
  git status`. Reject all other Bash commands + all write tools.
  Implement as `PermissionMode.EXTRACTION_READONLY` enum value
  OR as a tool-allowlist check inside `services/extract.py`. Lean:
  **inline allowlist in extract.py**; if multiple secondary passes
  appear in Phase 13+, refactor into a permission tier.
- **Token estimation accuracy**: HKUDS uses `tiktoken` with a 4/3
  padding factor for safety. Phase 4 already uses tiktoken.
  Phase 11 sub-decision: **keep 4/3 padding** + add per-image
  estimation budget (3072 tokens per image, configurable). Match
  HKUDS so cross-validation against upstream is meaningful.

---

## Acceptance for Phase 11 close-out (template)

### `summarize()` primitive (D29.2)

- [ ] `services/summarize.py::summarize()` defined with the exact
  signature above
- [ ] 3 streaming-retry attempts on `OpenHarnessApiError`
- [ ] 3 PTL retries on the summarize request itself; each drops
  oldest 1/5 of input messages
- [ ] `tools_disabled=True` forwards an empty tools list to the
  client
- [ ] Timeout enforced via `asyncio.wait_for`; raises
  `LoopTimeoutError` on expiry
- [ ] Unit tests: happy path / streaming retry / PTL retry / timeout /
  tools_disabled honored

### Compact escalation (D29.3)

- [ ] L2 context-collapse helper produces head/tail-truncated text
  with `[collapsed N chars]` marker; threshold 2400 chars per
  HKUDS
- [ ] L3 session_memory reuse: if checkpoint file exists and is
  fresh (<1h old), splice into messages array, no LLM call
- [ ] L4 full compact: calls `summarize()` with 9-slot system
  prompt; output parsed via `<summary>...</summary>` regex; spliced
  via boundary marker
- [ ] Escalation order: L0 estimate → L1 (already hook) → L2 → L3
  → L4; each layer only runs if prior didn't reduce below threshold
- [ ] `services/compact.py::auto_compact_if_needed()` called from
  `engine/query.py` before each LLM request (proactive)
- [ ] Existing Phase 4 reactive PTL retry remains as last-resort
- [ ] Coverage: 6-8 unit tests covering each escalation level + 2
  integration tests

### Session memory file (D29.4)

- [ ] `services/session_memory.py::update_session_memory_file()`
  writes 5-slot markdown checkpoint
- [ ] Storage: `~/.openharness/session-memory/<basename>-<sha1(cwd)[:12]>/checkpoint.md`
- [ ] Single file per project (overwritten each turn)
- [ ] Build deterministically from `tool_metadata` fields; no
  LLM call
- [ ] Called from engine finally-block after each user turn
- [ ] Cap: 12,000 chars; truncate Recent Conversation oldest first
- [ ] Coverage: 5 unit tests covering shape + overwrite + cap

### Extraction (D29.5)

- [ ] `services/extract.py::extract_memories_from_turn()` defined
- [ ] `EXTRACTION_SYSTEM_PROMPT` text constant with Claude-Code-
  style guidance
- [ ] JSON output parser tolerates malformed records (drop +
  warn, don't fail extraction)
- [ ] `max_records=3` cap enforced (extra records dropped + warn)
- [ ] Read-only tool sandbox per sub-decision
- [ ] Skip when `len(messages) < 2`
- [ ] Skip when `has_memory_writes_since(messages, memory_dir)`
  detects main convo already wrote
- [ ] Skip when `settings.extraction.enabled = False`
- [ ] Each accepted record → `MemoryStore.add_or_update(memory)`
- [ ] Signature dedup: same content → overwrite, not duplicate
- [ ] Failure isolated: one bad LLM call logs warning, doesn't
  crash turn
- [ ] Coverage: 8 unit tests covering skip conditions + parse + dedup

### `/compact` slash command (D29.6)

- [ ] Inside `oh chat`, typing `/compact` triggers L4 immediately
  regardless of token threshold
- [ ] CLI: `oh ask --compact-threshold 0.5` overrides default 0.83
- [ ] CLI: `oh ask --no-extract` disables Phase 11 extraction
- [ ] CLI: `oh ask --no-auto-compact` disables L2/L3/L4 (L1
  microcompact preserved per Phase 4 contract)
- [ ] Coverage: 4 integration tests

### PreApiCall reactive re-run (D29.7)

- [ ] `HookSpec.re_run_on_reactive_rebuild: bool = False` field
  added
- [ ] `engine/query.py` reactive PTL handler re-runs all
  PreApiCall hooks whose `re_run_on_reactive_rebuild=True`
- [ ] Default-False contract preserves Phase 4 / 5e behavior
- [ ] Test: hook with `re_run_on_reactive_rebuild=True` invoked
  exactly twice on a PTL-retry turn; hook without it invoked once

### Settings + nested env vars (D29.8)

- [ ] `CompactSettings` + `ExtractionSettings` nested models
- [ ] `OPENHARNESS_COMPACT__THRESHOLD_RATIO=0.7` env var works
- [ ] `OPENHARNESS_EXTRACTION__MODEL=qwen-turbo` env var works
- [ ] CLI flags override env vars override defaults (standard
  precedence)

### Stopwords (D29.9)

- [ ] Minimum stopword set (~25 words) constant in
  `memory/relevance.py`
- [ ] Tokens after stopword removal: if < 1 non-stopword token →
  return []
- [ ] Surface threshold: `meta_hits >= 1 OR body_hits >= 2`
- [ ] Phase 10 E2E false-positive case (query "what is the
  weather today" + stripe memory) → NOT injected with stopwords
  enabled (regression test for D29.9)

### Team scope (D29.10)

- [ ] `MemoryScope.TEAM` enum value added
- [ ] `parse_memory` accepts `scope: team`
- [ ] Team memories stored under `<dir>/team/`
- [ ] `check_team_memory_secrets()` regex scan (6 patterns)
- [ ] Extraction writes scope=team to team subdir; secret hits
  dropped + warned
- [ ] Coverage: 5 tests covering scope round-trip + secret blocking

### Cross-cutting invariant verification

- [ ] `git diff <Phase 10 close fc4d833^>..HEAD -- src/openharness/markdown_store/` zero
- [ ] Same for skills / commands / bundles / plugins / mcp /
  permissions / prompts / protocols / memory/model.py
- [ ] `git diff` for `engine/query.py` shows only additive
  hook re-run extension + extraction finally-block call
- [ ] `git diff` for `memory/store.py` shows only `add_or_update`
  method added; existing methods unchanged

### Quality gates

- [ ] mypy strict + ruff check + ruff format clean
- [ ] Coverage ≥ 95% retained
- [ ] CI green on Python 3.10 + 3.11
- [ ] All ≥1608 existing tests pass without modification

---

## Pointers

- **HKUDS upstream §16 compact + extract + session_memory**
  (independent reimplementation, no code copy):
  [`REFERENCE.md`](../REFERENCE.md) §16
- **Phase 10 boundary (D28 read path + memory store + relevance —
  Phase 11 builds on top):**
  [`decisions/25-phase-10-boundary.md`](./25-phase-10-boundary.md)
- **Phase 10 retro (T6 §3.3 stopwords + §3.4 mid-phase merge
  mess — both directly inform Phase 11):**
  [`learnings/phase-10.md`](../learnings/phase-10.md)
- **Phase 4 boundary + retro (microcompact + reactive PTL +
  PreApiCall debt that Phase 11 closes):**
  [`decisions/10-phase-4-boundary.md`](./10-phase-4-boundary.md)
  + [`learnings/phase-4.md`](../learnings/phase-4.md) §6
- **Phase 8 boundary (`markdown_store/` abstraction — Phase 11
  reuses without modification):**
  [`decisions/19-phase-8-boundary.md`](./19-phase-8-boundary.md)
- **Meta-retro §3.1 — abstraction-first compounding evidence
  (Phase 11's summarize-primitive-three-triggers tests it for
  the 5th time):**
  [`learnings/phase-7.md`](../learnings/phase-7.md) §3.1
- **Phase 12 preview**: `oh ask --resume` (session snapshot UI
  layer). Will read `session_memory/` checkpoint? No — Phase 11's
  checkpoint is for L3 compact reuse only; Phase 12 writes its
  own JSON snapshot via UI layer. See HKUDS reference §15.
