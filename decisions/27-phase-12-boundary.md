# Phase 12 Boundary — Session Snapshot + Resume

> Status: locked at Phase 12 entry, 2026-05-26.
>
> Scope note: Phase 12 turns Phase 11's session_memory checkpoint
> (which currently only L3 compact ever reads) into a **user-
> visible resume feature**. The agent's run state — messages,
> tool_metadata, system_prompt, model config — gets persisted as
> a structured snapshot every turn. `oh ask --resume` and
> `oh chat --resume` reload that snapshot as the starting
> conversation history, so a session that hit Ctrl+C / context-
> folded / closed the laptop can pick up where it left off.
>
> Phase 12 also lands Phase 11's remaining debt:
> **engine-side session_memory writer** — Phase 11 added
> ``QueryContext.session_memory_path`` but never actually wrote
> the checkpoint, so L3 reads currently always see None. That
> wiring lands here because the writer's data source
> (``tool_metadata``) is tightly coupled to what Phase 12's
> snapshot also needs (recent files / verified work / current
> goal). One source-of-truth for both.
>
> Related work references:
>
> - **Upstream HKUDS/OpenHarness §17** has the snapshot JSON
>   format + resume CLI. Phase 12 reimplements the read-side
>   contract (snapshot schema + load semantics) independently;
>   the JSON shape per D30.2 is informed by what HKUDS uses but
>   not byte-copied (HKUDS's schema includes upstream-specific
>   fields like ``model_provider_id`` we don't have).
> - **Phase 11 D29.4** designed the 5-slot markdown checkpoint
>   as L3 read-target; Phase 12 keeps that machinery and adds a
>   parallel JSON snapshot for resume.
> - **Phase 11 retro §5.1** flagged ``QueryContext.from_snapshot``
>   factory + git-HEAD staleness check + snapshot format v1
>   sentinel — Phase 12 acts on all three.
> - **Phase 10 D28.10** established the cwd-hashed user-global
>   directory pattern; Phase 12 reuses for snapshot storage
>   without modifying the directory algorithm.
> - **Claude Code's session resume** is the user-visible model;
>   we ship the same trigger surface (cwd-aware default + explicit
>   ID) without copying internals.

## Triggering observation

Three failure modes Phase 11 cannot recover from:

1. **Context-window overflow mid-session**: L4 compact compresses
   the prompt but the compressed summary IS the history going
   forward — fidelity is permanently degraded.
2. **Process death** (Ctrl+C / OOM / `oh ask` exits after one
   turn): the conversation is gone. Each `oh ask` invocation
   currently starts from zero, which is correct for one-shots
   but wasteful when the user wanted to continue.
3. **Cross-machine handoff**: same project on laptop A, then
   desktop B — no way to carry session state across.

The fix in all three cases is the same primitive: **persist
enough of the run state to a per-cwd file at every turn end,
load it on resume**. Phase 11 already persists the 5-slot
markdown checkpoint for L3 reuse; Phase 12 extends this to a
full snapshot that ``QueryContext.from_snapshot`` can rebuild
from.

Phase 11's ``tool_metadata`` design also lands here: the engine
needs to know "what files did the agent touch this turn" to
populate both the 5-slot checkpoint AND the snapshot. Phase 11
designed the data structure (``recent_files`` / ``verified_work``
/ ``task_focus_state``) but punted on the producer. Phase 12 adds
a deterministic engine-side producer (static heuristic scan of
``Read`` / ``Write`` / ``Edit`` tool_use blocks for files; LLM-
authored ``task_focus_state`` deferred to Phase 13).

---

## In scope

### D30.1 — Phase 12 scope: resume + snapshot only

Phase 12 ships **two features** + folds in one debt item:

- ``oh ask --resume`` and ``oh chat --resume`` — load snapshot
  for current cwd as initial messages
- Snapshot writer in the engine (per-turn end, atomic) — JSON
  format per D30.2, stored next to session_memory checkpoint
- Engine-side session_memory writer (Phase 11 debt; same call
  site as snapshot writer; shares ``tool_metadata`` producer)

**Out**: ``oh memory add`` explicit CLI (extraction makes the
case weaker — Phase 13 evaluates); auto-dream background
consolidation (cross-process complexity too large; Phase 14+);
plug-in snapshot formats (no demand yet).

### D30.2 — Snapshot format: JSON v1, parallel to the markdown checkpoint

Snapshot lives at:

```
~/.openharness/snapshots/<basename>-<sha1(cwd)[:12]>/
├── current.json           ← the resume target (overwritten each turn)
└── history/               ← (Phase 13+) rotated older snapshots
```

(``snapshots/`` directory; NOT the ``session-memory/`` dir per
D29.4. session_memory checkpoint stays at its own path so L3
compact's reader keeps working without lookup changes.)

JSON schema:

```jsonc
{
  "version": 1,                          // sentinel for parser dispatch
  "schema": "openharness.snapshot.v1",
  "created_at": "2026-05-26T18:30:00Z",
  "git_head": "d3beb40",                 // null if not in git repo
  "cwd": "/Users/user/project",          // absolute, for staleness check
  "model": "qwen-plus",
  "permission_mode": "default",
  "system_prompt": "...",                // FULL string, not re-rendered
  "max_tokens": 1024,
  "messages": [
    {
      "role": "user",
      "content": [{"type": "text", "text": "hi"}]
    },
    {
      "role": "assistant",
      "content": [...]
    }
  ],
  "tool_metadata": {
    "recent_files": ["src/x.py", "src/y.py"],
    "verified_work": [],
    "task_focus_state": {"goal": null, "next_step": null}
  },
  "extra": {                              // reserved for Phase 13+
    "hook_state": null,
    "session_memory_path": "..."          // cross-reference
  }
}
```

**Why JSON not markdown**: ``messages`` is structurally typed
(roles / content blocks / tool_use / tool_result); rendering to
markdown loses fidelity (round-trip not lossless). The 5-slot
markdown checkpoint stays separate — it serves L3 compact + human
inspection; snapshot serves resume + machine load.

**Why ``version`` + ``schema`` both**: ``version`` is the bump
when fields change; ``schema`` is the namespaced identifier so
future tools (extraction, auto-dream) producing JSON files in
the same dir don't get confused with snapshots.

**``git_head`` field**: present iff cwd is inside a git working
tree. Used for **staleness warning at resume time** (D30.5).

**``system_prompt`` stored verbatim**: not re-rendered from
current registry / skills / memory at resume time. Reason: the
agent's existing reasoning chain depends on the EXACT prompt it
saw; re-rendering after the user added/removed skills would
change context mid-conversation. Stale system_prompt is the
correct default; user can ``/clear`` + ``--no-resume`` for fresh.

### D30.3 — Snapshot trigger: every user turn end (atomic write)

The same engine call site that writes the session_memory checkpoint
(Phase 11 T5 design) writes the snapshot. Per turn end:

1. Build ``tool_metadata`` from the just-completed turn's messages
2. ``update_session_memory_file(cwd, tool_metadata, messages)``
3. ``write_session_snapshot(cwd, tool_metadata, messages, context)``

Both writes are ``tempfile + os.replace`` atomic; L3 compact reads
the markdown checkpoint, ``--resume`` reads the JSON snapshot — they
don't interfere.

**Frequency**: every user turn end. Not on tool-use turns (intermediate
state mid-loop), not on every event (too noisy). Matches the
session_memory contract D29.4.

**Cost**: ~5ms per turn (JSON serialize + tempfile write + os.replace).
Negligible vs the LLM call. The turn-end finally block already does
extraction (~2-3s when enabled) so an extra 5ms is invisible.

### D30.4 — Resume CLI surface

```
oh ask --resume "next question"        # implicit: load latest snapshot for cwd
oh ask --resume <snapshot-id>          # explicit: load named snapshot
oh chat --resume                        # multi-turn resume
oh chat --no-resume                    # ignore snapshot, start fresh
```

For Phase 12, ``<snapshot-id>`` is the ``git_head`` short SHA from
the snapshot itself (e.g. ``oh ask --resume d3beb40 "..."``). If
multiple snapshots match the prefix, error with the list. If no
snapshot exists for cwd, warn + start fresh (don't error).

``oh chat --resume`` loads the snapshot AND prints a one-liner
banner: ``(resumed: 23 messages from 2026-05-26 14:32; git_head=d3beb40)``.

**``--resume`` is opt-in, default OFF**: ``oh ask "..."`` without
``--resume`` ignores snapshots entirely (preserves Phase 11
semantics). Reason: most ``oh ask`` invocations are one-shots; auto-
resume would surprise users who closed a long session yesterday
and started a fresh question today.

For Phase 13: ``oh ask`` (no flag) checks snapshot age — if <5
min old, offer "found recent snapshot — pass --resume to continue,
or ignore". Phase 12 doesn't add the prompt; auto-detect is
behavior change worth its own decision cycle.

### D30.5 — Staleness detection at resume time

Three staleness signals checked when loading a snapshot:

1. **Git HEAD drift**: snapshot's ``git_head`` ≠ current HEAD →
   warn (``WARN: snapshot was taken at d3beb40 but HEAD is now
   a1b2c3d. File contents the agent saw may be stale.``)
   Doesn't refuse to load; the user decides.
2. **Cwd mismatch**: snapshot's ``cwd`` ≠ current cwd → refuse
   to load. Reason: same hash collision would let snapshots
   cross-pollute projects; load with cwd mismatch is silent
   data corruption.
3. **Version mismatch**: snapshot's ``version`` > parser's
   supported version → refuse. ``version`` < supported → load
   (forward-compat) with migration logger event.

The warning surfaces on stderr; the conversation still loads.
Phase 13 may add a ``--strict-staleness`` flag to refuse on
HEAD drift.

### D30.6 — Engine-side ``tool_metadata`` producer (static heuristic)

The engine maintains a per-turn ``tool_metadata`` dict during
``run_query``:

```python
@dataclass
class TurnMetadata:
    recent_files: list[str]      # paths touched by Read/Write/Edit this turn
    verified_work: list[str]     # tool_result content snippets (truncated)
    task_focus_state: dict[str, Any]  # placeholder dict; None goal/next_step
```

Population rules (deterministic, no LLM):

- ``recent_files``: scan ``ToolUseBlock`` for tools named
  ``Read`` / ``Write`` / ``Edit``; extract ``path`` / ``file_path``
  input field. Dedupe within turn.
- ``verified_work``: for each tool_use that produced a successful
  (non-error) tool_result, append ``"{tool_name}: {first 60 chars of result}"``.
  Cap at 10 most recent per turn.
- ``task_focus_state.goal`` / ``next_step``: leave None.
  Phase 13 evaluates whether LLM-authored goal extraction is
  worth the secondary-LLM-call cost. Phase 12 ships with the
  fields present (so snapshot schema is stable) but unpopulated.

This metadata is what flows into BOTH the session_memory checkpoint
AND the snapshot. Two consumers, one producer.

### D30.7 — ``QueryContext.from_snapshot`` factory

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
    skill_store: SkillStore | None = None,
    memory_store: object | None = None,
    # ... other "must-rebuild-fresh" fields
) -> tuple[QueryContext, list[ConversationMessage]]:
    ...
```

Returns ``(context, messages)``: context for the next turn,
messages as the resume history (which the CLI appends to before
calling ``run_query``).

**What loads from snapshot**: ``model`` / ``max_tokens`` /
``permission_mode`` / ``system_prompt`` / ``messages`` /
``tool_metadata`` cross-reference.

**What does NOT load** (caller must construct fresh): ``api_client``
(may need new auth) / ``tool_registry`` (may have new tools
since snapshot) / ``hook_registry`` (hook state isn't persisted
in v1) / ``execution_env`` (sandbox / host changes per invocation) /
``skill_store`` / ``memory_store`` / ``permission_checker``.

The split matches HKUDS upstream's resume implementation: persist
the agent-state (what the LLM saw), reconstruct the harness-state
(what the runtime provides).

### D30.8 — ``SnapshotSettings`` nested in ``Settings``

```python
class SnapshotSettings(BaseModel):
    enabled: bool = Field(True, description="Write per-turn snapshot")
    max_age_warn_days: int = Field(7, description="Warn when resuming snapshot older than N days")
```

Env vars via existing ``__`` delimiter:

- ``OPENHARNESS_SNAPSHOT__ENABLED=false`` — opt out of writing
- ``OPENHARNESS_SNAPSHOT__MAX_AGE_WARN_DAYS=30`` — tune warn threshold

No CLI flag for ``enabled`` because writing is invisible cost
(~5ms/turn) and there's no observable failure mode if disabled
(snapshots just don't accumulate). ``--no-resume`` covers the
read-side opt-out.

### D30.9 — Phase 11 debt that lands here

The engine-side **session_memory writer wiring**: Phase 11 added
``QueryContext.session_memory_path`` and the helper
``update_session_memory_file(cwd, tool_metadata, messages)`` but
the engine never calls the helper. Phase 12 connects both:

```python
# engine/query.py per-turn end (after extract, before yield ConversationCompleteEvent):
if context.session_memory_path is not None:
    tool_metadata = _collect_turn_metadata(messages)  # D30.6 producer
    try:
        update_session_memory_file(
            context.cwd, tool_metadata, messages
        )
    except OSError as exc:
        logger.warning("session_memory_write_failed", error=str(exc))

if context.snapshot_enabled:  # D30.8 setting
    try:
        write_session_snapshot(
            cwd=context.cwd,
            tool_metadata=tool_metadata,  # reused from above
            messages=messages,
            context=context,
        )
    except OSError as exc:
        logger.warning("snapshot_write_failed", error=str(exc))
```

Both writes share the ``tool_metadata`` producer (single
computation per turn). Errors don't fail the turn — observability
event surfaces them.

---

## Out of scope (Phase 13+)

- **Auto-detect + offer resume** in ``oh ask`` when fresh
  snapshot exists. Phase 12 ships ``--resume`` explicit only.
- **``oh memory add``** explicit write CLI. Extraction covers
  most cases; revisit if users complain.
- **Auto-dream subprocess** — periodic background consolidation
  of memories + snapshot GC. Phase 14+ (cross-process daemon
  complexity).
- **Snapshot diff visualization** — ``oh snapshot diff <id1> <id2>``
  to compare two sessions. Tooling concern.
- **Hook state persistence** — Phase 12's snapshot loses hook-
  internal state (e.g. PostToolUse hooks that accumulate counters).
  Hooks are stateless by contract today; revisit if user-authored
  hooks need state.
- **Cross-session memory deduplication** via snapshot scan.
  Auto-dream territory.
- **Snapshot encryption at rest** — Phase 12 writes plaintext
  JSON. If a project's conversation contains secrets, the user
  already has bigger problems (extraction has 6-regex scan;
  snapshots have no scan). Phase 13+ evaluates.
- **Plugin-authored snapshot formats** — let users author their
  own snapshot schema for tools like LangSmith / Arize. Phase 14+.
- **Snapshot retention policy** — Phase 12 only stores
  ``current.json`` (overwrite). ``history/`` directory is reserved
  but not populated. Phase 13 adds rotation.
- **LLM-authored ``task_focus_state``** — populate
  ``goal`` / ``next_step`` via secondary LLM pass at turn end.
  Adds another LLM call; defer until usage data shows the static
  heuristic falls short.

---

## Critical decisions (D30.x)

| ID | Decision | Why |
|---|---|---|
| **D30.1** | Phase 12 scope = resume + snapshot + Phase 11 debt fold-in; NOT memory-add / auto-dream | Avoid scope creep; resume alone is one phase of work |
| **D30.2** | Snapshot is JSON ``v1`` at ``~/.openharness/snapshots/<basename>-<sha1>/current.json``; parallel to (not replacing) the 5-slot markdown checkpoint | Two consumers (L3 compact / resume) have different fidelity needs |
| **D30.3** | Snapshot written every user turn end, atomic | Same call site as session_memory writer; ~5ms cost negligible |
| **D30.4** | ``--resume`` is opt-in (default OFF); cwd-aware default snapshot + explicit ``<id>`` pick | Preserve current ``oh ask`` semantics; auto-resume is behavior change |
| **D30.5** | 3 staleness signals at resume time: git HEAD drift warns, cwd mismatch refuses, version mismatch refuses | Silent corruption > false negative warning |
| **D30.6** | ``tool_metadata`` producer is engine-side static heuristic (scan Read/Write/Edit tool_use); ``task_focus_state`` left None | Deterministic + no LLM cost; Phase 13 evaluates LLM upgrade |
| **D30.7** | ``QueryContext.from_snapshot`` factory loads agent-state, caller reconstructs runtime-state | Matches HKUDS resume contract; tool/skill/hook drift expected |
| **D30.8** | Nested ``SnapshotSettings`` (env-only; no CLI flag for ``enabled``) | Same pattern as ``CompactSettings`` / ``ExtractionSettings`` |
| **D30.9** | Engine-side session_memory writer wiring lands in Phase 12 (Phase 11 debt) | Producer (``tool_metadata``) is shared with snapshot writer; one place, two consumers |

---

## Dependency direction

```
services/
├── session_memory.py                  ← extend if needed (write helper
│                                        already shipped Phase 11)
└── snapshot.py                        ← NEW: write_session_snapshot +
                                        load_snapshot + staleness check

engine/
├── context.py                         ← +snapshot_enabled field
│                                        +from_snapshot classmethod
├── query.py                           ← per-turn end finally block:
│                                        compute tool_metadata once,
│                                        call session_memory + snapshot
│                                        writers
└── messages.py                        ← +_collect_turn_metadata helper

config/settings.py                     ← +SnapshotSettings nested model

cli.py                                 ← +--resume flag on ask + chat
                                        +load_snapshot helper
                                        +banner on chat resume

prompts/                               ← ZERO DIFF
markdown_store/                        ← ZERO DIFF
skills/ commands/ bundles/ plugins/    ← ZERO DIFF
mcp/ permissions/ protocols/           ← ZERO DIFF
memory/                                ← ZERO DIFF (extract writes
                                        to memory_store unchanged)
hooks/                                 ← ZERO DIFF (no new event, no
                                        rerun extension)
```

10 protected directories. ``engine/`` gets one new helper + finally-
block extension; ``cli.py`` gets one new flag (cleanly mirrored on
ask + chat per Phase 11 precedent); ``config/settings.py`` adds one
nested model. Everything else additive in ``services/`` (new
module).

---

## Sub-decisions deferred to build

Three open questions resolved tentatively now, locked at build time:

- **Snapshot atomicity semantics under concurrent ``oh ask``
  invocations**: two parallel ``oh ask`` processes in the same cwd
  both write snapshots → last writer wins (``os.replace`` atomicity).
  Tentative: **accept last-writer-wins**; concurrent ``oh ask`` in
  the same cwd is rare and inconsistent state is what the user
  asked for. If usage shows otherwise, Phase 13 adds file locking.
- **``cwd`` resolution for snapshot key**: ``Path.cwd().resolve()``
  vs ``os.getcwd()``? ``resolve()`` follows symlinks (different
  paths → same canonical → same snapshot). Tentative: **follow
  ``get_session_memory_dir`` precedent (resolves symlinks)** so
  snapshot lives in the same dir family as session_memory.
- **What happens when ``messages[0]`` after resume is the
  snapshot-loaded assistant message vs a fresh user message**:
  ``--resume "new question"`` appends "new question" as a user
  message after the snapshot messages. ``--resume`` without a
  prompt (chat mode) just loads snapshot + waits for input.
  Tentative: **lock this contract**; tests pin both shapes.

---

## Acceptance for Phase 12 close-out (template)

### Snapshot writer (D30.2 + D30.3)

- [ ] ``services/snapshot.py::write_session_snapshot()`` writes
  JSON v1 atomic
- [ ] Schema includes all 11 fields per D30.2
- [ ] ``version=1`` + ``schema="openharness.snapshot.v1"`` constants
  centralized
- [ ] ``git_head`` populated when cwd is a git repo, null otherwise
- [ ] ``tool_metadata`` round-trips byte-identical with engine's
  producer output
- [ ] Coverage: 6 unit tests (happy path / no-git case / atomic
  / overwrite / size / disabled short-circuit)

### Snapshot loader + staleness (D30.5 + D30.7)

- [ ] ``services/snapshot.py::load_snapshot()`` returns parsed
  dict or raises ``SnapshotError`` subclass
- [ ] ``SnapshotStalenessWarning`` enum: ``GIT_HEAD_DRIFT`` /
  ``OLD_AGE`` / ``CWD_MISMATCH`` / ``VERSION_MISMATCH``
- [ ] Cwd mismatch → ``SnapshotError`` raised
- [ ] Version > supported → ``SnapshotError`` raised
- [ ] Version < supported → loads with WARN log
- [ ] Git HEAD drift → loads with WARN log (not error)
- [ ] Coverage: 6 unit tests covering each staleness signal

### Engine-side ``tool_metadata`` producer (D30.6)

- [ ] ``engine/messages.py::collect_turn_metadata(messages) -> dict``
- [ ] ``recent_files`` extracts paths from Read/Write/Edit tool_use
  blocks; dedupes within turn
- [ ] ``verified_work`` summarizes successful tool_results
- [ ] ``task_focus_state`` returns dict with goal=None, next_step=None
- [ ] Tool not in {Read,Write,Edit} → skipped (Bash / Grep / etc.
  don't go in recent_files)
- [ ] Coverage: 5 unit tests

### Engine call-site wiring (D30.9)

- [ ] After ``_maybe_extract_memories`` at turn end, compute
  ``tool_metadata`` once
- [ ] Call ``update_session_memory_file`` when ``session_memory_path``
  is set
- [ ] Call ``write_session_snapshot`` when ``snapshot_enabled``
- [ ] Both errors caught + logged, don't fail the turn
- [ ] Coverage: 4 integration tests (both writers fire on
  end_turn; both skipped on tool_use turn; writer errors
  contained; metadata producer called once per turn)

### CLI resume surface (D30.4)

- [ ] ``oh ask --resume "question"`` loads cwd's snapshot +
  appends new user message
- [ ] ``oh ask --resume`` without prompt is rejected (prompt
  positional is required for ``ask``)
- [ ] ``oh ask --resume <git-head-prefix> "question"`` matches
  snapshot by prefix; ambiguous → error with list; missing →
  error
- [ ] ``oh chat --resume`` loads snapshot + prints banner +
  enters REPL
- [ ] ``oh chat --no-resume`` ignores snapshot (override
  ``OPENHARNESS_SNAPSHOT__ENABLED=true`` write default)
- [ ] No snapshot for cwd → ``--resume`` warns + starts fresh
- [ ] Coverage: 8 integration tests covering all 4 flag combinations
  on ask + chat + happy paths + error paths

### ``QueryContext.from_snapshot`` (D30.7)

- [ ] Factory accepts the snapshot dict + runtime kwargs
- [ ] Returns ``(QueryContext, list[ConversationMessage])``
- [ ] Fields not in snapshot (api_client, registries, etc.) must
  be passed by caller (kwargs required)
- [ ] Default ``session_memory_path`` + ``memory_store`` rebuild
  from cwd
- [ ] Coverage: 4 unit tests

### Settings (D30.8)

- [ ] ``SnapshotSettings(enabled=True, max_age_warn_days=7)``
- [ ] ``OPENHARNESS_SNAPSHOT__ENABLED=false`` env override
- [ ] ``OPENHARNESS_SNAPSHOT__MAX_AGE_WARN_DAYS=30`` env override
- [ ] Coverage: 3 unit tests

### Cross-cutting invariant verification

- [ ] ``git log <Phase 11 close d3beb40^>..HEAD -- src/openharness/markdown_store/``
  zero
- [ ] Same for skills / commands / bundles / plugins / mcp /
  permissions / prompts / protocols / memory / hooks
- [ ] ``git diff`` for ``engine/query.py`` shows only additive
  per-turn-end finally block + ``from_snapshot`` factory in
  ``engine/context.py``
- [ ] ``git diff`` for ``services/session_memory.py`` may add
  helpers but the public ``update_session_memory_file`` signature
  stays byte-identical (Phase 11 contract preserved)

### Phase 12 close-out gates

- [ ] All Phase 11 tests still pass (1789 → expect 1789 + Phase
  12 additions)
- [ ] ``ruff check src tests`` clean; ``mypy src`` strict clean
- [ ] Coverage ≥ 95% (gate)
- [ ] ``learnings/phase-12.md`` retro written with the standard
  6-section template

---

## Risks

| Risk | Mitigation |
|---|---|
| Snapshot file grows unbounded over many turns | JSON is structural — message size is the only growth axis; same growth as Phase 11 session_memory checkpoint (no harder problem) |
| Resume loads stale ``system_prompt`` (user removed skill but snapshot still references it via Skill section text) | D30.2 stores system_prompt verbatim; doesn't try to re-validate against current skill_store. Stale prompt is the correct default — agent's reasoning chain depends on what it saw |
| ``QueryContext.from_snapshot`` field-by-field maintenance burden as QueryContext grows | Already 14 fields; factory becomes 14 explicit assignments. Phase 13 may refactor with ``dataclasses.replace`` if field count crosses 25 |
| Snapshot atomicity vs. ``oh ask --resume`` racing the writer | Same atomicity as Phase 11 session_memory: ``os.replace`` is atomic within a filesystem. Worst case: read sees previous version (acceptable — one turn stale) |
| Schema migration when v2 lands | ``version`` field gates parser dispatch; v2 ships with v1 reader. Migration is a v1 → v2 upgrade tool, not breakage |
| ``git_head`` lookup adds latency to every turn | Single ``git rev-parse HEAD`` is ~5ms; cached per ``run_query`` invocation if needed |
| User accidentally commits snapshot file (it has full conversation) | Storage path is under ``~/.openharness/`` not in cwd; cannot be ``git add``-ed accidentally |
| ``--resume`` user expects to inject new user message but the snapshot ends mid-tool-use turn | Snapshot is taken AT TURN END (stop_reason != tool_use); never persisted mid-loop. Verified by D30.3 |
| Phase 11 debt (session_memory writer wiring) introduces L3 compact regression because L3 was previously a no-op | Phase 11 T7-7b E2E test verifies L4 fires on overflow; Phase 12 should add the inverse — L3 fires when checkpoint is fresh + relevant. New test (close Phase 11 retro §4.2's "no L3 hit observed yet") |

## Risks specifically NOT mitigated (Phase 13+)

- **Snapshot encryption at rest** — plaintext JSON includes user
  prompts + LLM responses; if project has sensitive data + shared
  HOME dir, leak risk. Mitigation requires user-key management.
- **Concurrent ``oh ask`` write races** — accept last-writer-wins.
- **Snapshot rotation / GC** — ``current.json`` only; ``history/``
  reserved but unpopulated.
- **Resume cross-machine** — snapshot dir is HOME-local; copying
  requires user effort (`scp ~/.openharness/snapshots/...`).
  Phase 14+ could add ``oh snapshot export / import``.
- **Tools added since snapshot** — `tool_registry` rebuilt fresh at
  resume; if conversation references a now-removed tool, agent may
  attempt to call it on next turn. Currently the engine surfaces
  "tool not found" via the standard recovery path — acceptable.
- **Snapshot vs session_memory checkpoint divergence after manual
  edit** — user manually edits the markdown checkpoint between
  turns; snapshot won't reflect those edits. Both have the same
  contract: written by the engine, never read by the engine
  modifying.

## Pointers

- Plan: [`tasks/phase-12-plan.md`](../tasks/phase-12-plan.md)
- Phase 11 boundary (summarization substrate that Phase 12 builds on):
  [`decisions/26-phase-11-boundary.md`](./26-phase-11-boundary.md)
- Phase 11 retro (T7 §5 Phase 12 predictions; T5 session_memory
  writer debt explicitly flagged):
  [`learnings/phase-11.md`](../learnings/phase-11.md)
- Phase 10 boundary (memory subsystem; snapshot reuses the cwd-
  hashed user-global directory pattern):
  [`decisions/25-phase-10-boundary.md`](./25-phase-10-boundary.md)
