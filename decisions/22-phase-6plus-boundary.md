# Phase 6+ Boundary — `oh chat` Multi-Turn REPL

> Status: locked at Phase 6+ entry, 2026-05-19.
>
> Scope note: **interactive REPL on top of the existing `oh ask`
> single-turn machinery**. The user types successive prompts; the
> framework accumulates conversation history across turns; the same
> `run_query` engine drives each turn. Slash commands (`/exit` /
> `/clear` / `/help`) handle session management.

## Triggering observation

Every phase up to 7c is single-shot: `oh ask "..."` runs one
`run_query` invocation and exits. Real LLM productivity workflows
need **multi-turn**:

- "Read the file → summarize → now write tests based on that summary"
- "Fix this bug → run the tests → iterate on failures"
- "Explain this code → now refactor it"

A user typing the full context into one `oh ask` is fighting the
shell quoting rules and losing the engine's tool-dispatch loop's
context window. The MVP missing piece is the **REPL surface** that
wraps `oh ask`'s mechanism in a loop.

## Decisions

### D24.1 — Single-line `input()` REPL; no `prompt_toolkit` dependency

The first cut uses Python's stdlib `input()` with a `>>> ` prompt.
Multi-line input, history navigation, syntax highlighting, etc.
defer to Phase 9 if real demand surfaces. Stdlib-only keeps the
dependency surface lean and matches the "no surprise install" CLI
principle.

EOF / Ctrl+D → exit cleanly. Ctrl+C → print "(use /exit to quit)" +
continue REPL loop (matches REPL idioms in Python / Node / Ruby
shells).

### D24.2 — Cross-turn message accumulation via new `ConversationCompleteEvent`

The engine's `run_query` is an `AsyncIterator[ApiStreamEvent]`. To
expose the final conversation state to a REPL caller, we add ONE
new stream event:

```python
class ConversationCompleteEvent(StrictModel):
    """Yielded as the FINAL event of run_query when the loop exits
    cleanly (end_turn reached or max_turns hit). Carries the full
    conversation messages list so callers (oh chat REPL) can carry
    forward state to subsequent turns. ``oh ask`` ignores it."""
    messages: list[ConversationMessage]
```

The REPL captures `event.messages` from the last event, uses it as
`initial_messages` for the next turn. Subsequent turns thus see the
full history (user + assistant + tool_use + tool_result).

**Why a new event, not a mutable kwarg / return value**:

- `AsyncIterator` return values are awkward to access (need manual
  `__anext__` + `StopAsyncIteration.value`).
- Mutable kwargs (`output_messages: list = ...`) are an anti-pattern
  in async code (write-only param surfaces, ownership unclear).
- A stream event fits the existing taxonomy: it's just one more
  event type, opt-in for callers that care.

This is **one additive stream event** + engine emits it at end of
loop. ~15 LoC engine diff. `oh ask` keeps working byte-identical
(its event renderer ignores the new event).

### D24.3 — Built-in slash commands: `/exit` / `/quit` / `/clear` / `/help`

These are REPL-local; they DON'T flow to the LLM. The REPL
intercepts them before invoking `run_query`. The list:

- `/exit` and `/quit` — terminate the loop, clean exit code 0
- `/clear` — reset conversation history to empty (preserves
  `QueryContext` — same system prompt, same bundle, same tool
  registry) and prints "(conversation cleared)"
- `/help` — print the available slash commands

Phase 5b slash commands (user-authored `/<name>` for prompt
expansion) still work via the same `resolve_command_invocation`
path that `oh ask` uses. To avoid collision with built-in REPL
commands, the built-in names are reserved — a user-authored
command named `/clear` would shadow the built-in.

**Why this collision policy** (built-ins win on collision): same
reasoning as Phase 5d/5e (`BUILTIN_HOOKS` shadows plugin hooks).
Documented framework features have stable semantics; user files
shouldn't silently override them.

### D24.4 — Bundle resolves on first turn; persists for the session

If the first user input is `/<slash_cmd>` with a `mode:` field on
the resolved Command, the bundle is loaded and applied for the
ENTIRE chat session. Subsequent turns use the same bundle-modified
`QueryContext` (whitelist still active, hooks still registered,
deny_paths still augmented).

`/clear` resets messages but does NOT swap back to the unmodified
QueryContext — the bundle's overrides remain for the rest of the
session. To start a fresh non-bundle session, exit and restart.

**Why this scope**: mid-session mode switching is genuinely
non-trivial — it requires rebuilding the QueryContext mid-loop with
a different system prompt, tool registry, etc. Defer to Phase 6+.1
if real demand surfaces. MVP locks the simpler "bundle as session
context" semantics.

### D24.5 — Each turn streams to stdout, just like `oh ask`

The REPL reuses `render_stream` (the existing `oh ask` renderer).
After each turn:

- assistant text streams as it arrives
- tool_use blocks render (existing rendering)
- end_turn → REPL re-prompts with `>>> `

No special multi-turn UI (no message numbering, no separator lines
beyond what `render_stream` already does). MVP keeps the surface
minimal.

### D24.6 — `oh chat` is a new Typer command, not a flag on `oh ask`

The REPL lives at `oh chat` (not `oh ask --interactive`). Reasons:

- Different invocation semantics (no positional prompt arg)
- Different exit conditions (REPL loop vs single shot)
- Different error UX (error in chat shouldn't necessarily exit;
  surface and continue)
- Cleaner help / discoverability

Shared bootstrap is factored into a helper that both `_run_ask` and
`_run_chat` call — Settings load, client build, registry assembly,
bundle resolution, etc. ~50 LoC factored.

---

## Cross-cutting invariant

Phase 6+ is **purely additive**. Diffs vs Phase 7c close:

- **engine/** — ~15 LoC: `run_query` emits `ConversationCompleteEvent`
  as the FINAL event of the loop. Existing iterators that don't
  care (oh ask's renderer) ignore the new event.
- **protocols/stream_events.py** — new `ConversationCompleteEvent`
  class + added to `ApiStreamEvent` union.
- **cli.py** — new `_run_chat` async function + new `chat` Typer
  command + helper factoring shared bootstrap.
- **_stream_render.py** — `render_stream` handles the new event by
  ignoring it (it's metadata, not user-visible content). Optional:
  capture for the REPL's use via a kwarg.

**Zero diff** on:

- `permissions/`, `hooks/`, `observability/`, `mcp/`,
  `compaction/`, `skills/`, `commands/`, `bundles/`,
  `markdown_store/`, `tools/`, `execution/`, `prompts.py`
- `config/settings.py` (no new Settings field for MVP)

## Test invariant

`tests/execution/test_invariant.py` extended forbidden set with the
new `ConversationCompleteEvent` symbol — must NOT leak into
non-engine layers.

## Risks specifically NOT mitigated (Phase 6.1+)

- **Session save/load** (`/save <file>` / `/load <file>`) — defer
  until users actually want to resume sessions. Persistence format
  needs careful schema design (versioning, backward-compat).
- **Mid-session bundle/mode switch** — see D24.4.
- **Multi-line input** — `prompt_toolkit` dependency adds ~500KB +
  ~50ms startup; defer.
- **History recall / `/retry`** — REPL UX polish, defer.
- **Tab completion of slash commands** — defer (would need
  `prompt_toolkit` or readline integration).
- **Conversation compaction** at session-time — Phase 4's hook-
  based compaction already truncates tool results per-call;
  whole-conversation compaction (summarize old turns) is its own
  research project, defer.

---

## Pointers

- `oh ask` entry point (the shared bootstrap to factor): `src/openharness/cli.py::_run_ask`
- Engine driver: `src/openharness/engine/query.py::run_query`
- Stream event union: `src/openharness/protocols/stream_events.py::ApiStreamEvent`
- Renderer: `src/openharness/_stream_render.py::render_stream`
