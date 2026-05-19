# OpenHarness

[![CI](https://github.com/yangxiyue/build-my-own-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/yangxiyue/build-my-own-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **A production-grade Python harness for LLM agents — built from scratch as a learning project.**

OpenHarness is a Claude-Code-style agent harness: you give it a prompt,
it talks to an LLM, the LLM picks tools, the harness runs them safely,
the loop continues until the LLM says it's done. Everything you'd
expect from a serious agent runtime — tool dispatch, permission checks,
hook middleware, structured logging, sandbox execution, slash commands,
plugin hooks, multi-turn REPL — and nothing you wouldn't.

The codebase is the documentation: 18 subsystems, 22 decision records,
28 per-phase retrospectives, 1240 tests at 97%+ coverage, mypy strict
throughout. Each `learnings/phase-*.md` explains both the framework
decisions and the Python patterns that made them work.

---

## Quickstart

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone + sync
git clone https://github.com/yangxiyue/build-my-own-harness.git
cd build-my-own-harness
uv sync

# 3. Set two env vars (Qwen via DashScope is the default test target;
#    swap base_url for OpenAI / DeepSeek / Moonshot / any OpenAI-
#    compatible endpoint)
export OPENHARNESS_API_KEY="sk-..."
export OPENHARNESS_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"

# 4. Ask away
uv run oh ask "list 5 git commands"

# Or open the REPL for multi-turn:
uv run oh chat
```

Errors come back differentiated — no Python tracebacks in default mode:

| Situation | What you see |
|---|---|
| `OPENHARNESS_API_KEY` not set | `Configuration error` + hint |
| Wrong key | `Authentication failed (HTTP 401)` |
| Provider rate-limit | `Rate-limited after retries (HTTP 429)` |
| Loop hit `max_turns` | `Loop error: loop hit turn limit (N); raise --max-turns or simplify the prompt` |

---

## Key features

Each links to its per-phase development log in
[`docs/development-log.md`](./docs/development-log.md) for the
framework-design narrative, and to `learnings/phase-*.md` for the
framework-builder retrospective.

- **Streaming tool loop** — the engine's heart. `run_query()` is an
  `AsyncIterator[ApiStreamEvent]` that the LLM drives by emitting
  `tool_use` blocks; the harness dispatches the tool, feeds the
  result back, and loops until `end_turn`. ([dev-log → Tool Loop](./docs/development-log.md))
- **5 built-in tools** — `Read` / `Write` / `Edit` / `Bash` / `Grep`,
  with Pydantic-validated inputs + structured `ToolResult` outputs.
- **Three-tier permission system** — hardcoded sensitive-path deny,
  user-configurable glob deny (`OPENHARNESS_DENY_PATHS`), and
  permission-mode override (`--auto` / `--dry-run`).
- **Hook middleware** — 5 lifecycle events (`PreToolUse`,
  `PostToolUse`, `PreApiCall`, `PostApiCall`, `OnError`) with
  deny / modify / allow semantics. Used internally for auto-truncation;
  exposed for user plugins (entry points + filesystem `*.py`).
- **Structured observability** — JSON logs with `run_id` / `turn_id`
  / `agent_depth` context binding for trace reconstruction via `jq`.
- **MCP integration** — speak the Model Context Protocol (stdio
  transport) to register third-party tool servers.
- **Slash commands** — drop a markdown file at
  `~/.openharness/commands/<name>.md`, invoke as `oh ask "/<name> args"`.
- **Skills** — lazy-loaded expertise; the LLM sees a catalog and
  calls `LoadSkill` to expand specific entries on demand.
- **ModeBundle** — compose system prompt + tool whitelist + extra
  deny paths + named hooks into one named "mode" referenced from a
  slash command's `mode:` frontmatter.
- **Plugin hooks** — third-party Python packages can ship hooks via
  the `openharness.hooks` entry-point group; `.py` files dropped at
  `~/.openharness/hooks/` also discovered. Opt-in via
  `--enable-plugin-hooks`.
- **Sub-agent dispatch** — recursive `SpawnAgent` tool with depth
  limit; sub-agents inherit context immutably via `dataclasses.replace`.
- **Sandboxed execution** — Docker sandbox via `--sandbox` (Phase 7b)
  with selectable runtime via `--sandbox-runtime runc|runsc`
  (Phase 7c gVisor support).
- **Multi-turn REPL** — `oh chat` accumulates conversation history
  across turns via a new `ConversationCompleteEvent`. Built-in
  `/exit`, `/clear`, `/help`; user slash commands + bundles still work.
- **Compaction** — Layer 1 per-tool-result truncation via hook;
  Layer 2 reactive PromptTooLong retry by dropping the oldest
  tool-use/tool-result pair.

---

## CLI reference

Phase 7 ships these subcommands. More (`oh tools list`, `oh config
show`, `oh hooks list`) are slated for the v0.1 release —
see [`tasks/phase-7-final-plan.md`](./tasks/phase-7-final-plan.md).

```bash
oh ask "<prompt>"                  # Single-shot LLM query
oh ask "<prompt>" --model qwen-max # Override model
oh ask "<prompt>" --max-tokens 256 # Cap generation length
oh ask "<prompt>" --dry-run        # List tool calls without executing
oh ask "<prompt>" --auto           # Skip permission confirmations
oh ask "<prompt>" --sandbox        # Run Bash inside Docker container
oh ask "<prompt>" --sandbox --sandbox-runtime runsc  # gVisor isolation
oh ask "<prompt>" --enable-plugin-hooks              # Load plugin hooks

oh chat                            # Interactive multi-turn REPL
oh chat --sandbox                  # Same flags as `oh ask`

oh --version
oh --help
```

Inside `oh chat`, built-in slash commands: `/exit`, `/quit`,
`/clear` (reset history), `/help`. User-authored slash commands
(`/<name> args`) work the same as in `oh ask`.

---

## Configuration

All settings read from environment variables prefixed `OPENHARNESS_`
(via [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)).

| Env var | Default | Purpose |
|---|---|---|
| `OPENHARNESS_API_KEY` | (required) | Provider API key |
| `OPENHARNESS_BASE_URL` | (required) | OpenAI-compatible endpoint |
| `OPENHARNESS_MODEL` | `qwen-plus` | Default model |
| `OPENHARNESS_PERMISSION_MODE` | `default` | `default` / `auto` / `dry_run` |
| `OPENHARNESS_DENY_PATHS` | `()` | Comma-separated extra deny globs |
| `OPENHARNESS_LOG_LEVEL` | `WARNING` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `OPENHARNESS_LOG_FORMAT` | `console` | `console` or `json` |
| `OPENHARNESS_TOOL_RESULT_CAP` | `10000` | Layer-1 truncation token cap |
| `OPENHARNESS_AUTO_TRUNCATE` | `true` | Auto-register the cap hook |
| `OPENHARNESS_MAX_AGENT_DEPTH` | `3` | Sub-agent recursion cap |
| `OPENHARNESS_MCP_SERVERS` | `()` | MCP server config (JSON) |
| `OPENHARNESS_TRUSTED_MCP_SERVERS` | `()` | Comma-separated trusted server names |
| `OPENHARNESS_SANDBOX_ENABLED` | `false` | Default sandbox state |
| `OPENHARNESS_SANDBOX_IMAGE` | `python:3.12-slim` | Container image |
| `OPENHARNESS_SANDBOX_NETWORK` | `none` | `none` or `bridge` |
| `OPENHARNESS_SANDBOX_MEMORY` | `1g` | Memory limit |
| `OPENHARNESS_SANDBOX_CPUS` | `1.0` | CPU quota |
| `OPENHARNESS_SANDBOX_PIDS` | `256` | Process count limit |
| `OPENHARNESS_SANDBOX_RUNTIME` | `runc` | OCI runtime (`runc` / `runsc` / ...) |
| `OPENHARNESS_ENABLE_PLUGIN_HOOKS` | `false` | Discover entry-point + filesystem plugins |

A `.env` file at the repo root is loaded automatically. CLI flags
always override env vars; env vars always override defaults.

---

## Architecture at a glance

Three layered concerns, sliced vertically by phase:

1. **Engine** (`engine/`) — `run_query` is an async generator that
   streams `ApiStreamEvent`s. Per-turn: send messages to the API,
   handle `tool_use` stop reasons, dispatch tools, append results,
   loop until `end_turn`. Defensive immutability: caller's
   `initial_messages` is never mutated.
2. **Tools** (`tools/`) — `BaseTool` ABC with Pydantic-validated
   input schemas. `ToolRegistry` is the catalog the engine
   introspects. Permission check happens BEFORE dispatch via
   `permissions/checker.py`.
3. **Hooks** (`hooks/`) — middleware chain for the 5 lifecycle
   events. Hooks can deny / modify / observe. The hook chain is
   the extension point: Phase 4's compaction, Phase 5d's bundles,
   Phase 5e/5f's plugins all hang off it.

For the full tier division, dependency graph, and design rationale,
see [ARCHITECTURE.md](./ARCHITECTURE.md). For per-decision trade-off
analysis, see [`decisions/`](./decisions). For framework-builder
retrospectives, see [`learnings/`](./learnings).

---

## Project structure

```
.
├── SPEC.md                   # Project contract (objective / commands / boundaries)
├── ARCHITECTURE.md           # Multi-phase strategy (tiers, dependency graph)
├── REFERENCE.md              # Reverse-engineered OpenHarness reference
├── pyproject.toml            # Single source of truth (deps, ruff, mypy, pytest)
├── decisions/                # 22 decision records (per-trade-off)
├── learnings/                # 28 per-phase retrospectives
├── tasks/                    # Per-phase boundary docs + implementation plans
├── docs/
│   ├── development-log.md    # Per-phase feature narratives (READ FOR HISTORY)
│   ├── tutorial.md           # Walked-through scenarios (Phase 7 T4)
│   └── ideas/, learning/     # Drafts + living learning resources
├── examples/                 # Sample commands / skills / bundles / hooks (Phase 7 T4)
├── src/openharness/          # 18 subsystems
│   ├── api/                  # OpenAI-compatible client + retry + translation
│   ├── bundles/              # Phase 5d ModeBundle + 5e/5f plugin hooks
│   ├── cli.py                # Typer command surface (oh ask / oh chat / ...)
│   ├── commands/             # Phase 5b slash commands
│   ├── compaction/           # Phase 4 truncation hooks
│   ├── config/               # pydantic-settings layer (OPENHARNESS_*)
│   ├── engine/               # run_query + tool dispatch loop
│   ├── execution/            # Phase 7a substrate abstraction + 7b/7c sandbox
│   ├── hooks/                # Phase 3 middleware (5 events)
│   ├── markdown_store/       # Phase 8 shared parse + filesystem store
│   ├── mcp/                  # Phase 5 Model Context Protocol adapters
│   ├── observability/        # Phase 3 structured logging + 3-ID trace
│   ├── permissions/          # Phase 3 three-tier authz checker
│   ├── prompts.py            # Phase 2 system prompt assembly
│   ├── protocols/            # Phase 1 Pydantic v2 wire types (Anthropic-shape)
│   ├── skills/               # Phase 5c lazy-loaded expertise
│   └── tools/                # Phase 2 tool registry + 5 built-in tools
├── tests/                    # ~1240 tests mirroring src/ layout
├── .github/workflows/ci.yml  # Lint + type-check + test on Python 3.10/3.11
└── .pre-commit-config.yaml   # Fast hooks only (ruff + hygiene)
```

---

## Development

```bash
# Lint + format
uv run ruff check
uv run ruff format

# Type check (mypy strict mode)
uv run mypy --strict src/

# Tests with coverage
uv run pytest

# Install pre-commit hooks (one-time on fresh clone)
uv run pre-commit install

# Manually run all hooks
uv run pre-commit run --all-files

# Real-LLM smoke test (gated on env vars; skipped in CI)
uv run pytest -m integration

# Docker sandbox smoke (gated on `docker info`)
uv run pytest tests/execution/test_sandbox_integration.py
```

CI runs lint + type-check + full test suite on Python 3.10 and 3.11
via [`.github/workflows/ci.yml`](./.github/workflows/ci.yml).
Integration tests skip when env vars / Docker / gVisor aren't
available — `tests/` always passes cleanly without external deps.

---

## Design decisions at a glance

| Concern | Choice | Rationale |
|---|---|---|
| Build / package mgmt | `uv` + `hatchling` | [`decisions/01-scaffolding.md`](./decisions/01-scaffolding.md) |
| Lint + format | `ruff` (replaces flake8/black/isort/pyupgrade) | ↑ |
| Type checking | `mypy --strict` everywhere | ↑ |
| Wire type modeling | Pydantic v2 with `extra="forbid"` | [`decisions/02-protocols.md`](./decisions/02-protocols.md) |
| First Provider | Qwen via DashScope (OpenAI-compatible) | [`decisions/03-api-client-strategy.md`](./decisions/03-api-client-strategy.md) |
| Tool dispatch | Serial within a turn (D6.3) | [`decisions/06-phase-2-boundary.md`](./decisions/06-phase-2-boundary.md) |
| Permission model | 3-tier (hardcoded + glob + mode) | [`decisions/08-phase-3-boundary.md`](./decisions/08-phase-3-boundary.md) |
| Sandbox substrate | Protocol-based; `runc` default, `runsc` opt-in | [`decisions/15-phase-7-boundary.md`](./decisions/15-phase-7-boundary.md), [`decisions/21-phase-7c-boundary.md`](./decisions/21-phase-7c-boundary.md) |
| Bundle composition | Pre-LLM resolution; engine zero-diff | [`decisions/17-phase-5d-boundary.md`](./decisions/17-phase-5d-boundary.md) |
| Plugin discovery | Entry points (5e) + `.py` files (5f), opt-in | [`decisions/18-phase-5e-boundary.md`](./decisions/18-phase-5e-boundary.md), [`decisions/20-phase-5f-boundary.md`](./decisions/20-phase-5f-boundary.md) |

Full decision index: [`decisions/`](./decisions) (22 docs).

---

## What's next

Phase 7 (this phase) closes the SPEC v1 boundary — see
[`decisions/23-phase-7-final-boundary.md`](./decisions/23-phase-7-final-boundary.md)
and [`tasks/phase-7-final-plan.md`](./tasks/phase-7-final-plan.md).

Deferred to Phase 8+:

- **Anthropic native client** (`AnthropicApiClient` — protocols/ is
  already Anthropic-shape)
- **LLM auto-compaction Layer 3** (turn-summarization for long sessions)
- **Memory system** (YAML-frontmatter `~/.openharness/memory/`)
- **Keyring auth + multi-profile** API key management
- `oh mcp add/list`, `oh skill run` subcommands
- REPL polish (`/save`, `/load`, multi-line input)
- Firecracker substrate (microVM isolation)

See `decisions/23-phase-7-final-boundary.md` §6 for the full deferred
list with rationale per item.

---

## License

MIT — see [LICENSE](./LICENSE) (lands in Phase 7 T3 alongside the
PyPI artifact).
