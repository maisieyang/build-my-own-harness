# Phase 15 Implementation Plan — Rich `Live` spinner for tool-call execution

> Boundary contract: [`decisions/30-phase-15-rich-live-boundary.md`](../decisions/30-phase-15-rich-live-boundary.md).
> A renderer-only UX enhancement: tool-call event pairs get a
> transient `rich.Live` region (spinner + elapsed timer) on TTY,
> fall back byte-identical to v0.2.0 off-TTY. Zero protocol /
> engine / tool / dependency changes.

## Overview

**Phase 15 goal**: between `ToolExecutionStartedEvent` and the
matching `ToolExecutionCompletedEvent`, the renderer displays a
spinner + elapsed-seconds counter on TTY, replaced in place by
the final status line on completion. Off-TTY, the renderer
behaves byte-identical to v0.2.0.

**Cross-cutting invariant** (the 9th compounding test of the
abstraction-first pattern, after Phase 14's 11/11):

- `engine/`, `services/`, `protocols/`, `tools/`,
  `permissions/`, `skills/`, `commands/`, `bundles/`, `plugins/`,
  `mcp/`, `markdown_store/`, `memory/`, `hooks/`,
  `observability/` — zero diff
- All 7 existing tools — byte-identical behavior
- All existing tests — pass with **zero assertion changes**
  (boundary D30.4 fall-back branch covers this)
- `pyproject.toml` — zero new direct deps

Only `_stream_render.py` and `tests/cli/test_render.py` get
touched.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/30-phase-15-rich-live-boundary.md`](../decisions/30-phase-15-rich-live-boundary.md) | D30.1 Live region scope = one Started/Completed pair, no nesting; D30.2 spinner = rich default braille (R1); D30.3 non-TTY = explicit fall-back branch via `Console.is_terminal`, no `--no-live` flag (R2); D30.4 existing tests untouched, Live branch covered by new dedicated test class (R3); D30.5 out-of-scope: markdown / status line / collapsible / concurrent / customization |

---

## Task list

### P15-T1: Tool-call Live spinner with non-TTY fall-back 🔜 NEXT

**Description**: Wrap each `ToolExecutionStartedEvent` →
`ToolExecutionCompletedEvent` pair in a `rich.Live` region when
stdout is a terminal; show a braille spinner + tool-name + args
preview + elapsed-seconds counter while running; replace the
region with the final `→ output` / `✗ error` line on completion.
When stdout is **not** a terminal (pipe / file / CI / `io.StringIO`
test sink), use the current `out.write()` + `flush()` path
unchanged. New test class exercises the Live branch with
`force_terminal=True`; existing tests stay on the fall-back
branch with zero assertion changes.

**Acceptance**:

- [ ] `render_stream()` constructs `rich.console.Console(file=out)`
  once at entry; branches on `console.is_terminal`.
- [ ] `is_terminal == True` branch: on
  `ToolExecutionStartedEvent`, enter a `rich.Live` context with a
  renderable containing `rich.spinner.Spinner("dots")` + the
  same arg preview format as today (`tool_name + args`). The
  Live region is updated every ~100ms with the new
  elapsed-seconds value. On the matching
  `ToolExecutionCompletedEvent`, exit the Live context such that
  the **final printed line** is the existing format (`[Bash] →
  output...` for success, `[Bash error] ✗ ...` for `is_error=True`).
- [ ] `is_terminal == False` branch: existing `_render_tool_started`
  / `_render_tool_completed` functions are called unchanged.
  Output is byte-identical to v0.2.0 — verified by all existing
  `test_render.py` assertions passing without modification.
- [ ] Text-delta / retry / message-complete event handling
  unchanged in both branches (those events do not interact with
  Live).
- [ ] Live region cleanup on exception: if the event stream
  raises mid-tool-execution, the Live context exits cleanly
  (`with rich.Live(...)` block exit guarantees this) and does
  not leave terminal in raw / hidden-cursor state.
- [ ] New test class `TestLiveBranch` in `tests/cli/test_render.py`:
  - Uses `Console(force_terminal=True, file=StringIO(),
    no_color=True)` to enter the Live branch deterministically.
  - Asserts that during `Started → Completed` window, output
    contains spinner frame characters from rich's braille set.
  - Asserts that after `Completed`, the **final visible line**
    matches the existing `→ output` format (modulo any leading
    cursor-control bytes rich emits — assertion uses `.endswith()`
    or strip-ANSI rather than `==`).
  - Asserts that an `is_error=True` `Completed` event renders the
    `[tool error] ✗ ...` format.
- [ ] Existing test classes in `tests/cli/test_render.py` —
  byte-identical assertions pass.
- [ ] `tests/cli/test_loop_integration.py`,
  `tests/observability/test_smoke.py`, `tests/cli/test_cli.py`
  — pass unchanged.
- [ ] No new entries in `pyproject.toml` `[project.dependencies]`.
  `uv.lock` may not require regeneration (rich already pinned via
  typer); verify `uv sync` reports no resolution change.
- [ ] `ruff check src tests` clean.
- [ ] `mypy --strict src` clean.
- [ ] Manual smoke (recorded in retro): `oh ask "list /tmp"` in a
  real terminal shows the spinner during `Bash` execution; `oh
  ask "..." | cat` shows the v0.2.0 lines unchanged.

**Predicted retro questions**:
- Did `rich.Live` cleanup behave correctly under all exit paths
  (normal complete, exception, KeyboardInterrupt)?
- Was the "elapsed-seconds counter that updates every 100ms"
  implementable without a background task, or did it require a
  short-lived task per tool call? If the latter, what's the
  cancellation story?
- Did the renderer test split (existing contract tests + new
  `TestLiveBranch`) feel natural, or is `TestLiveBranch` brittle
  in ways that suggest a different test seam?
- Was the boundary's "0 new deps" claim correct, or did
  something need to land?
- Predicted next-phase candidate: if streamed assistant text as
  markdown (capability "B" in the discussion) becomes the next
  ask, is the renderer's branch structure (TTY / non-TTY split)
  the right seam to add a second Live region on top of, or does
  it need re-shaping?
