# Decision 07 — Base Tools (P2-T3)

- **Date**: 2026-05-08
- **Phase / Module**: Phase 2 / P2-T3
- **Status**: Decided

## Context

P2-T2 shipped the tool abstraction (`BaseTool[InputT]` ABC + `ToolRegistry`).
P2-T3 ships the five base tools — Read, Write, Edit, Bash, Grep — that
constitute the agent's minimal action space.

phase-2-plan.md pre-locked most semantic details (10 MiB read limit, 600s
Bash timeout, 12k char truncation, 200/2000 line caps for Grep, etc.).
This document captures only the **cross-cutting decisions** that emerged
in the Three-Axis kickoff and were not pre-spelled in phase-2-plan.md.

## Decisions

### D9.1 — Path resolution: relative -> cwd, absolute as-is

**Decision**: A — relative paths resolve to ``context.cwd``; absolute paths
are used as-is and then validated against any tool-specific scope rules.

**Why**: LLM prompts are highly likely to use relative paths
("read package.json"); ``context.cwd`` is the natural anchor. Absolute
paths must still work for legitimate cases (system file diagnostics)
but get the same scope check write-side tools apply.

**Reversibility**: Trivial — single helper ``_resolve(raw, cwd)`` per tool.

### D9.2 — Project-root scope = ``context.cwd`` (Write/Edit only)

**Decision**: B — Write and Edit refuse paths that, after ``resolve()``,
fall outside ``context.cwd``. Read and Grep do **not** apply this guard
(read-only operations don't risk persistent damage).

**Why**: Phase 2's ``--auto`` flag is reserved for P2-T6 to relax this
default. Until then, write-side tools must not silently let the LLM
escape via ``..`` or absolute paths. ``Path.resolve(strict=False)`` on
both sides handles symlinks and ``..`` normalization safely.

**Reversibility**: P2-T6 plumbs the relaxation through ``QueryContext``;
the per-tool check stays in place but consults the flag.

### D9.3 — One file per tool: ``tools/{read,write,edit,bash,grep}.py``

**Decision**: B — each tool lives in its own submodule. Filenames are
plain (``read.py`` not ``read_tool.py``) since the package name already
disambiguates.

**Why**: Easier to navigate when 5+ tools coexist; per-tool tests live
in matching ``tests/tools/test_<tool>.py`` files. Phase 5 plugin / MCP
adapters can drop alongside without restructuring.

**Reversibility**: Pure naming; unaffected by future capabilities.

### D9.4 — Bash ships without a deny-list at this layer

**Decision**: A — the Bash tool itself has **no** pattern filtering. Any
safety enforcement is the responsibility of P2-T6 ``PermissionChecker``.

**Why**: D6.2 already allocated the deny-list to P2-T6; duplicating
filters in two layers is hard to reason about. Phase 2's working
assumption is "developer machine, tests in ``tmp_path``". P2-T6 wires
the real interception layer via ``QueryContext.permission_checker``.

**Reversibility**: P2-T6 doesn't modify the Bash tool — it wraps it.

### D9.5 — exit_code in metadata, not in output

**Decision**: D9.5 — Bash's ``ToolResult.output`` carries only the
command's merged stdout/stderr. ``exit_code``, ``duration_ms``, and (on
timeout) ``timed_out`` go to ``metadata``.

**Why**: ``output`` is the channel the LLM reads to reason about the
tool's effect; ``metadata`` is the channel programs read for state
machines / metrics. Mixing channels (e.g., prepending ``[exit 1] ...``
to output) pollutes both.

**Reversibility**: Render layer (CLI / future TUI) is free to display
``metadata.exit_code`` next to the output; that's a presentation decision,
not a tool-API decision.

### D9.6 — Error messages: descriptive, ~200 char cap, OS errors wrapped

**Decision**: B — every ``ToolResult(is_error=True, output=...)``
message names the failure mode and the relevant context (path / signal /
exit code). Underlying ``OSError`` messages are wrapped, not raised.

**Why**: The LLM uses these strings to decide its next action. Terse
messages ("FAIL") force it to guess; verbose stack traces blow context.
~200 chars is enough for "what + where" without polluting the LLM's
working memory.

**Reversibility**: Each tool's error messages are local strings — no
contract anywhere depends on exact wording.

## Consequences

- ``tasks/phase-2-todo.md`` P2-T3 marks 6 sub-units (3a-3f) referencing
  the decisions above.
- Path-resolution helpers (``_resolve`` / ``_inside_project_root``) are
  duplicated across read.py, write.py, edit.py, grep.py. If a sixth
  filesystem-touching tool lands, extract to ``tools/_paths.py``.
- ``create_default_tool_registry()`` lives in ``tools/__init__.py`` and
  is the entry point P2-T6 ``cli.py`` will call.
- ``learnings/07-base-tools.md`` will capture the *Python patterns* used
  (asyncio.to_thread for blocking IO, asyncio.subprocess for Bash/Grep,
  Pydantic Field constraints as input validation, etc.).
