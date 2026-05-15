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
