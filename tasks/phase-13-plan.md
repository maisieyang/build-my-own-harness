# Phase 13 Implementation Plan — Snapshot Rotation + `oh snapshot` CLI + LLM-authored `task_focus_state`

> Boundary contract: [`decisions/28-phase-13-boundary.md`](../decisions/28-phase-13-boundary.md).
> Extends Phase 12's snapshot infrastructure (writer / loader /
> from_snapshot / --resume) with rotation + introspection + opt-in
> LLM enrichment. Phase 13 is largely additive — the heaviest single
> change is the rotation atomicity dance inside
> ``services/snapshot.py``.

## Overview

**Phase 13 goal**: turn the per-cwd snapshot from a single
``current.json`` into a **rotated history of up to N=100 / 90 days**,
expose ``oh snapshot list / show / gc`` for user-side introspection,
and ship LLM-authored ``task_focus_state`` as an **opt-in** that
reuses Phase 11's ``services/summarize.py`` substrate (becoming its
7th consumer).

The **cross-cutting invariant** (7th compounding test of the
abstraction-first pattern from Phase 7c retro §3.1):

- `markdown_store / skills / commands / bundles / plugins / mcp /
  permissions / prompts / protocols / memory / hooks` — zero diff
  (Phase 12 hit 11/11 — Phase 13 must hold this)
- `services/summarize.py` — zero diff (LLM focus-state becomes
  the 7th consumer without modifying the primitive)
- `services/snapshot.py` public API (``write_session_snapshot`` /
  ``load_snapshot`` / ``get_snapshot_dir`` + error classes) — byte-
  identical signatures; only internal additions

Only ``engine/messages.py`` (one optional kwarg), ``engine/query.py``
(one new helper + one finally-block branch), ``config/settings.py``
(one nested model field set), ``cli.py`` (one new sub-app + 2
flags), and ``services/snapshot.py`` + new ``services/focus_state.py``
get modified.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/28-phase-13-boundary.md`](../decisions/28-phase-13-boundary.md) | D31.1 scope = rotation + ``oh snapshot`` family + opt-in LLM focus-state; D31.2 count(100) + age(90 days) rotation policy; D31.3 eager trigger every snapshot write; D31.4 history/ name = ``<git_head>-<YYYYMMDDhhmmss>.json``; D31.5 hardlink with copy fallback for atomicity; D31.6 ``oh snapshot list/show/gc`` typer sub-app; D31.7 LLM focus-state opt-in via env + CLI; D31.8 no schema bump (``version: 1`` stays) |

---

## Task list

### P13-T1: Snapshot rotation + atomicity 🔜 NEXT

**Description**: Extend ``services/snapshot.py`` so every per-turn
write rotates the existing ``current.json`` into ``history/`` first,
then GCs entries exceeding count/age thresholds. Use hardlink for
atomicity vs concurrent readers, fall back to copy on unsupported
filesystems.

**Acceptance**:

- [ ] ``SnapshotHistorySettings`` nested model under
  ``SnapshotSettings``:
  - ``max_count: int = 100`` (ge=0; 0 disables history)
  - ``max_age_days: int = 90`` (ge=0; 0 disables age-based GC)
- [ ] Env vars: ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT`` +
  ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_AGE_DAYS``
- [ ] ``services/snapshot.py``:
  - ``_compute_history_name(snapshot_dict) -> str`` — pure
    function building ``<git_head>-<YYYYMMDDhhmmss>.json``;
    ``nogit`` literal when git_head missing
  - ``_rotate_current_to_history(snapshot_dir, max_count,
    max_age_days)`` — rotation step that runs AFTER the new
    current.json is in place
  - ``_gc_history(history_dir, max_count, max_age_days)`` —
    drops oldest entries exceeding either threshold; returns
    count of dropped paths
  - ``write_session_snapshot`` extended: between the new-current
    write and return, fires rotation if old current.json existed
    (the function reads ``current.json`` BEFORE writing the new
    one to know there's something to rotate)
- [ ] Atomicity: ``os.link`` for hardlink first; on
  ``OSError`` / ``NotImplementedError`` fall back to atomic
  copy (read + write_atomic to history/)
- [ ] Collision handling: when target history/ path exists, append
  ``-1`` ``-2`` etc. up to ``-99`` then raise
- [ ] Rotation failures (history GC errors / link OSError after
  copy fallback also fails) WARN-logged but don't block the
  per-turn snapshot write — same failure isolation as Phase 12
- [ ] Phase 12 backward compat: pre-existing ``current.json`` from
  Phase 12 (with git_head + created_at fields) rotates cleanly
  on first Phase 13 write
- [ ] Tests (``tests/services/test_snapshot_rotation.py``, ~12 cases):
  - First Phase 13 write on empty dir: no rotation, current.json
    created (sanity)
  - Second write: old current.json moves to history/, new
    current.json lands; both readable
  - Count threshold: 101 sequential writes → history/ has 100
    entries
  - Age threshold: synthesize history/ entries with old mtimes
    → next write drops the aged ones
  - Both thresholds at once: oldest-first dropping
  - Collision suffix: same git_head + same iso-second produces
    ``-1`` suffix
  - Hardlink fallback: monkeypatch ``os.link`` to raise → copy
    path executes
  - Phase 12 backward compat: write a Phase-12-shape snapshot
    by hand, then Phase 13 write rotates it
  - Concurrent reader during rotation: spawn a thread doing
    ``load_snapshot`` while rotation runs; reader always gets a
    valid snapshot
  - GC failure isolation: monkeypatch ``unlink`` to raise → rotation
    logs warning + new current.json still landed
  - Empty history (max_count=0): nothing is kept in history/
  - Default ``SnapshotHistorySettings`` matches D31.2 (100 / 90)

**Files**:

- `src/openharness/config/settings.py` (additive nested model)
- `src/openharness/services/snapshot.py` (additive helpers + extend
  write_session_snapshot)
- `tests/services/test_snapshot_rotation.py` (new)
- `tests/config/test_snapshot_settings.py` (extend with history
  settings cases)

**Sub-units**:

- 1a — `SnapshotHistorySettings` + env vars + tests
- 1b — `_compute_history_name` + `_gc_history` pure helpers + tests
- 1c — Rotation integration into `write_session_snapshot` + tests
- 1d — Hardlink/copy atomicity + concurrent-reader test
- 1e — Phase 12 backward compat + GC failure isolation

---

### P13-T2: `oh snapshot list / show / gc` CLI subcommand family

**Description**: New typer sub-app mirroring Phase 10's
``oh memory list / show / path``. Three subcommands for user-side
introspection + explicit cleanup. ``list`` is the discoverability
entry; ``show`` is the inspection tool; ``gc`` is the manual
rotation trigger for users who want to force cleanup outside the
per-turn eager path.

**Acceptance**:

- [ ] ``oh snapshot list`` (text default):
  - Columns: ID / CREATED / MESSAGES / GIT_HEAD / AGE
  - Sorted: ``current`` first, then history/ entries newest-first
  - Empty case: prints "No snapshots for cwd: <path>"
- [ ] ``oh snapshot list --format json``:
  - Array of objects with fields ``id`` / ``created_at`` /
    ``message_count`` / ``git_head`` / ``path``
- [ ] ``oh snapshot show <id>`` accepts:
  - ``current`` literal — renders the live current.json
  - git_head prefix — matches any history entry's git_head
    (ambiguous → error with list of matches; not found → exit 1)
- [ ] ``oh snapshot show <id>`` (text default):
  - Header: ``model`` / ``created_at`` / ``git_head`` /
    ``message_count``
  - Truncated system_prompt (first 240 chars + ellipsis if longer)
  - Numbered message one-liners (reuse session_memory's
    one-liner formatter from Phase 11)
- [ ] ``oh snapshot show <id> --format json`` prints the raw
  snapshot dict (the on-disk JSON, no transformation)
- [ ] ``oh snapshot gc``:
  - Reads current settings (max_count + max_age_days)
  - Calls ``_gc_history`` directly (without writing a new
    current.json)
  - Prints "Dropped N snapshot(s) from history/"
- [ ] ``oh snapshot gc --dry-run``:
  - Lists what WOULD be dropped without doing it
  - Same exit code 0 either way
- [ ] Tests (``tests/cli/test_snapshot_subcommands.py``, ~14 cases):
  - list with no snapshots
  - list with current + 0 history
  - list with current + 3 history (sort order)
  - list --format json shape
  - show current
  - show <prefix> match
  - show <prefix> ambiguous error
  - show <prefix> not found exit 1
  - show --format json round-trips with on-disk file
  - gc with nothing to drop
  - gc dropping by count
  - gc dropping by age
  - gc --dry-run reports without dropping
  - --help mentions all 3 subcommands

**Files**:

- `src/openharness/cli.py` (additive: snapshot sub-app + 3
  subcommand functions + render helpers)
- `tests/cli/test_snapshot_subcommands.py` (new)

**Sub-units**:

- 2a — `oh snapshot list` + tests
- 2b — `oh snapshot show <id>` + prefix matching + tests
- 2c — `oh snapshot gc` + --dry-run + tests
- 2d — `--help` discoverability tests

---

### P13-T3: LLM-authored `task_focus_state` (opt-in, 7th summarize consumer)

**Description**: Add a thin ``services/focus_state.py`` module that
calls ``services.summarize.summarize()`` with a focus-state prompt
and parses JSON ``{"goal", "next_step"}``. Engine optionally awaits
the call at turn end (after extract, before snapshot write) when
the opt-in flag is set; the authored values populate
``tool_metadata.task_focus_state`` in place of the Phase 12 None
placeholder.

**Acceptance**:

- [ ] ``services/focus_state.py``:
  - ``FocusState`` frozen dataclass: ``goal: str | None``,
    ``next_step: str | None``
  - ``FOCUS_STATE_SYSTEM_PROMPT`` constant — 3-sentence prompt
    asking for ``{"goal": "...", "next_step": "..."}`` JSON
  - ``async infer_focus_state(*, messages, prior_focus_state,
    api_client, model, timeout_seconds=15.0) -> FocusState``
  - JSON parse failure / timeout / any exception → log warning
    + return ``FocusState(goal=None, next_step=None)``
  - Uses ``services.summarize.summarize()`` (no modification —
    7th consumer)
- [ ] ``services/__init__.py`` re-exports ``infer_focus_state`` +
  ``FocusState``
- [ ] ``config/settings.py``:
  - ``SnapshotSettings.llm_focus_state: bool = False``
  - ``SnapshotSettings.focus_state_model: str | None = None``
    (None → use main convo model)
  - Env vars: ``OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE`` +
    ``OPENHARNESS_SNAPSHOT__FOCUS_STATE_MODEL``
- [ ] ``engine/context.py::QueryContext``:
  - ``llm_focus_state_enabled: bool = False`` field
  - ``focus_state_model: str | None = None`` field
- [ ] ``engine/messages.py::collect_turn_metadata`` accepts
  optional ``authored_focus_state: FocusState | None = None`` kwarg
  — when provided, overrides the None placeholder dict
- [ ] ``engine/query.py`` per-turn-end finally block:
  - If ``context.llm_focus_state_enabled``: await
    ``infer_focus_state(...)`` BEFORE ``_maybe_write_turn_end_metadata``
  - Pass result through to ``_maybe_write_turn_end_metadata`` so
    both session_memory writer + snapshot writer see authored
    focus state
- [ ] CLI: ``--llm-focus-state`` / ``--no-llm-focus-state`` on
  ``oh ask`` + ``oh chat`` (mirror per Phase 11 / 12 pattern)
- [ ] Tests (``tests/services/test_focus_state.py``, ~8 cases):
  - Happy path: stub returns valid JSON → FocusState populated
  - Malformed JSON → FocusState(None, None) + warning logged
  - Timeout → FocusState(None, None) + warning logged
  - Stub raises exception → FocusState(None, None) + warning logged
  - Prior focus state passed through to prompt
  - Empty messages → skip LLM call entirely, return None+None
  - Prompt template invariants (FOCUS_STATE_SYSTEM_PROMPT
    mentions JSON + ``goal`` + ``next_step``)
- [ ] Tests (``tests/engine/test_focus_state_engine_wiring.py``,
  ~5 cases):
  - llm_focus_state_enabled=True + valid LLM stub → snapshot's
    task_focus_state populated with authored values
  - llm_focus_state_enabled=False → snapshot has Phase 12 None
    placeholder
  - LLM failure → snapshot's task_focus_state has None placeholder
    (failure isolation)
  - Focus state computed ONCE per turn (spy: not called twice
    when both session_memory writer + snapshot writer fire)
  - Prior focus state from previous turn's metadata flows in
    (collect_turn_metadata seed → infer_focus_state arg)

**Files**:

- `src/openharness/services/focus_state.py` (new, ~120 lines)
- `src/openharness/services/__init__.py` (additive re-export)
- `src/openharness/config/settings.py` (additive 2 fields under
  SnapshotSettings)
- `src/openharness/engine/context.py` (additive 2 fields)
- `src/openharness/engine/messages.py` (additive 1 kwarg on
  collect_turn_metadata)
- `src/openharness/engine/query.py` (additive 1 helper + finally
  block branch)
- `src/openharness/cli.py` (additive: 1 flag pair × 2 commands +
  settings plumb)
- `tests/services/test_focus_state.py` (new)
- `tests/engine/test_focus_state_engine_wiring.py` (new)

**Sub-units**:

- 3a — `services/focus_state.py` + prompt + summarize integration + tests
- 3b — `SnapshotSettings` extension (llm_focus_state +
  focus_state_model) + tests
- 3c — Engine wiring + collect_turn_metadata kwarg + tests
- 3d — CLI flag plumbing + tests

---

### P13-T4: E2E + cross-cutting invariant + retro

**Description**: Three E2E loops + the standard invariant verification
across 11 protected directories + Phase 13 retro.

**Acceptance**:

- [ ] Integration test (``tests/services/test_e2e_rotation.py``):
  - Run 5 sequential snapshot writes through the engine in the
    same cwd → assert history/ has 4 entries + current.json points
    to most recent
- [ ] Integration test (``tests/cli/test_snapshot_subcommand_e2e.py``):
  - Write a snapshot via ``oh ask`` → ``oh snapshot list`` shows
    it → ``oh snapshot show current`` renders it → ``oh snapshot
    gc --dry-run`` reports nothing (under threshold)
- [ ] Integration test (``tests/services/test_e2e_focus_state.py``):
  - Stub LLM returns valid JSON focus state → engine writes
    snapshot whose ``tool_metadata.task_focus_state`` is the
    authored values → ``oh snapshot show <id>`` renders the
    focus state in the header
- [ ] **Cross-cutting invariant verification**:
  - ``git log Phase-12-close-c1e57c9^..HEAD -- src/openharness/markdown_store/`` → 0
  - Same for skills / commands / bundles / plugins / mcp /
    permissions / prompts / protocols / memory / hooks (11 total)
  - ``services/summarize.py`` zero removed lines (7th consumer
    didn't force modification)
  - ``services/snapshot.py`` public API signatures unchanged
    (write_session_snapshot / load_snapshot / get_snapshot_dir
    / SnapshotError subclasses)
- [ ] ``learnings/phase-13.md`` retro:
  - 1. Data points table (commits / tests / LoC / time vs Phase 11+12)
  - 2. Per-task takeaway (T1-T4) with commit hashes
  - 3. ⭐ Invariant verification result — 7th compounding test of
    substrate-first pattern (``services/summarize.py`` reused by
    7th consumer ``focus_state.py`` without modification)
  - 4. Conceptual lesson: rotation atomicity dance (hardlink +
    copy fallback) — did it stay confined to one function
    despite multiple failure modes?
  - 5. Real踩坑 predictions (3-4): hardlink semantics on macOS
    vs Linux (atomic guarantees), git_head prefix matching across
    current.json + history/, LLM focus-state JSON parse edge cases
  - 6. Phase 14 predictions: cross-machine resume export/import,
    ``oh memory add`` CLI, auto-dream subprocess
- [ ] All Phase 12 tests still pass (1897 → expect 1897 + Phase 13
  additions)
- [ ] mypy strict + ruff clean
- [ ] Coverage ≥ 95% (gate retained)

**Files**:

- `tests/services/test_e2e_rotation.py` (new)
- `tests/cli/test_snapshot_subcommand_e2e.py` (new)
- `tests/services/test_e2e_focus_state.py` (new)
- `learnings/phase-13.md` (new)

**Sub-units**:

- 4a — Rotation E2E (5 sequential writes)
- 4b — CLI subcommand E2E (ask → list → show → gc)
- 4c — Focus-state E2E (stubbed LLM → snapshot → show)
- 4d — Invariant verification + commit-msg attestation
- 4e — `learnings/phase-13.md` retro
- 4f — DoD closeout + Phase 14 predictions recording

---

## Checkpoints

After each capability: **human review** of the diff + test pass +
zero-diff verification against the 11 protected dirs. Two critical
checkpoints:

- **T1 close** (rotation atomicity): the
  ``_rotate_current_to_history`` helper is the load-bearing new
  code. If concurrent-reader-during-rotation test ever flakes,
  STOP and revisit the hardlink ordering. Atomicity is a
  contract — flaky test means broken invariant.
- **T4 close** (E2E + invariant): the 7th compounding test of the
  "design substrate at Nth consumer" pattern. If ``services/summarize.py``
  needed even one line modified to support focus-state, **stop and
  re-open D31.7** — the substrate's responsibility surface is wrong.

The review-before-commit walkthrough applies per usual.
Phase 12 retro §3.4 升级 review checklist:

| Pre-commit review checklist |
|---|
| diff content vs acceptance criteria |
| staged file list vs this capability's expected file set |
| any pre-existing untracked files getting swept up? |

## Risks

| Risk | Mitigation |
|---|---|
| Hardlink fails on macOS APFS cross-volume / FAT / Windows | Copy fallback path tested; rotation never throws (caught + WARN-logged) |
| Rotation race vs concurrent ``oh ask --resume`` reader | Sequence: write new current.json BEFORE moving old to history. Reader always sees a complete file. Tested with a concurrent stub reader |
| history/ name collision (same git_head + same wall-clock second) | ``-<n>`` numeric suffix; test pins behavior |
| ``oh snapshot show`` output for a 100-turn snapshot is huge | One-liner format reused from session_memory render (Phase 11 T2) — already 80-char capped |
| LLM-authored focus state adds latency when enabled | Opt-in; user accepts cost. 15s timeout default; failure → None fallback. Default OFF preserves Phase 12 zero-cost |
| LLM focus-state JSON parse failure | Same tolerance as extract: log warning + return None+None; doesn't fail turn |
| Engine + focus-state await order matters | Order: extract → focus-state (if enabled) → snapshot write (uses focus-state in tool_metadata). Each step independently caught |
| Phase 12 snapshots without history/ dir get rotation surprised | history/ created on first rotation if missing; tests cover |
| ``oh snapshot gc`` runs while engine is writing | gc reads + deletes from history/; engine writes current.json + moves old. Two distinct dirs touched → no race possible |
| Default 100 / 90 too aggressive for power users | Both env-configurable; ``oh snapshot list`` shows entries to inform tuning |
| ``services/summarize.py`` needs modification to support focus state | DO NOT modify — if substrate insufficient, redesign focus_state.py to wrap differently. The 7th-consumer invariant is the load-bearing claim |

## Risks specifically NOT mitigated (Phase 14+)

- **Cross-machine resume export/import** (``oh snapshot export``
  + ``oh snapshot import``)
- **Snapshot encryption at rest**
- **Per-project rotation policy override** (global only in Phase 13)
- **LLM-authored ``verified_work`` / ``recent_files`` enrichment**
- **Auto-dream background subprocess**
- **``oh memory add`` explicit write CLI**

## Pointers

- Boundary: [`decisions/28-phase-13-boundary.md`](../decisions/28-phase-13-boundary.md)
- Phase 12 boundary (snapshot infrastructure Phase 13 extends):
  [`decisions/27-phase-12-boundary.md`](../decisions/27-phase-12-boundary.md)
- Phase 12 retro (§5 Phase 13 predictions; §3.3 prior_metadata
  unused-but-kept; §3.4 review checklist 升级):
  [`learnings/phase-12.md`](../learnings/phase-12.md)
- Phase 11 ``services/summarize.py`` (substrate the LLM-authored
  focus-state becomes 7th consumer of):
  [`decisions/26-phase-11-boundary.md`](../decisions/26-phase-11-boundary.md)
- Phase 10 ``oh memory list / show / path`` (CLI subcommand pattern
  ``oh snapshot`` mirrors):
  [`decisions/25-phase-10-boundary.md`](../decisions/25-phase-10-boundary.md)
