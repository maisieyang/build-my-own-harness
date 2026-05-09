# Decision 06 — Phase 2 Boundary Contract

- **Date**: 2026-05-07
- **Phase / Module**: Phase 2 entry / Tool Loop
- **Status**: Decided

## Context

Phase 1 closed (runtime-wise) when `oh ask` first streamed real Qwen output.
Phase 2 is the implementation of §1 of the first-principles map
([learnings/openharness-first-principles.md](../learnings/openharness-first-principles.md)):
the agent loop. Phase 1 built the chambers (API abstraction, event
abstraction, CLI shell); Phase 2 makes the heart beat.

Before writing any Phase 2 code, this document fixes the boundary:

- What goes IN Phase 2 (and what is deferred to later phases)
- The 5 product decisions that shape every Phase 2 capability
- The minimal Phase 1 closure path before Phase 2 implementation begins

## Phase 2 Essence

> Wrap the single `client.stream_message()` call inside a
> `while stop_reason == "tool_use"` loop, so the LLM can call tools,
> see results, and decide what to do next.

**Deliverable**: `oh ask "what files are in /tmp"` → LLM picks `Bash`
→ Bash runs → result fed back → LLM writes the answer.

## Scope

### IN (Phase 2 must-do)

| Module | Responsibility | First-principles layer |
|---|---|---|
| `engine/` (`query.py` / `messages.py` / `context.py`) | `run_query()` loop; conversation history; turn limit | §1 core loop |
| `tools/base.py` | `BaseTool` / `ToolRegistry` / `ToolResult` / `ToolExecutionContext` | §2.2 tool abstraction |
| `tools/{read,write,edit,bash,grep}_tool.py` | The 5 base tools | §2.2 instances |
| `permissions/checker.py` (minimal) | Deny-list for known-dangerous patterns; supports `--auto` / `--dry-run` modes | §2.3 (thinnest layer) |
| `protocols/stream_events.py` extension | Add `ToolExecutionStarted` / `ToolExecutionCompleted` events | §2.4 events evolved |
| `prompts.py` | `build_system_prompt(tools, env) -> str` | §5 product shell evolved |
| `_stream_render.py` extension | Render tool calls + results in addition to text | §5 UI evolved |

Estimated 5-8 capabilities, 2-3 weeks.

### OUT (deferred, with rationale)

| Deferred to | Item | Why not now |
|---|---|---|
| Phase 3 | Full 9-step permission algorithm + sensitive-path hardcoding | Phase 2 ships only the minimal interception needed to run tools safely |
| Phase 3 | Hooks lifecycle (7 events × 4 types) | Extension of the interception layer; basic interception first |
| Phase 3 | Interactive `require_confirmation` UX | Phase 2 uses `--auto` / `--dry-run` flags instead (see D6.2) |
| Phase 3 | Parallel tool execution (`asyncio.gather`) | Phase 2 is serial (D6.3); parallelism is performance, "correct first" |
| Phase 3 | Full retry + error hierarchy hardening | Phase 1 T3 leaves the foundation; Phase 3 hardens |
| Phase 4 | Auto-compaction (any tier) | Phase 2 tolerates long-context blowup; `max_turns` is the floor |
| Phase 5 | MCP / slash commands / Skills | Extensibility; need the kernel first |
| Phase 6 | Sub-agents / Worktree / Sandbox | Advanced isolation |
| Out of scope | WebSearch / WebFetch / LSP tools | Not part of harness skeleton |
| Out of scope | NotebookEdit / Cron / TaskCreate / EnterPlanMode | OpenHarness has 43+; 5 base tools demonstrate the loop |

## Decisions

### D6.1 — Loop exit: `stop_reason` primary + `max_turns` hard cap

**Decision**: Hybrid (option C from boundary discussion).

- Loop continues while `stop_reason == "tool_use"`.
- Hard ceiling `max_turns=20` (default) — exceeding raises a recoverable
  error visible to the user with hint "loop hit turn limit, try simpler
  prompt or raise --max-turns".
- CLI flag `--max-turns` overrides; later phases may add cost-cap on top.

**Why**: Industry standard (Claude Code / OpenHarness / LangGraph all
do this). Pure `stop_reason` is unsafe (LLM bug → infinite loop); pure
counter is rigid (legitimate long tasks get cut). Hybrid trusts the LLM
but bounds the worst case.

**Reversibility**: Adding cost-cap later is additive; removing the
counter would require re-evaluating safety — treat counter as load-bearing.

### D6.2 — Permission baseline: deny-list + `--auto` / `--dry-run` flags

**Decision**: B+ — minimal deny-list interception; no interactive
confirmation dialogue in Phase 2.

- All 5 base tools enabled by default (including `Bash`).
- `--auto` flag: skip any future confirmation prompts (default behavior
  in Phase 2 since none exist yet; flag pre-reserved for Phase 3).
- `--dry-run` flag: list every tool call the loop *would* make, do not
  actually execute. For debugging and safety inspection.
- Phase 2 hardcodes a small deny-list (e.g., commands matching
  `rm -rf /`, `:(){ :|:& };:`); details emerge during build.

**Why**: Phase 2's goal is "make the heart beat", not "ship a
production-safety system". Interactive confirmation would add ~30%
complexity to Phase 2 (UX, flow control, async input) and is the
proper subject of Phase 3. The two flags give us the safety story we
need for demos and self-use without that complexity.

**Reversibility**: Phase 3 will replace the deny-list with the 9-step
algorithm — the *interface* (`PermissionChecker.evaluate(...)`) stays;
the *implementation* expands.

### D6.3 — Tool execution: serial within a turn

**Decision**: A — execute tool_use blocks sequentially in their order
of appearance.

- Loop iterates `for block in tool_use_blocks: result = await execute(block)`.
- No `asyncio.gather`, no read-only/read-write classification.

**Why**: Correctness first. Parallel execution introduces race
conditions (Edit + Bash on same file) that need a tool-classification
contract (`is_read_only`) — that's Phase 3 territory. Serial is also
easier to render in the UI.

**Reversibility**: Adding parallel execution later requires:
1. `BaseTool.is_read_only -> bool` (additive)
2. Loop change to group + gather (localized)
Both are local changes, low cost.

### D6.4 — Tool naming: PascalCase to match LLM training distribution

**Decision**: B — register tools as `Read`, `Write`, `Edit`, `Bash`, `Grep`.

**Why**: Anthropic's published tool docs and Claude Code training data
use PascalCase. Empirical observation (and OpenHarness's experience)
suggests LLMs hit the right tool name faster when the wire-format name
matches what they were trained on. The cost is purely cosmetic — a
string in the registry — and our internal types are already
Anthropic-shape.

**Reversibility**: Adding snake_case aliases later is one-line per tool.

### D6.5 — System prompt assembly: function-driven (`prompts.py`)

**Decision**: C — expose `build_system_prompt(tools, env) -> str` and
inject runtime context.

- New file `src/openharness/prompts.py`.
- Function signature: `build_system_prompt(tools: list[ToolSpec], env: EnvironmentInfo) -> str`.
- Phase 2 contents: base instructions + tool catalog + cwd/OS info.
- Phase 3 will inject personalization rules into the same function.
- Phase 4 will inject memory excerpts into the same function.

**Why**: Hardcoding (option A) blocks future enrichment. File-per-section
(option B) over-engineers for current needs. A single function with
an explicit signature is the natural locus for "assemble all prompt
context here" and lets later phases extend without renaming.

**Reversibility**: Trivial — function internals are a private contract.

## Process Meta-Decision: Minimal Phase 1 Closure First

> **Amended 2026-05-07 evening**: Original draft (this morning) underestimated
> Phase 1 progress. Actual state when this decision was being written: T1-T5
> all substantively done; protocols/ at 100% coverage, api/ at 94%+ coverage,
> total project at 92.64%. The closure list below reflects the *real* gap.

Phase 1 is runtime-closed AND test-closed in code. The remaining gap before
Phase 2 implementation begins:

1. **Align `tasks/plan.md`**: T2/T3 statuses, Checkpoints, and Open Questions
   were stale (showing pending where code was done). ✅ Aligned 2026-05-07.

2. **Fix 4 failing tests caused by `.env` contamination**: dev-time `.env` file
   in project root (added when user validated Qwen end-to-end) leaks into tests
   that assert "missing required env raises ValidationError":
   - `tests/cli/test_cli.py::TestErrorUX::test_missing_api_key_prints_config_hint`
   - `tests/config/test_settings.py::TestMissingRequiredFields::test_missing_api_key_raises_validation_error`
   - `tests/config/test_settings.py::TestMissingRequiredFields::test_missing_base_url_raises_validation_error`
   - `tests/config/test_settings.py::TestMissingRequiredFields::test_completely_unset_env_raises_validation_error`

   Fix candidates (decide during build): pass `_env_file=None` in test
   fixtures, or `monkeypatch.chdir(tmp_path)` so pydantic-settings can't
   find the project `.env`. Estimated time: 1-2 hours.

3. **CI push**: local `main` is ahead of `origin/main`; once tests are clean,
   push so CI registers Phase 1 as green. User-controlled, not blocking.

4. **Phase 1 + Phase 2 combined retrospective**: T5's `learnings/phase-1.md`
   already exists. We will write a combined retro at the end of Phase 2 that
   covers the loop-implementation lessons + Phase 1 contracts in retrospect.

After steps 2-3: enter Phase 2 with a fully green CI baseline.

## Consequences

- `tasks/phase-2-plan.md` will be authored next, organized by capability
  per ARCHITECTURE.md §5 Three-Axis template.
- This document is the contract anyone implementing Phase 2 must
  honor; deviation requires updating this file (not just the code).
- `learnings/phase-1-and-2.md` is the planned retrospective venue
  (D6 process meta-decision).
