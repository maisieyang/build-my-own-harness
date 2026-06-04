# OpenHarness

[![CI](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **A production-grade Python harness for LLM agents — and a documented case study in framework design.**
> **v0.1.0 shipped in 23 days, single developer + Claude Code.** Continues to evolve in versioned releases; each phase preserves boundary doc → plan → retro in this repo.

OpenHarness is a Claude-Code-style agent harness: you give it a prompt,
it talks to an LLM, the LLM picks tools, the harness runs them safely,
the loop continues until the LLM says it's done. Everything you'd
expect from a serious agent runtime — tool dispatch, three-tier
permissions, hook middleware, structured observability, sandboxed
execution, slash commands, plugin hooks, recursive sub-agents,
multi-turn REPL — and nothing you wouldn't.

It's **also** a deliberate learning artifact. The repo preserves every
boundary-doc decision (`decisions/`), every per-phase retrospective
(`learnings/`), and the full plan/execute trail (`tasks/`) — so you
can read **not just what was built, but why each trade-off was made
and what the next phase predicted before being built**.

---

## Four entry points

- 👉 **Want to use it as a harness?** → jump to [Quickstart](#quickstart)
- 📖 **Engineer — want to learn how it was built / apply the methodology?** → [**PLAYBOOK.md**](./PLAYBOOK.md) (4-step methodology + architecture + 5 framework lessons + 3 anti-patterns;~6500 字 中文)
- 🎯 **PM — want LLM/Agent product decision frameworks + cross-role collab playbook?** → [**PLAYBOOK-PM.md**](./PLAYBOOK-PM.md) (18,500 字 中文,8 个 Part:harness 产品视角 / 11 个真实产品决策 / LLM 机制速通 / 评测灰度 A/B / 跨角色协作 / 5 个反面案例,带 DeepSeek JD 技术词清单对照)
- 🏗️ **Want to fork / contribute?** → [`ARCHITECTURE.md`](./ARCHITECTURE.md) (tier map) + [`SPEC.md`](./SPEC.md) (project contract) + [`docs/development-log.md`](./docs/development-log.md) (per-phase narrative)

---

## At a glance (v0.1.0 baseline)

| | |
|---|---|
| Released | 2026-05-20 — first public release / case-study writeup |
| Built in | **23 days** (single developer + Claude Code as collaborator) |
| Phase loop | boundary-doc → plan → execute → retro, preserved per phase |
| Trail | [`decisions/`](./decisions) (per-trade-off rationale) · [`tasks/`](./tasks) (capability plans) · [`learnings/`](./learnings) (per-phase retros + framing essays) |
| Quality bars | mypy strict · ruff clean · ≥95% coverage gate (Python 3.10/3.11) |
| Per-release stats | See [CHANGELOG.md](./CHANGELOG.md) for test counts / coverage / LoC per version |

---

## Quickstart

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone + sync
git clone https://github.com/maisieyang/build-my-own-harness.git
cd build-my-own-harness
uv sync

# 3. Set up your provider (any OpenAI-compatible endpoint works:
#    Qwen via DashScope is the default test target; swap base_url
#    for OpenAI / DeepSeek / Moonshot / etc.)
cp .env.example .env
$EDITOR .env                                  # fill in OPENHARNESS_API_KEY

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
  result back, and loops until `end_turn`.
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
- **Capability-anchored prompt eval** —
  `src/openharness/eval/` substrate: `Sample` / `Score` / `Scorer`
  Protocol + 4 scorers (programmatic + LLM-judge) + cassette
  record/replay/live + 8-axes version-stamped results JSONL +
  `oh eval` CLI. First consumer at `evals/focus_state/`. 4 boundary
  docs (`decisions/31-34-eval-*.md`); long-form narrative in
  `docs/ideas/eval-*.md`.

---

## How this project was built — for the curious reader

OpenHarness isn't just runnable code; it's a **case study in framework
design under self-imposed production constraints**. The repo preserves
the full decision and reflection trail.

If you want **the actionable distillation** (how to apply this
methodology to your own project), read **[`PLAYBOOK.md`](./PLAYBOOK.md)**
first — a ~6500-character Chinese playbook covering the 4-step phase
loop, when the agent must stop and ask, review-before-commit, the trail
discipline, and 5 framework lessons + 3 anti-patterns with quantitative
evidence. The rest of this section is the source material the playbook
draws from.

### The methodology

Each phase runs the same four-step loop:

1. **Boundary doc** ([`decisions/NN-phase-X-boundary.md`](./decisions))
    — what's in scope, what's out, what invariant must hold across
   the change. Locked **before** any code is written.
2. **Plan** ([`tasks/phase-X-plan.md`](./tasks))
    — capabilities with acceptance criteria. Not sub-task granular —
   the plan is the contract between the framework builder (the
   human) and the implementer (Claude Code).
3. **Execute** — Claude Code drives sub-tasks; the human reviews
   at the contract layer, never the implementation detail. Capability-
   level spec → agent autonomous build → human review.
4. **Retro** ([`learnings/phase-X.md`](./learnings))
    — what was learned, which abstractions held, which broke, what to
   predict for the next phase. Writes itself **at the end of each
   phase**, not at the end of the project.

This loop is itself the artifact. The combination "human stays at
contract layer + machine drives implementation + every phase ends
in a retro" is the project's most reproducible takeaway, independent
of the specific harness being built.

### Start here: the meta-retro

**[`learnings/phase-7.md`](./learnings/phase-7.md)** is the v0.1.0
project-level retrospective. Read it for:

- §1 — quantitative summary (~5x faster than original plan; reasoning
  why)
- §2 — Phase-by-Phase ship-order timeline (not plan order — ship order
  reveals which phases compounded)
- §3 — ⭐ **5 framework-level lessons** with quantitative evidence per lesson
- §4 — Python-specific patterns that paid off
- §5 — things that should have been done differently
- §10 — self-evaluation vs original SPEC

### The 5 framework lessons (compact)

1. **Abstraction-first compounds**. Phase 7a Protocol setup → 7b Docker
   substrate → 7c gVisor in **12% the LoC of 7b**, because the Protocol
   was the right shape. Identity-transform is the strongest evidence
   an abstraction is correct.
2. **Layered model can hold cross-cutting load**. Phase 5d ModeBundle
   touched 4 layers simultaneously (system prompt + tool catalog +
   deny paths + hook chain) — 11 protected dirs showed **zero diff**.
3. **Additive kwarg = the right shape for extending stable APIs**
   (Phase 5e + 6+). Default value = old behavior; opt-in = new
   functionality; existing tests byte-identically pass.
4. **Source-agnostic catalog** is the extensibility unlock. Phase 5f
   added a second producer (filesystem) at **60% the cost of the
   first** (entry points), because the catalog format never carried
   producer-specific fields.
5. **API-level zero-diff is the correct invariant for refactors**.
   Phase 8 extracted `markdown_store/` after rule-of-three triggered
   (5b/5c/5d) — 233 caller tests unchanged. Rule-of-three is the
   sweet spot, not earlier.

### The decision trail

24 boundary records in [`decisions/`](./decisions). Most valuable to
read in isolation:

- [`01-scaffolding.md`](./decisions/01-scaffolding.md)
   — full toolchain selection rationale (uv / ruff / mypy strict / Pydantic v2 / pytest-asyncio / etc.)
- [`08-phase-3-boundary.md`](./decisions/08-phase-3-boundary.md)
   — three-tier permission system design
- [`17-phase-5d-boundary.md`](./decisions/17-phase-5d-boundary.md)
   — first cross-layer tenant (the layered model's stress test)
- [`18-phase-5e-boundary.md`](./decisions/18-phase-5e-boundary.md) + [`20-phase-5f-boundary.md`](./decisions/20-phase-5f-boundary.md)
   — plugin discovery trust boundary
- [`22-phase-6plus-boundary.md`](./decisions/22-phase-6plus-boundary.md)
   — stream-event as final-state mechanism (the right way to expose
  generator state to a caller)

### The retros

18 per-phase retros + framing essays in [`learnings/`](./learnings).
Each `phase-N.md` opens with: which abstractions were tested, which
held, which broke. Read in ship order (see meta-retro §2).

---

## CLI reference

**Run a query**:

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
```

Inside `oh chat`, built-in slash commands: `/exit`, `/quit`,
`/clear` (reset history), `/help`. User-authored slash commands
(`/<name> args`) work the same as in `oh ask`.

**Introspect the framework**:

```bash
oh tools list                      # Built-in tools (Read/Write/Edit/Bash/Grep/Agent)
oh tools list --format json        # Same, machine-readable
oh tools show Read                 # Name, schema, is_read_only, trust_source
oh tools show Read -f json         # Same, JSON

oh config show                     # Effective Settings (api_key redacted)
oh config show --format json       # Same, JSON
oh config edit                     # $EDITOR on ~/.openharness/.env (lower-precedence layer)

oh hooks list                      # Built-in hooks (audit_log / deny_writes)
oh hooks list --enable-plugin-hooks  # Also include entry-point + filesystem plugins
oh hooks describe audit_log        # Event + docstring

oh --version
oh --help
```

**Run prompt eval**:

```bash
oh eval focus_state                          # Live mode (real LLM, write results JSONL)
oh eval focus_state --mode live              # Same as default
oh eval focus_state --mode record            # Real LLM + save cassettes (overwrite)
oh eval focus_state --mode replay            # 0 LLM call, replay cassettes (deterministic)
oh eval focus_state -m replay                # Short flag
oh eval focus_state --model qwen-max         # Override OPENHARNESS_MODEL (same-provider only;
                                             # cross-provider needs BASE_URL + API_KEY too)
oh eval focus_state --no-results             # Skip writing results JSONL
```

Cassette modes (D33.2):
- **`live`** (default): real LLM call every run, no cassette save
- **`record`**: real LLM call + save cassette (overwrites existing)
- **`replay`**: load cassette only, never call LLM; missing cassette
  raises `CassetteMissingError` (no silent fallback to live)

Each run writes `evals/focus_state/results/{timestamp}_{model}_{mode}.jsonl`
with 8-axes `RunMetadata` header: identity claim (sha256 of prompt /
rubrics / dataset), content claim (full prompt + rubric text), state
claim (git_commit + git_dirty). See `decisions/34-eval-stage5-*.md`.

`~/.openharness/.env` is a lower-precedence layer than the project's
`./.env`, which is lower than shell env vars. Use it for global
defaults (API keys, log level); override per-project in `./.env`;
override per-invocation via shell env.

---

## Configuration

All settings read from environment variables prefixed `OPENHARNESS_`
(via [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)).
See [`.env.example`](./.env.example) for a starter template.

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
see [`ARCHITECTURE.md`](./ARCHITECTURE.md). For per-decision trade-off
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
├── decisions/                # 24 decision records (per-trade-off)
├── learnings/                # 18 per-phase retros + framing essays (31 total)
├── tasks/                    # Per-phase boundary docs + implementation plans
├── docs/
│   ├── development-log.md    # Per-phase feature narratives (READ FOR HISTORY)
│   ├── tutorial.md           # Walked-through scenarios (Phase 7 T4)
│   ├── publishing.md         # PyPI runbook
│   └── ideas/, learning/     # Essays + living learning resources
├── examples/                 # Sample commands / skills / bundles / hooks (Phase 7 T4)
├── src/openharness/          # subsystems
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
├── tests/                    # ~1277 tests mirroring src/ layout
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

If you use Claude Code locally, copy
[`.claude/settings.json.example`](./.claude/settings.json.example)
to `.claude/settings.json` (gitignored) and replace
`/path/to/build-my-own-harness` with your actual clone path.

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

Full decision index: [`decisions/`](./decisions) (24 docs).

---

## SPEC v1 status

✅ **Shipped** as v0.1.0 on 2026-05-20, in 23 days. See
[`learnings/phase-7.md`](./learnings/phase-7.md) §10 self-evaluation
against the original SPEC, and [CHANGELOG.md](./CHANGELOG.md) for
post-v1 releases.

**Optional follow-ups** (acknowledged in meta-retro §5 and
[`decisions/23-phase-7-final-boundary.md`](./decisions/23-phase-7-final-boundary.md) §6,
none required for SPEC v1):

- Anthropic native client (~150 LoC; `protocols/` is already
  Anthropic-shape, so the wire translation is one-sided)
- LLM auto-compaction Layer 3 (turn-summarization for long sessions)
- Memory system (YAML-frontmatter `~/.openharness/memory/`)
- Keyring auth + multi-profile API key management
- `oh mcp add/list`, `oh skill run` subcommands
- REPL polish (`/save`, `/load`, multi-line input)
- Firecracker substrate (microVM isolation)

---

## Acknowledgments

This project's name and module vocabulary share heritage with
**[HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness)** (MIT
licensed) — the original Python LLM harness. This repo
(`build-my-own-harness`) is an **independent, from-scratch
reimplementation** built as a learning artifact: no code is shared,
implementation details diverge frequently, scope is intentionally
narrower (see [SPEC.md](./SPEC.md) §1 and
[ARCHITECTURE.md](./ARCHITECTURE.md) Tier 0 vs Out-of-Scope tables).

[`REFERENCE.md`](./REFERENCE.md) captures the upstream's v0.1.7
specification as it existed on 2026-04-26 — used here as a study
target, not a copy source. Full attribution at the top of that file.

---

## License

MIT — see [LICENSE](./LICENSE).
