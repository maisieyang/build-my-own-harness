# Phase 15 Boundary — Rich `Live` spinner for tool-call execution

> Status: drafted at Phase 15 entry, 2026-06-02.
>
> Scope note: Phase 15 is a **renderer-only** UX enhancement.
> `ToolExecutionStartedEvent` → `ToolExecutionCompletedEvent`
> windows get a transient `rich.Live` region with a spinner +
> elapsed-time counter, replaced at completion by the final
> status line. **No protocol changes. No engine changes. No tool
> semantics changes.** Blast radius is `_stream_render.py` and
> the renderer test suite.
>
> Trigger: a long TUI-ecosystem design discussion (see
> [`docs/ideas/tui-vs-web-frontend-first-principles.md`](../docs/ideas/tui-vs-web-frontend-first-principles.md))
> concluded that for OpenHarness's stated learning-project +
> Python-stack constraint, the highest-value smallest-cost UX
> upgrade is **enhancing the existing append-only renderer with
> `rich.Live`**, *not* building a full TUI (Textual / Ink). This
> phase ships that minimal enhancement.
>
> Related work references:
> - Phase 1 (D5.5) — original 3-event renderer
> - P2-T6.6d — extension to 5 events, added the tool-event surface
>   that Phase 15 wraps in a Live region
> - [`docs/ideas/tui-vs-web-frontend-first-principles.md`](../docs/ideas/tui-vs-web-frontend-first-principles.md)
>   — design rationale for "stay append-only, enhance rather than
>   replace"

---

## Triggering observation

Current renderer (`_stream_render.py`, 106 lines) draws each tool
call as two synchronous lines:

```
[Bash] command='ls /tmp'
... user has no signal during execution ...
[Bash] → file1\nfile2\n...
```

For tools that take more than ~1 s (`Bash` on heavy commands,
`WebFetch`, `SpawnAgent`), the user sees nothing during the gap.
The full output IS already in the `ToolResultBlock` returned to
the LLM, so the LLM stays informed — only the **user-visible
terminal** lacks progress feedback. This is the smallest
isolated UX gap in the current renderer.

---

## In scope

### D30.1 — Live region scope: one `Started → Completed` pair

While in the window between `ToolExecutionStartedEvent` and the
matching `ToolExecutionCompletedEvent`, the screen shows a
transient region containing:

- A braille spinner frame (rich default set)
- Tool name + args preview (same format as the current `[ToolName]
  arg=val` line)
- Elapsed seconds since the `Started` event

On the `Completed` event, the region is **replaced** in-place by
the final status line (matching the current "→ output" or
"✗ error" format). The region is bounded by the event pair only;
no other event types alter Live state.

**Concurrent / nested tool execution is out of scope.** The
current engine dispatches tool calls serially (P2-T4.4a); the
Phase 15 renderer assumes that invariant. If concurrent
dispatch lands in a future phase, this section will need
revisiting.

### D30.2 — Spinner character set: rich default braille (R1 locked)

`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` — rich's default `Spinner("dots")`. ASCII fallback
considered and rejected:

- rich itself auto-falls-back based on `Console.legacy_windows`
  detection
- Forcing ASCII shrinks the visual to noise on modern terminals,
  which are >99% of the user surface

### D30.3 — Non-TTY behavior: renderer falls back to current write+flush (R2 locked)

**Explicit branch at `render_stream` entry**, not reliance on
`rich.Live`'s internal auto-degrade. Reason: existing tests
assert exact stdout byte sequences; `rich.Live` in non-TTY mode
may still print intermediate frames or render artifacts. The
safer invariant is "non-TTY output is byte-identical to v0.2.0".

Detection: a `Console(file=out)` is constructed once at
`render_stream` entry; `console.is_terminal` decides the branch.

- `is_terminal == True`  → tool events go through Live region
- `is_terminal == False` → tool events go through the existing
  `out.write()` + `flush()` path, byte-identical to v0.2.0

No `--no-live` flag. The auto-detect path subsumes pipe / file
redirect / CI / `oh ask --print` (non-interactive) without new
CLI surface.

### D30.4 — Test strategy: tests stay on the non-TTY branch (R3 locked)

Existing renderer tests (`tests/cli/test_render.py`, 289 lines)
already use `io.StringIO()` as the stdout sink. `Console(file=
StringIO())` reports `is_terminal == False`, so existing tests
**automatically take the fall-back branch** and their stdout
string assertions pass byte-identical.

**Phase 15 adds a second test surface** that exercises the
Live branch using `Console(force_terminal=True, file=StringIO())`
and asserts on structural properties (spinner frame appears,
elapsed counter increments, final line replaces region) rather
than exact byte sequences. This split keeps the existing
contract tests deterministic and isolates the animation-sensitive
assertions to dedicated Live tests.

Rejected option: pattern-matching tests against spinner frames
in the main contract test suite. Reason: animation timing makes
those tests flaky in CI.

### D30.5 — Out of scope (defer to Phase 16+ if Phase 15 lands clean)

- Streamed assistant text rendered as Markdown (capability "B"
  in the design discussion — streaming re-parse perf is a
  separate problem)
- Bottom-of-screen status line (capability "C" — requires
  `rich.Layout`, larger blast radius)
- Collapsible tool-output panels (capability "D" — needs
  key-event capture, append-only model can't do)
- Multiple concurrent tool spinners
- Customizable spinner style / color (premature)
- Token-usage live counter

---

## Invariants Phase 15 must hold

- `engine/*`, `services/*`, `protocols/*` — zero diff
- `tools/*` — zero diff (renderer is purely a sink)
- All 7 existing tools — byte-identical behavior
- Existing `tests/cli/test_render.py` — pass with zero
  assertion changes (covered by D30.4 fall-back branch)
- `tests/cli/test_loop_integration.py`,
  `tests/observability/test_smoke.py`,
  `tests/cli/test_cli.py` — pass unchanged
- `oh ask --print` non-interactive mode — byte-identical to
  v0.2.0 (subsumed by D30.3 non-TTY auto-degrade)
- `pyproject.toml` — zero new direct deps (`rich` is already
  a transitive dep via `typer`, confirmed present in `uv.lock`)

---

## Touched files (forecast)

| File | Change |
|---|---|
| `src/openharness/_stream_render.py` | + `is_terminal` branch in `render_stream`; + `_render_tool_started_live` / `_render_tool_completed_live` helpers using `rich.Live` + `rich.spinner.Spinner`; existing helpers unchanged for fall-back branch |
| `src/openharness/cli.py` | unchanged or +1 line (pass through default Console if any) |
| `tests/cli/test_render.py` | + new test class `TestLiveBranch` exercising `force_terminal=True`; existing tests untouched |

**Forecast: ~80 LoC net new in `_stream_render.py`, ~60 LoC of
new tests, 0 changes in existing test assertions.**

---

## Why this is "one phase, one task" and not multiple

The current capability is single: "tool calls show progress
while running, fall back cleanly off-TTY". The four sub-pieces
(D30.1 / .2 / .3 / .4) are decisions on **how**, not separate
capabilities. Splitting into multiple tasks (e.g. "task 1: add
Console branch; task 2: implement Live region") would be
sub-task decomposition — exactly what CLAUDE.md §"Spec at the
right altitude" rules against.

Phase 15 has exactly one P15-T1.
