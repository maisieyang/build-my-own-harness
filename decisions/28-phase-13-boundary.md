# Phase 13 Boundary — Snapshot Rotation + `oh snapshot` CLI + LLM-authored `task_focus_state`

> Status: locked at Phase 13 entry, 2026-05-28.
>
> Scope note: Phase 13 turns the Phase 12 snapshot from a single
> ``current.json`` per cwd into a **rotated history** of snapshots,
> exposes a user-visible ``oh snapshot`` subcommand family (list /
> show / gc), and evaluates the deferred decision from Phase 12 retro
> §5.2 — whether to populate ``tool_metadata.task_focus_state`` via
> a secondary LLM call. The LLM-authored variant is shipped as
> **opt-in**; the existing zero-cost placeholder stays default.
>
> Phase 12 reserved ``history/`` under each snapshot dir but never
> populated it (D30.2 plan, NOT acceptance). Phase 13 makes
> ``history/`` real: every per-turn snapshot write now rotates the
> existing ``current.json`` into ``history/<key>.json`` first.
>
> Related work references:
>
> - **Phase 12 D30.2** reserved ``history/`` directory in the
>   snapshot layout. Phase 13 ships the rotation policy + writer
>   atomicity that fills it.
> - **Phase 12 retro §5** predicted Phase 13 as snapshot rotation +
>   ``oh snapshot list`` + LLM-authored ``task_focus_state``. This
>   boundary doc realizes that prediction verbatim.
> - **Phase 12 retro §3.3** flagged the ``prior_metadata`` param on
>   ``collect_turn_metadata`` as "designed-but-unused" — Phase 13
>   decides whether to delete it or wire it up. (Decision: keep it,
>   may get exercised by Phase 13's LLM focus-state flow.)
> - **Phase 11 ``services/summarize.py``** becomes the 7th
>   consumer's substrate when LLM-authored focus-state opts in. No
>   modification expected (D31.7 contract pinned by invariant T4-7d).
> - **Phase 10 D28.10** established the cwd-hashed user-global
>   directory pattern; Phase 13 reuses for the ``history/`` subdir
>   without modifying the algorithm.
> - **Git reflog** is the conceptual model for rotation policy:
>   keep the last N entries OR everything within M days, whichever
>   arm hits first.

## Triggering observation

Phase 12 leaves three loose ends:

1. **No history**: ``current.json`` is overwritten every turn. A
   user wanting to resume a session from 3 days ago has no way to
   recover it — the snapshot they need was overwritten on turn 247
   of the current session. Phase 12 explicitly deferred rotation;
   Phase 13 enables it.
2. **No introspection**: ``oh ask --resume`` is a black-box load.
   The user can't see "what snapshots exist" before deciding which
   to load. ``oh snapshot list`` answers "which sessions are
   resumable for this cwd, and how stale?"
3. **task_focus_state is structurally hollow**: Phase 12 ships the
   schema field with ``{"goal": None, "next_step": None}``
   placeholder. Phase 12 retro §3.3 deferred LLM-authored
   evaluation. Phase 13 evaluates by **shipping it opt-in** — users
   who care about structured goal capture can enable; default
   stays the zero-cost placeholder.

All three rest on the same ``services/snapshot.py`` + engine
``_maybe_write_turn_end_metadata`` infrastructure Phase 12 built.
Phase 13 is largely additive — the most invasive single change is
the rotation atomicity (hardlink + atomic-replace dance), which
lives inside ``services/snapshot.py``.

---

## In scope

### D31.1 — Phase 13 scope: rotation + CLI + opt-in LLM focus-state

Phase 13 ships **three features + Phase 12 retro debt closure**:

- **Rotation**: history/ directory becomes populated; each per-turn
  snapshot write rotates current.json → history/<key>.json before
  the new current.json lands.
- **CLI**: ``oh snapshot`` typer sub-app with ``list`` /
  ``show <id>`` / ``gc`` subcommands.
- **LLM-authored task_focus_state**: opt-in flag
  (``OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE`` env +
  ``--llm-focus-state`` CLI) — uses ``services/summarize.py`` as the
  7th consumer.
- **Phase 12 retro debt**: evaluate ``prior_metadata`` param on
  ``collect_turn_metadata``. Decision: KEEP (will likely be wired
  by LLM focus-state's prior-turn continuation logic). Document
  in retro.

**Out**: ``oh memory add`` explicit CLI (Phase 14 — extraction +
snapshot together cover most cases); cross-machine snapshot
export/import (Phase 14 — user can scp manually); auto-dream
background subprocess (Phase 14 — cross-process daemon
complexity); snapshot encryption at rest (no demand demonstrated).

### D31.2 — Rotation policy: count + age, whichever-first

Both arms enforced together, conservative side wins:

```python
class SnapshotHistorySettings(BaseModel):
    max_count: int = 100        # keep last 100 snapshots
    max_age_days: int = 90      # drop anything older than 90 days
```

Rotation runs **after** the new ``current.json`` is written + the
old one moved into ``history/``. Drops entries from history/ that
exceed EITHER arm. Drops oldest-first within history/.

**Why both**: pure count is too generous for a long-running project
(an active project hits 100 turns in a day); pure age is too
aggressive for a sparse one (a personal project may use 1 turn /
week — age would erase everything). Both arms is the conservative
default matching git reflog's UX.

**Env-only knobs** (no CLI flag — rotation is operational, not
per-invocation):

- ``OPENHARNESS_SNAPSHOT__HISTORY_MAX_COUNT=100``
- ``OPENHARNESS_SNAPSHOT__HISTORY_MAX_AGE_DAYS=90``

### D31.3 — Rotation trigger: eager, every snapshot write

Rotation fires inline with every per-turn snapshot write:

```
1. Read existing current.json (if any) → stash for hardlink target
2. Write new current.json (atomic tempfile + os.replace per Phase 12)
3. If stash present: move stash → history/<key>.json
4. GC history/: drop entries exceeding D31.2 thresholds
```

Steps 2 + 3 + 4 are independent failures-isolated (per Phase 12's
contract: snapshot write must not block the turn). GC failure is
logged but doesn't abort.

**Why eager not background**:

- Per-turn cost: dirent + 2 stat() + 1 unlink for the oldest dropped
  entry. <10ms.
- No daemon process to manage cross-OS.
- Lazy (``oh snapshot gc`` only) would let history/ grow unbounded
  for users who never run gc — bad default.

``oh snapshot gc`` (D31.6) is still available for explicit
force-cleanup outside the per-turn path.

### D31.4 — history/ entry naming

Locked format:

```
history/<git_head>-<created_iso_short>.json
```

Where:

- ``<git_head>`` is the snapshot's ``git_head`` field (7-char SHA)
  or ``nogit`` literal when not in a git repo
- ``<created_iso_short>`` is the snapshot's ``created_at`` field
  rendered as ``YYYYMMDDhhmmss`` (no separators — filesystem-safe
  + sortable + unique enough)

Examples:

- ``history/abc1234-20260528143012.json``
- ``history/nogit-20260528144530.json``

**Collision handling**: if the candidate name already exists
(extremely rare — same git_head + same wall-clock second),
append ``-<n>`` (`-1`, `-2`, ...). Test pins behavior; not
expected in practice.

**Why git_head + timestamp instead of just one**:

- Just git_head: collisions when 2 snapshots taken at same commit
  (very common — 10 turns within one commit)
- Just timestamp: 2-second-window collisions on chatty agents
- Both: collisions only with same-second + same-commit

### D31.5 — Rotation atomicity: hardlink with copy fallback

Step 3 from D31.3 (moving stash → history/<key>.json) needs to be
atomic vs concurrent ``load_snapshot`` reading current.json.
Phase 12's ``os.replace`` is atomic within a single dir for a
SINGLE file. Phase 13 needs to atomically:

1. Preserve old current.json in history/
2. Make new current.json live

Without atomicity, a concurrent reader could see neither (file
disappeared mid-rename) — broken.

**Algorithm**:

```python
# Before any rename:
old_current = read_text(current.json)  # snapshot to memory

# 1. Write new current.json (Phase 12 unchanged):
write_atomic(new_current_path, new_content)  # tempfile + os.replace

# 2. Move old → history/ — try hardlink first (atomic), fall back to copy:
if old_current is not None:
    history_path = history_dir / _compute_history_name(old_metadata)
    try:
        os.link(_saved_old_current_temp_path, history_path)  # atomic
    except (OSError, NotImplementedError):
        # FAT, Windows mount, or otherwise: fall back to write_atomic
        write_atomic(history_path, old_current)

# 3. GC history/ per D31.2 thresholds:
_gc_history(history_dir, max_count, max_age_days)
```

The key trick: **step 1 happens before step 2**, so a concurrent
reader of current.json always sees either old or new — never
missing. The "save old to temp then hardlink" preserves the old
content without ever leaving current.json missing.

**Hardlink failure modes**: cross-device link (history/ on
different filesystem from current.json — shouldn't happen since
both under same cwd-hash dir, but defensive), FAT/Windows
mounts. Fall back to write_atomic of the buffered content.

### D31.6 — CLI: `oh snapshot list / show / gc` subcommand family

Mirror Phase 10's ``oh memory list / show / path`` pattern. New
sub-app:

```
oh snapshot list                           # tabular list of snapshots
oh snapshot list --format json             # machine-readable
oh snapshot show <id>                      # render a single snapshot
oh snapshot show <id> --format json        # raw JSON
oh snapshot gc                             # force rotation (drops oldest)
oh snapshot gc --dry-run                   # report what would be dropped
```

``<id>`` accepts:

- ``current`` literal — the live current.json
- ``<git_head_prefix>`` — matches any history entry's git_head
  (ambiguous prefix → error with the list of matches; not found →
  exit 1)

**``list`` columns** (text format):

```
ID         CREATED              MESSAGES  GIT_HEAD  AGE
current    2026-05-28 14:30:12  47        abc1234   2 min ago
abc1234-…  2026-05-28 13:00:05  31        abc1234   1.5h ago
nogit-…    2026-05-27 18:00:00  12        (no git)  20h ago
```

**``show``** (text format): print the snapshot's `system_prompt`
(truncated), `model`, `created_at`, `git_head`, `message_count`,
then a numbered list of message one-liners (first 80 chars per
message — same shape as Phase 11's session_memory render).

**``gc``**: force-runs rotation per D31.2 thresholds. Returns
``exit 0`` + reports how many were dropped. Distinct from per-turn
eager rotation in that ``gc`` can run when no new snapshot was
written (user explicitly cleaning up).

### D31.7 — LLM-authored task_focus_state opt-in (7th summarize consumer)

When opted in:

```python
@dataclass(frozen=True)
class FocusState:
    goal: str | None
    next_step: str | None
```

Engine flow per turn end (after extract, parallel to snapshot write):

```python
if context.llm_focus_state_enabled:
    focus = await _maybe_authored_focus_state(
        context, final_messages, prior_focus_state
    )
    tool_metadata["task_focus_state"] = focus or {"goal": None, "next_step": None}
else:
    tool_metadata["task_focus_state"] = {"goal": None, "next_step": None}
```

Calls ``services.summarize.summarize()`` with a focus-state prompt
that asks for JSON `{"goal": "...", "next_step": "..."}` based on
the turn's content + prior focus state. JSON parse failure /
timeout / any error → log warning + fall back to None.

**Opt-in surfaces**:

- ``OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE=true`` env
- ``--llm-focus-state`` / ``--no-llm-focus-state`` on ``oh ask`` + ``oh chat``
- Default OFF — preserves Phase 12 zero-cost behavior

**Model choice**: caller passes via existing
``settings.extraction.model`` field default (None → use main convo
model); add new ``settings.snapshot.focus_state_model`` if Phase 13
T3 evaluation shows main-model cost is unacceptable.

**Failure isolation**: the focus-state LLM call NEVER blocks turn
completion. ``await``-ed with 15s timeout; failure → log warning
+ snapshot writes with None placeholder.

### D31.8 — Backward compat for Phase 12 snapshots

Phase 12 snapshots have ``version: 1`` and may exist on disk when
Phase 13 lands. Two compat concerns:

1. **Loading Phase 12 snapshots**: ``version: 1`` still readable by
   ``load_snapshot`` (Phase 12 contract). ``task_focus_state`` is
   already in the schema (placeholder dict). LLM-authored values
   populate the SAME field — no schema bump.
2. **Rotating Phase 12 snapshots**: when Phase 13 rotation first
   runs on a project with a pre-Phase-13 ``current.json``, the
   rotation moves it to ``history/`` using D31.4 naming. The
   pre-existing snapshot's git_head + created_at fields supply
   the name components — Phase 12 already wrote both.

No version bump needed. Phase 12's ``version: 1`` schema is
unchanged; Phase 13 only adds:

- Rotation behavior (no schema impact)
- `oh snapshot` subcommand (no schema impact)
- LLM-authored ``task_focus_state`` content (no schema impact —
  field was already there as placeholder dict)

**Schema bump deferred to**: Phase 14+ if structural fields are
added (e.g. tool registry snapshot, hook state). Phase 13 keeps
v1.

---

## Out of scope (Phase 14+)

- **``oh memory add``** explicit write CLI (extraction + snapshot
  cover most cases; revisit if usage data shows demand).
- **Auto-dream subprocess** — periodic background consolidation
  + cross-session dedup. Cross-process daemon complexity.
- **Cross-machine snapshot export/import** (``oh snapshot export
  <id>`` + ``oh snapshot import <file>``). User can scp manually
  for now.
- **Snapshot encryption at rest** — plaintext JSON includes user
  prompts + LLM responses; if multi-user shared HOME, leak risk.
  Mitigation requires user-key management — defer.
- **Snapshot diff viewer** (``oh snapshot diff <id1> <id2>``) —
  tooling concern, Phase 14+.
- **LLM-authored ``verified_work`` enrichment** — Phase 13 only
  evaluates ``task_focus_state``. The other slots (recent_files,
  verified_work) stay static-heuristic per D30.6.
- **Plug-in rotation policies** — let users author their own
  rotation criteria. No demand yet.
- **Rotation policy per cwd** — Phase 13 uses global settings;
  per-project override would be a Phase 14+ feature.

---

## Critical decisions (D31.x)

| ID | Decision | Why |
|---|---|---|
| **D31.1** | Phase 13 scope = rotation + ``oh snapshot`` family + opt-in LLM focus-state; ``oh memory add`` / auto-dream / encryption deferred | Three features fit one phase; auto-dream's daemon needs separate scope |
| **D31.2** | Rotation policy = count(100) + age(90 days), whichever arm hits first; both env-configurable | Matches git reflog UX; pure-count too generous, pure-age too aggressive |
| **D31.3** | Rotation trigger = eager (every snapshot write checks + rotates); ``oh snapshot gc`` available for explicit force-cleanup | Lazy would let history/ grow unbounded for users who never gc |
| **D31.4** | history/ entry name = ``<git_head>-<YYYYMMDDhhmmss>.json``; ``nogit`` literal when no git repo; ``-<n>`` suffix on collision | git_head alone collides (10 turns per commit); timestamp alone collides on chatty agents |
| **D31.5** | Rotation atomicity: hardlink with write_atomic fallback for cross-device / FAT / Windows | Hardlink is atomic; concurrent reader of current.json always sees a complete file |
| **D31.6** | CLI = ``oh snapshot list / show <id> / gc`` typer sub-app | Mirrors Phase 10 ``oh memory list/show/path`` pattern |
| **D31.7** | LLM-authored ``task_focus_state`` = opt-in (env + CLI flag); summarize substrate's 7th consumer; failure isolated to None fallback | Default OFF preserves Phase 12 zero-cost; opt-in lets researchers / debug-heavy users enable |
| **D31.8** | No schema bump: Phase 12 ``version: 1`` snapshots load + rotate cleanly; LLM focus-state populates the existing placeholder field | Schema-stable extension is cheaper than v1→v2 migration |

---

## Dependency direction

```
services/snapshot.py                    ← extend with rotation +
                                         _gc_history helper +
                                         backward-compat for Phase 12
                                         current.json on first rotation

services/focus_state.py                 ← NEW: thin wrapper around
                                         summarize() for LLM-authored
                                         task_focus_state (D31.7);
                                         pure function, no engine import

engine/messages.py                      ← extend collect_turn_metadata
                                         to accept optional
                                         authored_focus_state arg
                                         (overrides None placeholder)

engine/query.py                         ← per-turn-end finally block:
                                         optionally await focus-state
                                         LLM call before
                                         _maybe_write_turn_end_metadata

config/settings.py                      ← extend SnapshotSettings with
                                         history_max_count +
                                         history_max_age_days +
                                         llm_focus_state +
                                         focus_state_model

cli.py                                  ← +--llm-focus-state /
                                         --no-llm-focus-state on
                                         ask + chat
                                         +oh snapshot sub-app
                                         (3 subcommands)

prompts/                                ← ZERO DIFF (snapshot show
                                         renders messages but uses
                                         ad-hoc one-liner formatting,
                                         not build_system_prompt)
markdown_store/                         ← ZERO DIFF
skills/ commands/ bundles/ plugins/     ← ZERO DIFF
mcp/ permissions/ protocols/            ← ZERO DIFF
memory/                                 ← ZERO DIFF
hooks/                                  ← ZERO DIFF
```

11 protected directories. ``engine/messages.py`` gets one optional
kwarg (additive); ``engine/query.py`` gets one new helper +
finally-block branch (additive). Everything else extends inside
``services/`` (additive new file + additive helpers in existing
file) or ``cli.py`` (new sub-app + new flag).

---

## Sub-decisions deferred to build

Three open questions resolved tentatively now, locked at build time:

- **Rotation timing relative to extraction**: should rotation run
  BEFORE or AFTER ``_maybe_extract_memories``? Tentative: **AFTER
  extract** so extract has the full pre-rotation history to scan
  via ``has_memory_writes_since``. (extract reads ``messages``,
  not snapshot files, so this is mostly a code-locality concern.)
- **LLM focus-state prompt template**: 2-3 sentences asking for
  ``{"goal", "next_step"}`` JSON, given turn content + optional
  prior focus state. Tentative: **3-sentence prompt** with prior
  focus state in the user message, return JSON only. Pin
  verbatim in T3 boundary.
- **``oh snapshot show`` token cost when message bodies are large**:
  show prints message one-liners — but a 10k-message-body tool_result
  printed at 80 chars gives 80-char string. Tentative: **render
  same shape as session_memory ``_message_oneliner`` (Phase 11
  T2)**. Reuse existing tested function rather than duplicate.

---

## Acceptance for Phase 13 close-out (template)

### Rotation (D31.2 + D31.3 + D31.4 + D31.5)

- [ ] ``services/snapshot.py`` ``write_session_snapshot`` extends
  with rotation step
- [ ] New helper ``_gc_history(history_dir, max_count, max_age_days)``
  drops entries exceeding either threshold
- [ ] Rotation atomicity: hardlink first, copy fallback on OSError
- [ ] history/ entry naming per D31.4 (``<git_head>-<iso_short>.json``,
  ``nogit`` literal, ``-<n>`` on collision)
- [ ] ``SnapshotHistorySettings`` nested under ``SnapshotSettings``
  with ``max_count=100`` + ``max_age_days=90`` defaults
- [ ] Env vars: ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT`` +
  ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_AGE_DAYS``
- [ ] Coverage: ~10 unit tests (rotation triggers / count threshold
  / age threshold / collision suffix / hardlink fallback / Phase 12
  backward compat / gc-only when needed / concurrent reader during
  rotation)

### CLI subcommand family (D31.6)

- [ ] ``oh snapshot list`` text + ``--format json`` output
- [ ] ``oh snapshot show <id>`` for ``current`` literal + git_head
  prefix matching
- [ ] ``oh snapshot show <id>`` ambiguous → error with match list
- [ ] ``oh snapshot show <id>`` not-found → exit 1
- [ ] ``oh snapshot gc`` returns count of dropped entries
- [ ] ``oh snapshot gc --dry-run`` reports without dropping
- [ ] Coverage: ~12 integration tests covering all 3 subcommands +
  happy + error paths

### LLM-authored task_focus_state (D31.7)

- [ ] ``services/focus_state.py::infer_focus_state(messages,
  prior_focus_state, summarize_kwargs) -> FocusState``
- [ ] Uses ``services.summarize.summarize()`` (no modification)
- [ ] JSON output parser tolerates malformed → return None+None
- [ ] 15s timeout default; failure → log warning + return None+None
- [ ] CLI ``--llm-focus-state`` / ``--no-llm-focus-state`` on ask + chat
- [ ] Env ``OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE`` toggle
- [ ] Engine wires authored state into ``tool_metadata.task_focus_state``
  ONLY when enabled (default OFF preserves Phase 12 behavior)
- [ ] Coverage: ~8 tests (enabled writes authored state / disabled
  preserves None / parse failure falls back / timeout falls back /
  prior_focus_state carries through / opt-in plumbing through CLI)

### Phase 12 backward compat (D31.8)

- [ ] Existing ``version: 1`` ``current.json`` (no LLM focus state)
  loads via Phase 12's ``load_snapshot`` unchanged
- [ ] First Phase 13 rotation on a Phase-12-written project moves
  the existing current.json to history/ with the proper name
- [ ] No schema bump anywhere; ``SNAPSHOT_VERSION`` stays 1

### Cross-cutting invariant verification

- [ ] ``git log Phase-12-close..HEAD -- src/openharness/markdown_store/`` → 0
- [ ] Same for skills / commands / bundles / plugins / mcp /
  permissions / prompts / protocols / memory / hooks
- [ ] ``git diff Phase-12-close..HEAD -- src/openharness/services/snapshot.py``
  shows public API (``write_session_snapshot`` / ``load_snapshot`` /
  ``get_snapshot_dir`` / SNAPSHOT_VERSION / SNAPSHOT_SCHEMA / error
  classes) byte-identical signature — only internal additions
- [ ] ``git diff`` for ``engine/messages.py`` shows
  ``collect_turn_metadata`` signature additive only
- [ ] ``git diff`` for ``engine/query.py`` shows only one new helper
  + one finally-block branch
- [ ] ``learnings/phase-13.md`` retro per project convention

### Phase 13 close-out gates

- [ ] All Phase 12 tests still pass (1897 → expect 1897 + Phase 13
  additions)
- [ ] ``ruff check src tests`` clean; ``mypy src`` strict clean
- [ ] Coverage ≥ 95% (gate)
- [ ] ``services/__init__.py`` re-exports the new ``focus_state``
  function

---

## Risks

| Risk | Mitigation |
|---|---|
| Hardlink fails on cross-device / FAT / Windows mounts | Copy fallback path tested; rotation never throws (caught + WARN-logged) |
| Rotation race vs concurrent ``oh ask --resume`` reader | Sequence: write new current.json BEFORE moving old → reader always sees a complete file. Tested with a stub reader during rotation |
| history/ name collision (same git_head + same wall-clock second) | ``-<n>`` numeric suffix; test pins behavior; <1-in-1000 in practice |
| ``oh snapshot show`` output dump for a 100-turn snapshot is huge | One-liner format per message (80 chars cap, reused from session_memory render); paginated through stdout's default flow |
| LLM-authored focus-state adds ~1-2s per turn when enabled | Opt-in only; user accepts cost. Failure-isolated to None placeholder so no UX regression even on enabled path |
| LLM focus-state prompt produces malformed JSON | Same parser tolerance as extract: JSON parse fail → log warning + return None+None; doesn't fail turn |
| Backward compat: Phase 12 snapshots without ``history/`` dir get rotation surprised | history/ created on first rotation if missing; Phase 12 cwds with only current.json + no history/ work identically to Phase 13 cwds |
| ``oh snapshot gc`` runs while engine is writing | gc reads + deletes from history/; engine writes current.json + moves old. Two distinct dirs touched → no race possible |
| max_count=100 + max_age_days=90 defaults too aggressive for power users | Both env-configurable; ``oh snapshot list`` shows entries to inform tuning |
| Engine + focus-state await order matters for failure isolation | Order: extract → focus-state (if enabled) → snapshot-write (uses focus-state in tool_metadata). Each step independently caught |

## Risks specifically NOT mitigated (Phase 14+)

- **history/ rotation across machine restarts** — if user closes
  laptop mid-rotation, snapshots may be left in inconsistent state.
  Filesystem-level atomic operations cover most cases; full crash
  recovery would need a journal.
- **Snapshot encryption at rest** — plaintext JSON.
- **Per-project rotation policy override** — global only in
  Phase 13.
- **LLM-authored verified_work / recent_files enrichment** —
  Phase 13 only evaluates ``task_focus_state``.
- **Snapshot rotation respecting "active" sessions** — Phase 13
  rotates blindly by count/age; a snapshot you're actively
  resuming could in theory get gc'd. In practice resume reads
  current.json (newest), so this is theoretical.

## Pointers

- Plan: [`tasks/phase-13-plan.md`](../tasks/phase-13-plan.md)
- Phase 12 boundary (snapshot infrastructure Phase 13 extends):
  [`decisions/27-phase-12-boundary.md`](./27-phase-12-boundary.md)
- Phase 12 retro (§5 Phase 13 predictions; §3.3 prior_metadata
  unused-but-kept):
  [`learnings/phase-12.md`](../learnings/phase-12.md)
- Phase 11 ``services/summarize.py`` (substrate the LLM-authored
  focus-state becomes the 7th consumer of):
  [`decisions/26-phase-11-boundary.md`](./26-phase-11-boundary.md)
- Phase 10 ``oh memory list / show / path`` (the CLI subcommand
  pattern ``oh snapshot`` mirrors):
  [`decisions/25-phase-10-boundary.md`](./25-phase-10-boundary.md)
