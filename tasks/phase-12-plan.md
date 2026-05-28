# Phase 12 Implementation Plan — Session Snapshot + Resume

> Boundary contract: [`decisions/27-phase-12-boundary.md`](../decisions/27-phase-12-boundary.md).
> Builds on Phase 11's `services/` package + session_memory writer
> design + `tool_metadata` data structure. Ships the **resume
> user feature** + the **engine-side writer wiring** (Phase 11 debt).

## Overview

**Phase 12 goal**: Make every `oh ask` / `oh chat` turn produce a
JSON snapshot of the run state. Add `--resume` to load the latest
snapshot for the current cwd back into the next turn's
`QueryContext`. Phase 11's 5-slot session_memory checkpoint, which
Phase 11 designed but never actually wired the writer for, lands
here too — same engine call site, same `tool_metadata` producer,
two consumers (L3 compact + resume).

The **cross-cutting invariant** (6th compounding test of the
abstraction-first compounding pattern from Phase 7c retro §3.1):

- `markdown_store/` — zero diff (Phase 12 doesn't touch the
  store; extraction's write path stays untouched)
- `memory/` — zero diff (resume re-discovers; doesn't mutate
  schema or store)
- `prompts/` — zero diff (`build_system_prompt` signature
  unchanged; resume loads the prompt verbatim from snapshot)
- `skills / commands / bundles / plugins / mcp / permissions /
  protocols / hooks` — zero diff (resume reconstructs runtime
  registries fresh from the harness's normal bootstrap path)

Only `engine/` (one factory + one per-turn-end addition),
`config/settings.py` (one nested model), `cli.py` (one flag
mirrored on ask + chat), and the new `services/snapshot.py`
module are modified.

`engine/messages.py` gets one new pure helper (`collect_turn_metadata`)
shared between the session_memory writer and the snapshot writer
— matching D30.6's single-producer design.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/27-phase-12-boundary.md`](../decisions/27-phase-12-boundary.md) | D30.1 scope = resume + snapshot + Phase 11 debt fold-in; D30.2 JSON v1 parallel to 5-slot markdown; D30.3 atomic write per user turn end; D30.4 `--resume` opt-in; D30.5 3 staleness signals (git HEAD warn, cwd mismatch refuse, version mismatch refuse); D30.6 engine static-heuristic `tool_metadata` producer; D30.7 `QueryContext.from_snapshot` factory; D30.8 nested `SnapshotSettings`; D30.9 session_memory writer wiring |

---

## Task list

### P12-T1: `tool_metadata` producer + engine wiring for session_memory writer (Phase 11 debt) 🔜 NEXT

**Description**: Phase 11 designed the data flow but the writer was
never called. Lands the single-producer / two-consumer architecture
ahead of T3 (which adds the second consumer). This task fully
closes Phase 11's debt while standing alone behaviorally (L3 compact
now has actual data to read).

**Acceptance**:
- [ ] `src/openharness/engine/messages.py::collect_turn_metadata(messages, prior_metadata=None) -> dict`:
  - `recent_files`: extracts `path` / `file_path` from `ToolUseBlock`
    where `name in {"Read", "Write", "Edit"}`; deduped within turn
  - `verified_work`: for each tool_use producing a successful
    (non-error) `ToolResultBlock`, append
    `f"{tool_name}: {content[:60]}"`; capped at 10 most recent
  - `task_focus_state`: returns `{"goal": None, "next_step": None}`
    (Phase 13 evaluates LLM upgrade)
  - `prior_metadata` (optional): when resuming, the previous turn's
    metadata is the seed — recent_files accumulate across turns
    capped at last 10
- [ ] `src/openharness/engine/query.py` per-turn-end finally block
  (after `_maybe_extract_memories`, before `yield ConversationCompleteEvent`):
  ```python
  if context.session_memory_path is not None:
      tool_metadata = collect_turn_metadata(final_messages)
      try:
          update_session_memory_file(
              context.cwd, tool_metadata, final_messages
          )
      except OSError as exc:
          logger.warning("session_memory_write_failed", error=str(exc))
  ```
- [ ] Errors caught + logged; turn still returns success
- [ ] Tests (`tests/engine/test_turn_metadata_producer.py`, ~6 cases):
  - Read / Write / Edit tool_use blocks populate `recent_files`
  - Bash / Grep / non-file tools NOT in `recent_files`
  - Successful tool_result populates `verified_work` (capped 10)
  - Error tool_result excluded from `verified_work`
  - Dedupe across multiple Read of same path
  - `task_focus_state` always has goal=None / next_step=None
- [ ] Tests (`tests/engine/test_session_memory_engine_wiring.py`, ~4 cases):
  - `session_memory_path` set + end_turn → file written with
    expected slots populated
  - `session_memory_path` None → no write attempt
  - OSError during write → logged warning, turn still emits
    `ConversationCompleteEvent`
  - Tool_use turn (stop_reason="tool_use") → NOT written
    (writer fires only at user-turn end)

**Files**:
- `src/openharness/engine/messages.py` (additive: 1 new function)
- `src/openharness/engine/query.py` (additive: 1 block in finally)
- `tests/engine/test_turn_metadata_producer.py` (new)
- `tests/engine/test_session_memory_engine_wiring.py` (new)

**Sub-units**:
- 1a — `collect_turn_metadata` helper + tests
- 1b — Engine call site + session_memory write + tests
- 1c — Verify L3 compact now actually reads non-None checkpoints
  (integration test bridging Phase 11 T7-7b)

---

### P12-T2: `services/snapshot.py` — writer + loader + staleness

**Description**: New module mirroring `services/session_memory.py`
in shape (path resolver + writer + reader + render helper) but
emitting JSON v1 per D30.2. Same atomic-write pattern. Adds the
staleness-check primitives that the CLI's `--resume` will surface.

**Acceptance**:
- [ ] `src/openharness/services/snapshot.py`:
  - `get_snapshot_dir(cwd) -> Path` — mirrors session_memory layout
    at `~/.openharness/snapshots/<basename>-<sha1(cwd)[:12]>/`
  - `write_session_snapshot(*, cwd, tool_metadata, messages,
    context) -> Path` — atomic JSON write to `current.json`
  - `load_snapshot(cwd, *, snapshot_id=None) -> dict` — load
    `current.json` (or matching `<git-head>.json` if Phase 13 adds
    history); raises `SnapshotError` subclass on failure
  - `_serialize_snapshot(tool_metadata, messages, context) -> dict`
    — pure function building the JSON structure
  - `_current_git_head(cwd) -> str | None` — `subprocess.run(['git',
    'rev-parse', 'HEAD'])` with 1s timeout; None on any failure
    (not a git repo / git not installed / detached / timeout)
- [ ] JSON schema (locked verbatim in code as the v1 reference):
  ```json
  {
    "version": 1,
    "schema": "openharness.snapshot.v1",
    "created_at": "...",
    "git_head": "..." or null,
    "cwd": "...",
    "model": "...",
    "permission_mode": "...",
    "system_prompt": "...",
    "max_tokens": 1024,
    "messages": [...],
    "tool_metadata": {...},
    "extra": {}
  }
  ```
- [ ] Constants `SNAPSHOT_VERSION = 1`, `SNAPSHOT_SCHEMA = "openharness.snapshot.v1"`
- [ ] `SnapshotError` + `SnapshotStaleness` exception hierarchy:
  - `SnapshotError` base
  - `SnapshotNotFound` (no `current.json` for cwd)
  - `SnapshotCwdMismatch` (snapshot cwd ≠ current cwd → REFUSE)
  - `SnapshotVersionMismatch` (snapshot version > supported → REFUSE)
  - `SnapshotGitHeadDrift` (NOT raised — surfaced via return + log)
- [ ] `load_snapshot` returns dict + emits structured log events for
  each staleness signal hit (caller decides how to surface to user)
- [ ] `messages` field round-trips byte-identical: serialized via
  `model_dump(mode="json")` on each `ConversationMessage`; parsed
  back via `ConversationMessage.model_validate` (pydantic round-trip
  contract)
- [ ] Tests (`tests/services/test_snapshot.py`, ~14 cases):
  - `get_snapshot_dir` mirrors session_memory layout (same hash)
  - `_current_git_head` returns short SHA in a git repo
  - `_current_git_head` returns None when no `.git`
  - `_current_git_head` returns None on timeout (slow-git stub)
  - `write_session_snapshot` produces well-formed JSON
  - Round-trip: write → load returns equivalent dict
  - Messages round-trip byte-identical (tool_use / tool_result
    / image blocks all preserved)
  - `load_snapshot` raises `SnapshotNotFound` when no file
  - `load_snapshot` raises `SnapshotCwdMismatch` when cwd differs
  - `load_snapshot` raises `SnapshotVersionMismatch` when v2
  - `load_snapshot` succeeds + warn-logs on git HEAD drift
  - `load_snapshot` succeeds + info-logs on version downgrade
    (snapshot v0 < supported v1 — forward-compat path)
  - Atomic write: write + concurrent read sees old version, not partial
  - Disabled (passed via context.snapshot_enabled=False) → caller
    short-circuits before calling writer

**Files**:
- `src/openharness/services/snapshot.py` (new, ~250 lines)
- `src/openharness/services/__init__.py` (re-export
  `write_session_snapshot`, `load_snapshot`, error classes)
- `tests/services/test_snapshot.py` (new)

**Sub-units**:
- 2a — Path resolver + `_current_git_head` + tests
- 2b — `write_session_snapshot` + JSON round-trip + tests
- 2c — `load_snapshot` + 3-staleness branches + tests
- 2d — Error hierarchy + `__init__.py` re-exports

---

### P12-T3: Engine call-site for snapshot writer + `SnapshotSettings`

**Description**: Wires T2's writer into the same engine finally block
T1 just wired the session_memory writer into. Single
`collect_turn_metadata` call feeds both writers (D30.6's single-
producer design). Adds the nested settings model (env-only knobs;
no CLI flag for write-enabled per D30.8).

**Acceptance**:
- [ ] `src/openharness/config/settings.py`:
  - `SnapshotSettings(BaseModel)`:
    - `enabled: bool = True`
    - `max_age_warn_days: int = 7`
  - `Settings.snapshot: SnapshotSettings`
- [ ] Env nested overrides via existing `env_nested_delimiter="__"`:
  - `OPENHARNESS_SNAPSHOT__ENABLED=false`
  - `OPENHARNESS_SNAPSHOT__MAX_AGE_WARN_DAYS=30`
- [ ] `src/openharness/engine/context.py`:
  - `QueryContext.snapshot_enabled: bool = False`
  - `QueryContext.snapshot_max_age_warn_days: int = 7`
  (Defaults False here so unit-test code paths that build
  `QueryContext` directly aren't surprised; cli.py opts in from
  settings.)
- [ ] `src/openharness/engine/query.py` finally block (extending T1):
  ```python
  if context.snapshot_enabled:
      try:
          write_session_snapshot(
              cwd=context.cwd,
              tool_metadata=tool_metadata,  # reused from T1
              messages=final_messages,
              context=context,
          )
      except OSError as exc:
          logger.warning("snapshot_write_failed", error=str(exc))
  ```
- [ ] `src/openharness/cli.py::_run_ask` + `_run_chat` (mirrored):
  - Pass `snapshot_enabled=settings.snapshot.enabled` into QueryContext
- [ ] Tests (`tests/config/test_snapshot_settings.py`, ~5 cases):
  - Defaults match D30.8
  - Env nested override works
  - CLI default propagates through QueryContext
- [ ] Tests (`tests/engine/test_snapshot_engine_wiring.py`, ~5 cases):
  - `snapshot_enabled=True` + end_turn → JSON file written
  - `snapshot_enabled=False` → no write attempt
  - OSError logged + turn still emits `ConversationCompleteEvent`
  - Tool_use turn → no write
  - Single `collect_turn_metadata` call per turn shared with
    session_memory writer (assert via spy)

**Files**:
- `src/openharness/config/settings.py` (additive nested model)
- `src/openharness/engine/context.py` (additive 2 fields)
- `src/openharness/engine/query.py` (additive 1 block)
- `src/openharness/cli.py` (additive 2 lines in each runner)
- `tests/config/test_snapshot_settings.py` (new)
- `tests/engine/test_snapshot_engine_wiring.py` (new)

**Sub-units**:
- 3a — `SnapshotSettings` + nested env tests
- 3b — `QueryContext.snapshot_enabled` field + bootstrap wiring
- 3c — Engine call site + tests

---

### P12-T4: `QueryContext.from_snapshot` factory + helper

**Description**: D30.7's split — load agent-state from snapshot,
caller passes runtime-state. Returns `(QueryContext, messages)`
tuple. CLI consumers build the QueryContext like normal (fresh
registries / hooks / execution_env) and then override agent-state
fields from the snapshot via this factory.

**Acceptance**:
- [ ] `src/openharness/engine/context.py::QueryContext.from_snapshot`:
  ```python
  @classmethod
  def from_snapshot(
      cls,
      snapshot: dict,
      *,
      api_client: SupportsStreamingMessages,
      tool_registry: ToolRegistry,
      permission_checker: PermissionChecker,
      hook_registry: HookRegistry,
      execution_env: ExecutionEnvironment,
      cwd: Path,
      # ... other runtime-only kwargs
  ) -> tuple[QueryContext, list[ConversationMessage]]:
      ...
  ```
- [ ] Loads from snapshot: `model`, `max_tokens`, `permission_mode`,
  `system_prompt`, `messages`, `tool_metadata` reference for next-turn
  metadata accumulation
- [ ] Caller-required: `api_client`, `tool_registry`,
  `permission_checker`, `hook_registry`, `execution_env`,
  `skill_store`, `memory_store`, `session_memory_path`,
  `snapshot_enabled`, etc. (all runtime/Phase-11-introduced fields)
- [ ] Messages parsed via `ConversationMessage.model_validate`
  (pydantic round-trip)
- [ ] Tests (`tests/engine/test_from_snapshot.py`, ~6 cases):
  - Round-trip: snapshot dict → `from_snapshot` → run_query
    sees expected messages
  - System prompt loaded verbatim (not re-rendered)
  - permission_mode enum value loaded
  - model + max_tokens loaded
  - tool_use / tool_result blocks in messages parse correctly
  - Missing required runtime kwargs → TypeError (caller's bug)

**Files**:
- `src/openharness/engine/context.py` (additive classmethod)
- `tests/engine/test_from_snapshot.py` (new)

**Sub-units**:
- 4a — Factory signature + agent-state loading + tests
- 4b — Messages round-trip via pydantic + tests

---

### P12-T5: CLI `--resume` flag + load + banner

**Description**: User-visible surface. Mirrored on `oh ask` and
`oh chat`. Opt-in (default OFF — current `oh ask` semantics
preserved). Supports both implicit (latest snapshot for cwd) and
explicit `<git-head-prefix>` selection.

**Acceptance**:
- [ ] `src/openharness/cli.py::ask`:
  - `--resume` / `--no-resume` (bool flag, default None → use
    settings; explicit `--no-resume` overrides settings)
  - `--resume <id>` accepts optional git-HEAD-prefix argument via
    `--resume-id` separate typer option (avoid bool ambiguity)
  - On resume: load snapshot → `QueryContext.from_snapshot` →
    append new user prompt → `run_query`
- [ ] `src/openharness/cli.py::chat`:
  - Same `--resume` / `--no-resume` / `--resume-id` mirror
  - Print banner on resume: `(resumed: 23 messages from
    2026-05-26 14:32; git_head=d3beb40)`
  - Resume + no input → enter REPL waiting for first user message
- [ ] No snapshot for cwd + `--resume` → warn + start fresh
  (don't error)
- [ ] `--resume-id` with no match → error + list available IDs
- [ ] `--resume-id` with ambiguous prefix → error + list matches
- [ ] Staleness warnings (git HEAD drift) printed to stderr
- [ ] Tests (`tests/cli/test_resume.py`, ~12 cases):
  - `oh ask --resume "follow-up"` loads snapshot + appends prompt
  - `oh ask "fresh"` (no flag) ignores snapshot
  - `oh ask --no-resume "fresh"` explicit ignore overrides env
  - `oh ask --resume-id <bad>` errors with available list
  - `oh ask --resume-id <prefix>` matches correctly
  - `oh ask --resume` with no snapshot warns + starts fresh
  - `oh chat --resume` prints banner + loads
  - `oh chat --resume --no-resume` rejected (mutually exclusive)
  - git HEAD drift warning surfaces to stderr
  - cwd mismatch (synthesized snapshot from another path) →
    refused with error
  - Snapshot v999 → refused with version error
  - resumed conversation persists tool_metadata across snapshot
    reload (recent_files accumulate)

**Files**:
- `src/openharness/cli.py` (additive: 3 flags on ask + chat,
  helper for load + banner)
- `tests/cli/test_resume.py` (new)

**Sub-units**:
- 5a — `--resume` / `--no-resume` flags on ask + chat
- 5b — `--resume-id` flag + prefix matching
- 5c — Snapshot load helper + banner
- 5d — Staleness warning surface + error paths

---

### P12-T6: E2E + cross-cutting invariant + retro

**Description**: Wraps Phase 12. End-to-end integration test proves
the resume loop works: ask → snapshot persisted → resume → next
turn sees prior context. Compact L3 integration test (closes Phase
11 retro §4 "no L3 hit observed yet" gap). Invariant verification
across 10+ protected layers. Phase 12 retro.

**Acceptance**:
- [ ] Integration test (`tests/services/test_e2e_resume.py`):
  - Turn 1: `oh ask "set x=42"` (stubbed LLM, end_turn) → snapshot
    written to expected path
  - Turn 2: `oh ask --resume "what is x?"` (fresh subprocess) →
    captured LLM request includes turn 1's messages + new prompt
- [ ] Integration test (`tests/services/test_e2e_phase11.py` extend
  OR `tests/services/test_compact_l3_integration.py` new):
  - Force compact L3 path: write a fresh session_memory checkpoint
    + many short messages just over threshold → `auto_compact_if_needed`
    returns `compact_kind == "session_memory"`, no LLM call
  - Bridges Phase 11 retro §4.2 gap (L3 had no producer in Phase 11)
- [ ] Integration test (`tests/cli/test_resume_chat_e2e.py`):
  - `oh chat --resume`: stub input sequence → REPL loads snapshot
    + prints banner + accepts next message
- [ ] **Cross-cutting invariant verification** (run + document in retro):
  - `git log <Phase 11 close d3beb40^>..HEAD -- src/openharness/markdown_store/` → 0
  - Same for skills / commands / bundles / plugins / mcp /
    permissions / prompts / protocols / memory / hooks
  - `git diff` for `engine/query.py` shows only additive per-turn-
    end finally block + `from_snapshot` factory in `engine/context.py`
  - `git diff` for `services/session_memory.py` shows public API
    byte-identical (Phase 11 contract preserved)
- [ ] `learnings/phase-12.md` retro:
  - 1. Data points table (commits / tests / LoC / time vs Phase 11)
  - 2. Per-task takeaway (T1-T6 one-liners)
  - 3. ⭐ Invariant verification result — 10 protected dirs zero
    diff (6th compounding test of substrate pattern: snapshot reuses
    session_memory's path-resolver + tempfile pattern verbatim)
  - 4. Conceptual lesson: did single-producer / two-consumer
    (`collect_turn_metadata`) stay primitive-only without
    "if writer ==" branches?
  - 5. Real踩坑 (predict 3-4):
    - `model_dump(mode="json")` round-trip subtleties on
      `ToolResultBlock` (mixed content types)
    - git subprocess timeout / not-installed cases on CI
    - `from_snapshot` field-by-field maintenance burden surfaces
    - snapshot vs session_memory checkpoint divergence after
      Phase 13 changes one but not the other
  - 6. Phase 13 predictions: snapshot rotation / `history/` /
    `oh memory add` / LLM-authored task_focus_state
- [ ] All Phase 11 tests still pass (1789 → expect ~1840+ with
  Phase 12 additions)
- [ ] mypy strict + ruff clean
- [ ] Coverage ≥ 95% (gate retained)

**Files**:
- `tests/services/test_e2e_resume.py` (new)
- `tests/services/test_compact_l3_integration.py` (new — OR
  appended to `test_e2e_phase11.py`)
- `tests/cli/test_resume_chat_e2e.py` (new)
- `learnings/phase-12.md` (new)

**Sub-units**:
- 6a — E2E resume round trip
- 6b — Compact L3 actually-hits integration (Phase 11 debt
  verification)
- 6c — Chat resume e2e
- 6d — Invariant git-diff verification + commit-msg attestation
- 6e — `learnings/phase-12.md` retro
- 6f — DoD closeout + Phase 13 prediction recording

---

## Checkpoints

After each capability: **human review** of the diff + test pass +
zero-diff verification against the 10 protected dirs. Two critical
checkpoints:

- **T1 close** (Phase 11 debt fold-in): `collect_turn_metadata` is
  the load-bearing new helper. Verify it correctly accumulates
  across turns when resumed (`prior_metadata` arg). If T1 ships
  but L3 compact still reads None, Phase 11 debt is not actually
  closed — stop and re-debug.
- **T6 close** (E2E + invariant): the 6th compounding test of the
  "design substrate at Nth consumer" pattern. `services/snapshot.py`
  + `services/session_memory.py` should look like siblings — same
  path-resolver + atomic-write shape. If they diverge structurally,
  **stop and check whether the snapshot abstraction needs to fold
  into session_memory's**.

The review-before-commit walkthrough applies per usual.

## Risks

| Risk | Mitigation |
|---|---|
| `model_dump(mode="json")` doesn't round-trip `ToolResultBlock` cleanly (mixed content types: text / image / structured) | Write a round-trip test FIRST (T2-2b); if it fails, fix at the protocol layer (pydantic config); fall back to custom serializer if pydantic can't handle |
| `git rev-parse HEAD` subprocess slow / hangs (CI variability) | 1s timeout per call; cache per `run_query` invocation; None on failure (snapshot just has null git_head) |
| `from_snapshot` field-by-field maintenance as `QueryContext` grows | Currently 14 fields; factory adds maybe 6 explicit assignments. Snapshot doesn't load runtime-only fields. Phase 13 refactor candidate if field count > 25 |
| Snapshot writer + session_memory writer race on same `tool_metadata` reference | Both writers receive the SAME computed dict (D30.6); writers don't mutate the dict. Verified by spy test in T3 |
| Resume loads stale system_prompt referencing now-removed skills | D30.2 contract: stored verbatim. Agent's reasoning depends on what it saw. Stale > silent re-render |
| Concurrent `oh ask --resume` race | `os.replace` atomicity per filesystem (D30.3); last writer wins; rare scenario worth accepting |
| Test isolation: snapshot dir leaks across tests via HOME | Conftest's HOME-isolation fixture already covers (Phase 10 established); snapshot dir falls under same root |
| ``--resume-id`` prefix matching ambiguity (two snapshots with same SHA prefix) | Error with both IDs listed; require longer prefix |
| Snapshot JSON growth unbounded over long conversations | Same growth as session_memory checkpoint (no harder problem); rotation deferred to Phase 13 `history/` |

## Risks specifically NOT mitigated (Phase 13+)

- **Snapshot rotation** — `current.json` is single-file overwrite;
  `history/` reserved but unpopulated
- **Snapshot encryption at rest** — plaintext JSON includes
  conversation contents
- **Cross-machine resume** — manual scp / rsync until Phase 14
  `oh snapshot export / import`
- **Auto-resume offer** — Phase 12 ships `--resume` explicit only
- **LLM-authored `task_focus_state`** — Phase 12 leaves None; Phase
  13 evaluates secondary LLM call
- **Snapshot diff viewer** — `oh snapshot diff <id1> <id2>`
- **Tool-state persistence** — sub-agent state / hook accumulator
  state not in snapshot

## Pointers

- Boundary: [`decisions/27-phase-12-boundary.md`](../decisions/27-phase-12-boundary.md)
- Phase 11 boundary (services/ substrate Phase 12 builds on):
  [`decisions/26-phase-11-boundary.md`](../decisions/26-phase-11-boundary.md)
- Phase 11 retro (T5 session_memory writer debt + T7 §5 Phase 12
  predictions):
  [`learnings/phase-11.md`](../learnings/phase-11.md)
- Phase 10 boundary (cwd-hashed user-global directory pattern
  Phase 12 reuses):
  [`decisions/25-phase-10-boundary.md`](../decisions/25-phase-10-boundary.md)
