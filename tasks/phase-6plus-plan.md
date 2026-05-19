# Phase 6+ Implementation Plan — `oh chat` Multi-Turn REPL

> Phase 6+ boundary: [`decisions/22-phase-6plus-boundary.md`](../decisions/22-phase-6plus-boundary.md).

## Overview

REPL surface for multi-turn conversation. Reuses Phase 7c-and-below
machinery; adds (1) a new `ConversationCompleteEvent` stream event
that exposes the final messages list, (2) a `_run_chat` async loop
that captures it across turns, (3) a `oh chat` Typer command, (4)
built-in REPL slash commands (`/exit` / `/clear` / `/help`).

**Total scope**: ~half day, 4 capabilities, ~5 commits, ~200 LoC
production + ~150 LoC tests.

## Task list

### P6+-T1: `ConversationCompleteEvent` + engine emits ✅

**Description**: New stream event type exposing the engine's final
messages list. Engine yields it as the LAST event of `run_query`.

**Acceptance**:
- [ ] `protocols/stream_events.py`:
  - New class `ConversationCompleteEvent(StrictModel)` with
    `messages: list[ConversationMessage]` field
  - Added to `ApiStreamEvent` union
- [ ] `engine/query.py`:
  - At the natural exit point (end_turn OR LoopLimitExceeded), yield
    `ConversationCompleteEvent(messages=messages)` as the FINAL
    event before the generator returns
- [ ] Tests:
  - Existing `tests/engine/test_query.py` tests pass (regression)
  - New test: `run_query` emits the new event as its LAST event with
    the full conversation history
  - New test: messages list includes user + assistant + tool_result
    when tools were dispatched

**Files**:
- `src/openharness/protocols/stream_events.py` (extend)
- `src/openharness/engine/query.py` (emit at exit)
- `tests/engine/test_query.py` (new tests)

---

### P6+-T2: `_run_chat` async REPL loop ✅

**Description**: New CLI helper that drives the multi-turn loop.
Reuses the existing bootstrap (settings, client, registry, bundles)
factored as a helper.

**Acceptance**:
- [ ] `cli.py`:
  - Factor shared bootstrap from `_run_ask` into `_build_query_context`
    helper (returns `QueryContext` + cleanup callable / context manager).
  - New `_run_chat` async function:
    - Builds the QueryContext once
    - Loop: `prompt = input(">>> ")` → if `/exit`/`/quit` break; if
      `/clear` reset history; if `/help` print help; else run
      `run_query` with current history → render stream → capture
      `ConversationCompleteEvent.messages` → set as next history
    - EOF (Ctrl+D) → clean break
    - KeyboardInterrupt (Ctrl+C) → print hint + continue
- [ ] New Typer command `chat`:
  - Same flag surface as `ask` minus the prompt arg
  - Same error UX as `ask` (UnknownCommandError / UnknownBundleError
    etc. caught and printed)
- [ ] Tests:
  - REPL exits on `/exit`
  - REPL exits on EOF
  - History grows across turns (mock input + run_query)
  - `/clear` resets history
  - `/help` prints commands
  - Bundle persists across turns

**Files**:
- `src/openharness/cli.py` (new `_run_chat` + `chat` command)
- `tests/cli/test_chat.py` (new test file)

**Sub-units**:
- 2a — Factor `_build_query_context` from `_run_ask`
- 2b — Implement `_run_chat` REPL loop + Typer command
- 2c — Tests

---

### P6+-T3: Invariant + README + retro ✅

**Acceptance**:
- [ ] `tests/execution/test_invariant.py` extends forbidden set with
  `ConversationCompleteEvent` (must NOT leak into permissions/,
  hooks/, observability/, mcp/, compaction/, skills/, commands/,
  bundles/, markdown_store/, tools/, execution/).
- [ ] Formal git-diff vs Phase 7c close: protected dirs unchanged.
- [ ] README "Phase 6+ — `oh chat` multi-turn REPL" section
- [ ] `learnings/phase-6plus.md` retro
- [ ] DoD checklist all green
