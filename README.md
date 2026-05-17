# OpenHarness

[![CI](https://github.com/yangxiyue/build-my-own-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/yangxiyue/build-my-own-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **A production-grade Python harness for LLM agents — built from scratch as a learning project.**
>
> ✅ **Status**: Phase 1 complete — `oh ask "<prompt>"` streams real responses
> from any OpenAI-compatible Provider (Qwen via DashScope tested). Phase 2
> (Tool Loop) is next. See [learnings/phase-1.md](./learnings/phase-1.md)
> for the cross-module retrospective.

---

## What is this

This repo is a learning project that re-implements a Claude-Code–style LLM agent harness in
Python, from scratch, to a production-grade quality bar.

The project is staged in 7 phases (see [ARCHITECTURE.md](./ARCHITECTURE.md)):

| Phase | Goal | Status |
|-------|------|--------|
| 0 | Architecture map: tier division, module dependency graph, scope boundary | ✅ |
| 1 | **Foundation + Hello LLM** — toolchain, data models, API client, CLI, Print mode | ✅ |
| 2 | Tool Loop — `BaseTool` / `ToolRegistry` / `run_query()` / Read+Write+Edit+Bash+Grep | ⏸ next |
| 3 | Safety + Production Hardening — full permissions, hooks, retries, test coverage | ⏸ |
| 4 | Context Management — auto-compaction (microcompact + boundary detection) | ⏸ |
| 5 | Extensibility — MCP, slash commands, Skills/Plugins | ⏸ |
| 6 | One Advanced module (sub-agents / Docker sandbox / full compaction) | ⏸ |
| 7 | Polish + publish to PyPI | ⏸ |

The project specification (objective / commands / structure / style / testing / boundaries) lives
in [SPEC.md](./SPEC.md). The reverse-engineered OpenHarness reference (the project we draw
inspiration from) is in [REFERENCE.md](./REFERENCE.md). Per-decision trade-offs and rationale
live under [`decisions/`](./decisions). Per-module retrospectives live under
[`learnings/`](./learnings).

---

## Quick start

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies (creates .venv automatically)
uv sync

# Smoke check — package can be imported and basic invariants hold
uv run python -m openharness
uv run pytest
```

---

## How do I try it?

Phase 1 ships `oh ask "<prompt>"` — a single-shot CLI that streams a real
LLM response. The default Provider is **Qwen via DashScope** (OpenAI-compatible),
but any OpenAI-compatible endpoint works (OpenAI cloud, DeepSeek, Moonshot, etc.).

### 1. Get a DashScope API key

[阿里云百炼 / DashScope console](https://bailian.console.aliyun.com/) → 创建 API Key.
You can swap to OpenAI / DeepSeek / etc. by pointing `OPENHARNESS_BASE_URL` at the
right endpoint and using their key.

### 2. Set environment variables

```bash
export OPENHARNESS_API_KEY="sk-..."
export OPENHARNESS_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
# Optional — overrides the qwen-plus default; CLI --model overrides this
export OPENHARNESS_MODEL="qwen-plus"
```

Or drop them in a `.env` file at the repo root (loaded automatically).

### 3. Run it

```bash
# Default model (qwen-plus), default max-tokens (1024)
uv run oh ask "say hello"

# Override model per-invocation
uv run oh ask "explain async iterators in Python" --model qwen-max

# Cap generation length (handy for testing or budgeting)
uv run oh ask "what is HTTP/2?" --max-tokens 256

# Pipe-friendly (the renderer is append-only on stdout, retries to stderr)
uv run oh ask "list 5 git commands" | tee transcript.txt
```

### Errors are differentiated

| Situation | What you see |
|-----------|-------------|
| `OPENHARNESS_API_KEY` not set | `Configuration error` + hint pointing at the env var |
| Wrong key | `Authentication failed (HTTP 401)` + "verify OPENHARNESS_API_KEY" |
| Provider rate-limit | `Rate-limited after retries (HTTP 429)` + retry hint |
| Server error | `Request failed (HTTP <status>): <message>` |
| Loop hit `max_turns` | `Loop error: loop hit turn limit (N); raise --max-turns or simplify the prompt` |

No Python tracebacks in the default mode. Coverage 96.9% (gate 95%).

### Phase 3 features — safety + observability

Phase 3 layered four production-grade capabilities onto the Phase 2 base.
Full retro at [`learnings/phase-3.md`](./learnings/phase-3.md); the quick
tour:

#### Tools and permissions

The Phase 2 `Read` / `Write` / `Edit` / `Bash` / `Grep` tools are gated
by a **three-tier permission system**:

- **Tier 1 (hardcoded)** — denies `~/.ssh/**`, `/etc/passwd`, `~/.aws/**`,
  etc. Framework-owned, not configurable.
- **Tier 2 (user globs)** — `OPENHARNESS_DENY_PATHS="*.env,secrets/**"`
  in env or `.env`. Cwd-relative semantics like `.gitignore`.
- **Tier 3 (mode-based)** — write/exec tools restricted to project root
  unless `--auto` opts in.
- Plus the carryover **Bash catastrophic deny-list** (`rm -rf /`,
  fork-bomb, `mkfs`, etc.).

```bash
# Tier 1 blocks reading SSH keys regardless of mode
uv run oh ask "show me my ~/.ssh/id_rsa"

# Tier 2 lets you scope the harness to its project root
OPENHARNESS_DENY_PATHS="secrets/**,*.env" uv run oh ask "..."

# --dry-run lets you observe what tools the LLM would call, zero side effect
uv run oh ask --dry-run "edit my README to add a license header"
```

#### Hooks — middleware for the dispatch loop

5 lifecycle events (`PreToolUse` / `PostToolUse` / `PreApiCall` /
`PostApiCall` / `OnError`) let users plug in observability / cost
tracking / content moderation / memory injection without touching the
engine. Registration is programmatic in Phase 3; plugin discovery lands
in Phase 5.

```python
from openharness.hooks import HookRegistry, HookResult, PreToolUseContext

registry = HookRegistry()

async def cost_track(ctx):
    if isinstance(ctx, PreToolUseContext):
        print(f"about to call {ctx.tool_name}")
    return None  # observe-only, no decision

registry.register("PreToolUse", cost_track)
# Pass `registry` into QueryContext when constructing your harness.
```

Chain semantics: **modify accumulates, first-deny-wins** (Express-style).
See [`src/openharness/hooks/executor.py`](./src/openharness/hooks/executor.py)
for the algorithm + 10 micro-decisions in commit `c69ef4c`.

#### Observability — structured logs + 3-ID trace

```bash
# Default WARNING — terminal stays quiet
uv run oh ask "hello"

# Turn on INFO trace
uv run oh ask --log-level INFO "explain async iterators"

# JSONL on stderr for jq / OTel exporter consumption
uv run oh ask --log-level INFO --log-format json "..." 2> trace.jsonl
cat trace.jsonl | jq -c '{event, run_id, turn_id, tool_use_id}'
```

8 log points: `turn_start` / `tool_dispatch` / `tool_complete` /
`loop_limit_exceeded` / `retry` / `permission_denied` / `hook_invoke` /
`hook_failed`. Every record carries `run_id` (the trace ID) + `turn_id`
when in scope. Sanitize processor auto-redacts credentials by key name
(`api_key` / `password` / ...) and value patterns (`sk-...` / GitHub
PAT / AWS Access Key / JWT). Path / command fields are reduced to
cwd-relative paths or first-token + length.

Logs go to **stderr** — `stdout` stays clean for the LLM response, so
pipe-friendly:

```bash
oh ask --log-format json "..." > answer.txt 2> trace.jsonl
```

### Phase 4 features — context management (compaction)

Long conversations + large tool outputs eventually blow the model's
context window. Phase 4 ships two layers of defense (full design in
[`decisions/10-phase-4-boundary.md`](./decisions/10-phase-4-boundary.md)):

- **Layer 1 — per-tool-result truncation** (proactive). When a tool's
  output exceeds `--tool-result-cap` (default 10000 tokens), the output
  is head/tail truncated with a marker (Codex-style: the start and end
  of a tool's output usually carry the LLM-actionable signal; the middle
  is often bulk). Implemented as a default `PostToolUse` hook so it
  dogfoods the Phase 3 hook system.
- **Layer 2 — reactive prompt-too-long retry** (engine-internal). On a
  provider 400 with a "context length exceeded"-style message, the engine
  drops the oldest tool_use/tool_result pair from `messages` and retries
  the same turn. Bounded to 3 retries before re-raising.

```bash
# Defaults: 10k cap on each tool_result, Layer 1 on, Layer 2 always on
uv run oh ask "use Read to scan all source files and summarize"

# Smaller cap — useful for short-context models or aggressive control
uv run oh ask --tool-result-cap 2000 "..."

# Disable Layer 1; rely only on Layer 2 reactive recovery
uv run oh ask --no-auto-truncate "..."

# Both env-var forms:
OPENHARNESS_TOOL_RESULT_CAP=5000 OPENHARNESS_AUTO_TRUNCATE=true oh ask "..."
```

Two extra log events on top of the Phase 3 inventory:

- `tool_truncated` (info) — Layer 1 fired on this `tool_use_id`; carries
  `original_tokens` / `truncated_tokens` / `cap_tokens`.
- `reactive_truncate` (warning) — Layer 2 fired on this turn; carries
  `attempt` / `dropped_count` (how many messages were trimmed).

### Phase 5c features — Skills (lazy-loaded expertise)

Skills let you capture domain expertise as markdown files that the LLM
loads on demand — same shape as Claude Code Skills. Full design in
[`decisions/12-phase-5c-skills-boundary.md`](./decisions/12-phase-5c-skills-boundary.md);
the deep first-principles framing in
[`tasks/phase-5c-skills-preview.md`](./tasks/phase-5c-skills-preview.md).

**Authoring a skill** — drop a markdown file with YAML frontmatter into
one of two layers (project overrides global):

- Global: `~/.openharness/skills/<name>.md`
- Project: `<project-root>/.openharness/skills/<name>.md`

```markdown
---
name: react-testing
description: When to write React component tests and what patterns to use
---

When writing tests for React components, follow these principles:
1. Test behavior through user interactions, not implementation.
2. ...
```

**How the LLM uses it** — at CLI bootstrap the harness scans both
directories and injects a catalog (names + descriptions only) into the
system prompt:

```
## Available Skills (call LoadSkill to expand)

- **react-testing** -- When to write React component tests and what patterns to use
- **sql-tuning** -- Postgres performance tuning playbook
```

When the user's task matches a description, the LLM calls
`LoadSkill(name="react-testing")` like any other tool. The harness reads
the markdown body, strips the frontmatter, returns it as a `tool_result`
block. The LLM uses the loaded guidance in the next turn.

This is the **Index → Lookup → Content → Recurse** pattern that also
underlies MCP / RAG / Memory — the same `LLM + tool-call` machinery
applied to "external knowledge lazy-load". Phase 5c verified the third
tenant of Phase 3's cross-cutting invariant: `permissions/` / `hooks/` /
`engine/` / `observability/` show **zero diff** vs. pre-Skills state.

```bash
# Skills auto-discovered when you run `oh ask`:
mkdir -p .openharness/skills
cat > .openharness/skills/test-helper.md <<'EOF'
---
name: test-helper
description: Guidance for writing tests that don't flake
---
Always use stub clocks, not time.sleep().
EOF

uv run oh ask "help me write a flake-free test"
# → LLM sees the 'test-helper' skill in its catalog and may call
#   LoadSkill(name="test-helper") to expand it before answering.

# Disable Skills entirely (testing / debug):
uv run oh ask --no-skills "..."
```

### Phase 5b features — Slash Commands (user-facing UX shortcuts)

Slash commands are markdown templates the user invokes with `/cmd args`
to save typing on repeated workflows. Full design in
[`decisions/14-phase-5b-boundary.md`](./decisions/14-phase-5b-boundary.md).

**Authoring a command** — drop a markdown file with YAML frontmatter
into one of two layers (project overrides global, same convention as
Skills and git config):

- Global: `~/.openharness/commands/<name>.md`
- Project: `<project-root>/.openharness/commands/<name>.md`

```markdown
---
name: review
description: Review pending changes for correctness + security
---
Please review the following changes:

{args}

Focus on edge cases and security implications.
```

**How it works** — the user types `oh ask "/review last commit"`. The
CLI parses the leading `/`, looks up `review.md`, substitutes
`{args}` → `last commit`, and the resolved body becomes the user
message sent to the LLM. From `run_query`'s perspective, no slash
command exists — it's a pure CLI input transformation that vanishes
before the agent loop sees the prompt.

```bash
# Author once, reuse forever:
mkdir -p .openharness/commands
cat > .openharness/commands/review.md <<'EOF'
---
name: review
description: Review pending changes
---
Please review:

{args}

Focus on edge cases.
EOF

uv run oh ask "/review last 3 commits"
# → LLM receives "Please review:\n\nlast 3 commits\n\nFocus on edge cases."
```

**Args placement rules**:

- Body contains `{args}` → substituted in place (including empty args).
- Body has no `{args}` placeholder + non-empty args → appended on a
  new line at end of body (args never silently vanish).
- `oh ask "/cmd"` (no args) → `{args}` substituted with empty string.

**Unknown command error**:

```bash
$ oh ask "/nonexistent something"
Unknown command: no slash command named 'nonexistent'; available commands: review
```

Exit code 1, no LLM call attempted, catalog of available names surfaces
so you can pick the right one.

**Escape hatch** — `--no-commands` for prompts that legitimately start
with `/`:

```bash
uv run oh ask --no-commands "/path/to/file what's wrong here?"
# → slash prefix flows verbatim to LLM as user message
```

**Commands vs Skills** — the role split is load-bearing:

| | Skills (Phase 5c) | Commands (Phase 5b) |
|---|---|---|
| Audience | LLM-facing knowledge | User-facing UX shortcut |
| Trigger | LLM calls `LoadSkill(name)` | User types `/<name> args` |
| When resolved | Mid-conversation, lazy | Pre-LLM, in `cli.py` |
| Catalog visibility | Injected into system prompt | None — LLM doesn't see commands |
| Affects LLM behavior | Yes (loaded body in tool_result) | Yes (different user message) |

Phase 5b is the **fourth tenant test** of the Phase 3 cross-cutting
invariant. Slash commands don't touch `permissions/`, `hooks/`,
`engine/`, `observability/`, `prompts/`, or `tools/` — they live
entirely in `commands/` + `cli.py`. Formal structural test in
[`tests/commands/test_e2e.py`](./tests/commands/test_e2e.py)
introspects 9 protected modules.

### Phase 6 features — Sub-agent (recursive tool dispatch)

Sub-agent (the `Agent` tool) lets the LLM delegate a sub-task to a fresh
agent loop with isolated conversation context. The parent's conversation
grows by exactly one `tool_use` / `tool_result` pair regardless of
how many turns the sub-agent took — so a 50-turn research detour
doesn't balloon the parent's token budget.

Full design in
[`decisions/13-phase-6-boundary.md`](./decisions/13-phase-6-boundary.md).
The conceptual insight: **tool dispatch is the LLM's syscall interface,
and the agent loop itself is one of the syscalls**. Sub-agent isn't a
new mechanism — it's the recursive application of the primitive the
harness already owns. ``run_query`` invokes itself through a single
``BaseTool``, with no dispatch-side code knowing it's recursion.

**How the LLM uses it** — the `Agent` tool is registered by default
(catalog visible in the system prompt). The LLM calls it like any
other tool:

```python
# LLM emits:
tool_use(
    name="Agent",
    input={
        "description": "research async patterns",
        "prompt": "Survey common async patterns in this codebase and summarize.",
    },
)
```

The sub-agent receives `prompt` as its initial user message, runs
independently through tool dispatch with its own turn budget, and
returns its final text as the `tool_result`. The parent's LLM then
continues with that one result in context.

```bash
# Bounded recursion — default 3 levels deep (supervisor → research → leaf):
uv run oh ask "Use the Agent tool to count words in foo.txt and summarize the findings."

# Override depth bound:
OPENHARNESS_MAX_AGENT_DEPTH=5 uv run oh ask "..."

# Disable spawning entirely (kill-switch):
OPENHARNESS_MAX_AGENT_DEPTH=0 uv run oh ask "..."
# → Any `Agent` invocation returns is_error=True with "max agent depth (0) reached".
```

**Trace stitching** — sub-agent log events carry two new fields:

- `parent_run_id` — points at the immediate parent's `run_id`
- `agent_depth` — 0 for top-level, +1 per nesting level

JSONL consumers can self-join `run_id ↔ parent_run_id` to reconstruct
the parent/sub-agent tree:

```
$ uv run oh ask --log-format json "..." 2> trace.jsonl
$ jq -c 'select(.agent_depth > 0)' trace.jsonl
{"event":"turn_start","run_id":"R2","parent_run_id":"R1","agent_depth":1,...}
```

**Cross-cutting invariant verified (third tenant test)** — Phase 6
landed without any change to `permissions/`, `hooks/`, `mcp/`,
`compaction/`, or `protocols/`. The engine dispatch loop gained exactly
3 additive code lines:

```python
from openharness.observability import bind_agent_depth        # +1 import
with bind_run(), bind_agent_depth(context.agent_depth):        # +1 line
exec_context = ToolExecutionContext(cwd=..., parent_query=context)  # +1 kwarg
```

The recursion lives entirely inside `SpawnAgent.execute`. Structural
test
[`tests/tools/test_spawn_agent_invariant.py`](./tests/tools/test_spawn_agent_invariant.py)
introspects 9 protected modules to confirm no leak.

### Phase 7a features — ExecutionEnvironment abstraction (substrate layer)

The harness now has an explicit **substrate layer** —``BashTool``
no longer hard-codes ``asyncio.create_subprocess_shell`` calls;it
delegates to the configured :class:`ExecutionEnvironment` on the
query context. Default is ``HostExecution`` (current behavior); Phase
7b will plug in a ``SandboxExecution`` (Docker container) without
changing ``BashTool``.

Full design in
[`decisions/15-phase-7-boundary.md`](./decisions/15-phase-7-boundary.md).
The conceptual lift:**"where does this tool run" becomes an
injectable dependency**. Same shape as Phase 5a MCP (where the tool
runs) and Phase 6 Sub-agent (recursive agent loop as a tool) — every
extension lands on the same `BaseTool → ToolRegistry → dispatch loop`
primitive.

**For users** — Phase 7a is invisible at the CLI surface. `oh ask "..."`
behavior is byte-identical to before:

```bash
uv run oh ask "list files in this dir using bash"
```

Internally, the engine populates
`ToolExecutionContext.execution_env=context.execution_env`. ``BashTool``
reads that field, calls
`env.run_command(command=args.command, cwd=ctx.cwd, timeout=...)`, and
translates the returned :class:`ProcessResult` into a
`ToolResult`. All 13 existing BashTool tests pass unchanged after the
refactor — behavior parity is the load-bearing assertion.

**For framework developers** — to inject a custom substrate:

```python
from openharness.engine import QueryContext
from openharness.execution import ExecutionEnvironment, ProcessResult


class MyExecution:
    async def run_command(
        self, command: str, cwd, timeout=None
    ) -> ProcessResult:
        # ... whatever (remote worker pool / gVisor / Firecracker / ...)
        return ProcessResult(output="...", exit_code=0)


env: ExecutionEnvironment = MyExecution()
ctx = QueryContext(
    ...,
    execution_env=env,  # all Bash dispatches route through MyExecution
)
```

Sub-agents inherit the parent's `execution_env` via `dataclasses.replace`
automatically — a parent sandboxed sub-agent transparently keeps the
sandbox.

**Cross-cutting invariant verified (fourth tenant test)** — Phase 7a
landed with **zero diff** on `permissions/`, `hooks/`,
`observability/`, `mcp/`, `compaction/`, `skills/`, `commands/`,
`protocols/`, and all other tool modules. `engine/query.py` gained
exactly one additive kwarg on the existing
`ToolExecutionContext(...)` call. Structural test in
[`tests/execution/test_invariant.py`](./tests/execution/test_invariant.py)
asserts no `ExecutionEnvironment` / `HostExecution` / `ProcessResult` /
`_HOST_EXECUTION` identifier leaks into any of the 22 protected
modules.

**Phase 7b** lands the real Docker substrate(`SandboxExecution`)as a
second `ExecutionEnvironment` implementation — see the "Phase 7b"
section below.

### Phase 7b features — Docker sandbox (real execution isolation)

`SandboxExecution`(Phase 7b)takes the abstraction Phase 7a established
and plugs in a real Linux-namespace-isolated substrate via Docker.
``BashTool``'s code didn't change — it just sees a different
``ExecutionEnvironment`` on ``QueryContext``. Full design in
[`decisions/16-phase-7b-boundary.md`](./decisions/16-phase-7b-boundary.md).

**Default behavior(no Docker)**:

```bash
uv run oh ask "list files via bash"
# → BashTool delegates to HostExecution (current behavior, no changes)
```

**Enable Docker sandbox**:

```bash
uv run oh ask --sandbox "use bash to count files in this dir"
# → BashTool delegates to SandboxExecution (container)
```

**What the sandbox isolates**:

- **Filesystem**: only the project cwd is bind-mounted (read-write)
  to ``/workspace`` inside the container. The host's ``/etc``,
  ``~/.ssh``, ``~/.aws``, ``/Users/...`` are **structurally absent**
  from the container's mount namespace — defense in depth via the
  kernel, not via permission checks. ``Bash("cat /etc/passwd")``
  doesn't return the host's passwd file; it returns the container
  base image's (which is harmless).
- **Network**: default ``--sandbox-network=none`` blocks all external
  network — blocks classic prompt-injection exfiltration attempts.
  Opt-in via ``--sandbox-network=bridge`` for npm install / git clone.
- **Resources**: cgroup-bounded by default (1GB memory / 1 CPU / 256
  processes). Fork bombs and OOM-amok scripts get kernel-killed, not
  the harness.

**Configuration** (CLI flags / env vars):

```bash
# Tune limits + image:
uv run oh ask --sandbox \
  --sandbox-memory 512m \
  --sandbox-cpus 0.5 \
  --sandbox-image ubuntu:latest \
  --sandbox-network bridge \
  "..."

# Or via env (persists across invocations):
export OPENHARNESS_SANDBOX_ENABLED=true
export OPENHARNESS_SANDBOX_IMAGE=python:3.12-slim
export OPENHARNESS_SANDBOX_NETWORK=none
```

**Requirements**:

- Docker daemon running locally (macOS Docker Desktop; Linux native)
- Container image (default ``python:3.12-slim``, ~120MB) auto-pulled
  on first use
- macOS users:Docker Desktop's nested LinuxKit VM adds ~5-10s
  warm-up latency on first ``oh ask --sandbox`` of a session; subsequent
  invocations reuse the warm VM

**What's still on the host**(not sandboxed):

- ``Read`` / ``Write`` / ``Edit`` / ``Grep`` — path-pure tools
  already covered by Tier 1-3 permission checks
- ``LoadSkill`` — reads markdown files, no shell execution
- MCP tool dispatch — runs in remote MCP server process
- ``SpawnAgent`` — same agent loop, same substrate inherited via
  ``dataclasses.replace``

Only ``BashTool`` routes through the substrate — per D17.4 layered
extension model (only tools that need the cost pay it).

**Cross-cutting invariant** — Phase 7b again landed with zero diff on
``permissions/``, ``hooks/``, ``observability/``, ``mcp/``,
``compaction/``, ``skills/``, ``commands/``, ``engine/``, ``tools/``,
``protocols/``, and even Phase 7a's `execution/base.py` + `execution/host.py`.
Only ``execution/sandbox.py`` (new) + `cli.py` (+1
`AsyncExitStack` block + 5 flags) + `config/settings.py` (+6 fields)
were touched. The Phase 7a abstraction's payoff: Phase 7b is **pure
plug-in work**.

### Phase 5d features — ModeBundle (the first cross-layer tenant)

Phase 5d ships **ModeBundle** — the first feature that composes
multiple existing layers (system prompt + tool catalog + permissions +
hooks) into one named "mode" the user can invoke via a slash command.

The bundle is a markdown file with YAML frontmatter declaring up to
four layer overrides:

```markdown
---
name: code-review
description: Read-only code review mode with audit logging
system_prompt: |
  You are a code reviewer. Focus on correctness, readability, security.
  Never modify files.
tools:
  whitelist: [Read, Grep, LoadSkill]
deny_paths:
  - secrets/**
  - "*.env"
hooks:
  - audit_log
  - deny_writes
---
```

Stored under ``~/.openharness/bundles/`` (global) or
``<cwd>/.openharness/bundles/`` (project). Project overrides global on
the same name — same two-layer convention as Skills (5c) and Commands
(5b).

**How to trigger one** — a slash command's frontmatter references the
bundle via a ``mode:`` field:

```markdown
---
name: review
description: Code review mode (read-only)
mode: code-review
---
Review the following changes:

{args}
```

When the user runs ``oh ask "/review last 3 commits"``:

1. ``cli._run_ask`` resolves the slash command via
   ``resolve_command_invocation`` — gets the substituted prompt + the
   ``Command`` object.
2. If ``Command.mode`` is set, ``FilesystemBundleStore`` looks up the
   named bundle. Unknown bundle → ``UnknownBundleError`` → exit 1
   with "Unknown bundle: <name>; available: ..." stderr.
3. ``apply_bundle_to_context`` composes the 4 layers against the base
   primitives (already-built tool registry + hook registry + Settings):
   - **Layer 1 — system_prompt**: REPLACES base entirely if bundle
     specifies it; else base prompt is rebuilt against the EFFECTIVE
     tool registry so the catalog reflects any whitelist.
   - **Layer 2 — tool catalog**: ``WhitelistRegistry`` subclasses
     ``ToolRegistry`` and exposes only whitelisted tools. Engine sees
     it as a regular ``ToolRegistry`` — zero engine diff.
   - **Layer 3a — deny_paths**: AUGMENT (bundle's patterns appended
     to ``Settings.deny_paths``) — safer than replace. The
     ``TierBasedPermissionChecker`` reads ``settings.deny_paths``
     unchanged.
   - **Layer 3b — hook chain**: clone base ``HookRegistry`` + register
     bundle's named hooks. Bundle hooks fire AFTER user-registered
     hooks for the same event.
4. ``QueryContext`` is constructed with the effective primitives;
   engine runs unchanged.

**Built-in named hooks (Phase 5d MVP):**

- ``audit_log`` (``PostToolUse``) — emits
  ``event=audit_tool_complete`` with ``tool_name`` / ``tool_use_id`` /
  ``is_error`` / ``output_len``. Distinct from the framework's default
  ``tool_complete`` so a ``jq 'select(.event=="audit_tool_complete")'``
  filter isolates bundle-driven audit records for compliance trace.
- ``deny_writes`` (``PreToolUse``) — denies any tool whose
  ``is_read_only=False``. Belt-and-braces "read-only mode" so a
  whitelist typo can't silently grant write access. Looks up the tool
  via ``context.exec_context.parent_query.tool_registry``; passes
  through (instead of denying) when the tool isn't in the registry, to
  avoid masking the engine's own "tool not found" error.

User-supplied custom hooks via plugin discovery defer to Phase 5e.

**Cross-cutting invariant verified (fifth tenant test)** — Phase 5d
landed with **zero diff** on ``permissions/``, ``hooks/``, ``engine/``,
``observability/``, ``mcp/``, ``compaction/``, ``skills/``,
``protocols/``, ``tools/``, ``execution/``, ``prompts.py`` vs Phase
7b close. The only diffs are: ``commands/model.py`` (+1 additive
``mode`` field), ``commands/expand.py`` (+1 new function), ``cli.py``
(bootstrap chain + 1 except arm), and the new ``bundles/`` package
itself. **Why this matters**: every prior phase (5a/5b/5c/6/7a/7b)
extended ONE axis at a time — bundles compose FOUR axes
simultaneously. The cross-layer composition working without modifying
any layer is the strongest single proof that Phase 3's layered model
holds under real cross-cutting load. See ``learnings/phase-5d.md`` for
the retrospective.

### Phase 5e features — plugin hook discovery (third-party named hooks)

Phase 5d shipped 2 framework-built-in named hooks (``audit_log`` +
``deny_writes``) that bundle frontmatter references by string. Phase
5e generalizes that catalog so **third-party Python packages can
ship hooks** via the ``openharness.hooks`` entry-point group —
bundle frontmatter then references them with the same `hooks:
[name1, name2]` shape, no code changes required.

**Plugin author workflow** — author a Python package:

```python
# my_pkg/hooks.py
from openharness.bundles import hook_spec

@hook_spec("PostToolUse")
async def slack_notify(context):
    """Notify the team Slack channel when a tool dispatch completes."""
    # send Slack webhook payload
    ...

@hook_spec("PreApiCall")
async def budget_guard(context):
    """Deny API calls when the daily cost budget is exceeded."""
    ...
```

```toml
# pyproject.toml
[project.entry-points."openharness.hooks"]
slack_notify = "my_pkg.hooks:slack_notify"
budget_guard = "my_pkg.hooks:budget_guard"
```

After `pip install my_pkg`, the hooks are available to any bundle
that references them by name — provided the end-user opts in.

**End-user enable flow** — discovery is **opt-in** (default OFF):

```bash
# Enable via CLI flag
oh ask --enable-plugin-hooks "/review last commit"

# Or via env var
OPENHARNESS_ENABLE_PLUGIN_HOOKS=true oh ask "/review last commit"
```

The bundle's frontmatter references the plugin hook by name:

```yaml
---
name: code-review
description: Read-only code review with audit logging + Slack notify
system_prompt: |
  You are a code reviewer. Read-only mode.
tools:
  whitelist: [Read, Grep]
hooks:
  - audit_log         # framework built-in
  - deny_writes       # framework built-in
  - slack_notify      # plugin from my_pkg
  - budget_guard      # plugin from my_pkg
---
```

**Collision policy** — framework > plugins > error:

1. **Plugin name collides with built-in** (e.g. plugin tries to
   register as `audit_log`) → plugin skipped + warning logged
   (`plugin_hook_collides_with_builtin`). Framework hooks are
   documented + version-stable; a plugin can't silently override
   compliance-critical hooks.
2. **Plugin name collides with another plugin** → first-wins (entry-
   point iteration order is generally install order) + warning.
3. **Plugin load error** (import fail, wrong type, exception) →
   skipped + warning. Same skip-not-fail discipline as `parse_*`
   functions.

**Security model** — opt-in by design:

- Default OFF: even if plugin packages are installed, hooks are not
  loaded. Users must explicitly turn on discovery.
- Plugin hooks can DENY or MODIFY any tool call — too large a blast
  radius for default ON.
- Matches `--sandbox` (Phase 7b) opt-in shape: features that affect
  authorization or execution surface require explicit consent.

**Cross-cutting invariant verified** — Phase 5e landed with **zero
diff** vs Phase 5d close on `permissions/`, `hooks/`, `engine/`,
`observability/`, `mcp/`, `compaction/`, `skills/`, `commands/`,
`protocols/`, `tools/`, `execution/`, and `bundles/{model,store,
registry,errors}.py`. Only additive diffs: new
`bundles/hook_plugins.py` (190 LoC), additive kwargs in
`bundles/hooks.py` (`resolve_hook` + `plugin_catalog`) and
`bundles/apply.py` (`apply_bundle_to_context` + `plugin_hook_catalog`),
CLI flag + bootstrap (32 LoC), and `Settings.enable_plugin_hooks`
field. **Phase 5e is extension WITHIN the bundle subsystem** — it
adds a catalog source without inventing a new lookup path or
modifying any layer. See `learnings/phase-5e.md`.

### Want to verify the wire path against your account?

```bash
# Runs the gated integration test (skipped when env vars aren't set)
uv run pytest -m integration
```

---

## Development workflow

```bash
# Lint + format
uv run ruff check
uv run ruff format

# Type check (mypy strict mode)
uv run mypy --strict src/

# Tests with coverage
uv run pytest

# Install pre-commit hooks (one-time)
uv run pre-commit install

# Manually run all hooks
uv run pre-commit run --all-files
```

---

## Project structure

```
.
├── SPEC.md                   # Project specification (objective / commands / structure / style / testing / boundaries)
├── ARCHITECTURE.md           # Multi-phase strategy (tiers, dependency graph, phase ordering)
├── REFERENCE.md              # Reverse-engineered OpenHarness reference (study source, read-only)
├── pyproject.toml            # Single source of truth: deps, ruff, mypy, pytest
├── decisions/                # Decision records: trade-offs + rationale per module
├── learnings/                # Per-module retrospectives (Python patterns + product decisions)
├── tasks/plan.md             # Current phase plan
├── tasks/todo.md             # Running task list
├── docs/ideas/               # Blog drafts and ideation outputs
├── docs/learning/            # Living learning resources (book lists, etc.)
├── src/openharness/          # Source (src layout)
│   ├── __init__.py           # Top-level re-exports (Settings, __version__)
│   ├── __main__.py           # `python -m openharness` entry
│   ├── cli.py                # CLI: Typer `oh ask` command + error UX (P1-T4)
│   ├── _stream_render.py     # Append-only ApiStreamEvent → terminal renderer
│   ├── config/               # pydantic-settings layer (OPENHARNESS_*) — P1-T4 4a
│   ├── protocols/            # Pydantic v2 wire types (Anthropic-shape) — P1-T2
│   └── api/                  # Provider clients + retry + translation — P1-T3
│       ├── client.py         # OpenAICompatibleApiClient (Qwen / OpenAI / etc.)
│       ├── translation.py    # Anthropic ↔ OpenAI wire translation
│       ├── retry.py          # Exponential backoff + jitter
│       └── errors.py         # OpenHarnessApiError hierarchy
├── tests/                    # pytest suite (asyncio_mode = auto)
│   ├── protocols/, api/, config/, cli/  # mirrors src/ layout
│   └── conftest.py           # Shared fixtures (env-var carve-outs)
├── .github/workflows/ci.yml  # Lint + type-check + test on Python 3.10 / 3.11
└── .pre-commit-config.yaml   # Fast hooks only (ruff + hygiene)
```

---

## Design decisions at a glance

| Concern | Choice | See |
|---------|--------|-----|
| Build / package mgmt | `uv` + `hatchling` | [decisions/01-scaffolding.md](./decisions/01-scaffolding.md) |
| Lint + format | `ruff` (replaces flake8/black/isort) | ↑ |
| Type checking | `mypy --strict` | ↑ |
| Test framework | `pytest` + `pytest-asyncio` + `pytest-cov` | ↑ |
| Pre-commit | enabled, **ruff only** (mypy/pytest in CI) | ↑ |
| CI | GitHub Actions, matrix Python 3.10 / 3.11 | [.github/workflows/ci.yml](./.github/workflows/ci.yml) |

---

## License

MIT — see [LICENSE](./LICENSE).
