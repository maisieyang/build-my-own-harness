# OpenHarness

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

[![CI](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **A from-scratch LLM agent harness in Python, built to production-grade standards — every subsystem and trade-off owned.**

You give it a prompt; it streams an LLM, the LLM picks tools, the harness runs
them safely, the loop continues until the model says it's done. Everything a
serious agent runtime needs — streaming tool loop, three-tier permissions, hook
middleware, structured observability, Docker/gVisor sandboxing, slash commands,
plugins, recursive sub-agents, multi-turn REPL, a capability-anchored eval
substrate, and Claude-Code-style auto memory — built on a deliberately thin core.
Provider-agnostic: the same loop runs Qwen, DeepSeek, or any OpenAI-compatible
endpoint. `mypy --strict`, `ruff` clean, ≥95% coverage gate.

---

## Design principles

Four convictions shaped every subsystem:

1. **The harness should be thin.** It encodes only what models can't yet do —
   and every such workaround becomes dead weight as models improve. The design
   target is the model six months out: a contract is never weakened to fit a
   weak model; you swap the model up or strengthen the prompt.
2. **Provider-agnostic is an invariant, not a feature.** The same loop, tools,
   and permission model run any OpenAI-compatible endpoint. This also turns the
   harness into a controlled-comparison instrument: hold it fixed, swap the
   model, attribute the behavior difference to the model.
3. **Minimal scaffolding over orchestration.** No graph builder, no workflow
   DSL. One streaming tool loop + recursive sub-agents + dynamic skill loading
   cover what orchestration frameworks do with far more machinery — machinery
   that ages poorly as models get better at long-horizon planning.
4. **A skill is an executable spec, not documentation.** The model executes it
   clause by clause, not skims it for vibes. Companion repo
   [finance-skills](https://github.com/maisieyang/finance-skills) demonstrates a
   non-Anthropic model following 16 numbered hard-reject rules and citing rule
   IDs verbatim in its output.

---

## Architecture

Three layered concerns, sliced vertically:

1. **Engine** (`engine/`) — `run_query` is an async generator streaming
   `ApiStreamEvent`s. Per turn: send messages → handle the `tool_use` stop
   reason → dispatch tools → append results → loop until `end_turn`. The
   caller's `initial_messages` is never mutated (defensive immutability).
2. **Tools** (`tools/`) — `BaseTool` ABC with Pydantic-validated input schemas;
   `ToolRegistry` is the catalog the engine introspects. The permission check
   runs **before** dispatch.
3. **Hooks** (`hooks/`) — a middleware chain over 5 lifecycle events; hooks can
   deny / modify / observe. It's the extension seam: compaction, mode bundles,
   and plugins all hang off it.

The LLM is the orchestrator — there's no state machine. The loop advances on
"did the model emit a `tool_use` this turn?", nothing else. The provider boundary
sits in `api/` — a streaming client plus wire-to-`ApiStreamEvent` translation
(with differentiated errors and retry/backoff) — so swapping providers (Qwen,
DeepSeek, any OpenAI-compatible endpoint) never touches the loop.

---

## Key engineering decisions

Each trade-off has a recorded rationale in [`decisions/`](./decisions):

| Concern | Choice | Rationale |
|---|---|---|
| Build / packaging | `uv` + `hatchling` | [`01-scaffolding`](./decisions/01-scaffolding.md) |
| Lint + format | `ruff` (replaces flake8/black/isort/pyupgrade) | ↑ |
| Type checking | `mypy --strict` everywhere | ↑ |
| Wire types | Pydantic v2, `extra="forbid"` | [`02-protocols`](./decisions/02-protocols.md) |
| First provider | Qwen via DashScope (OpenAI-compatible) | [`03-api-client`](./decisions/03-api-client-strategy.md) |
| Tool dispatch | Serial within a turn (no `gather`) | [`06-phase-2`](./decisions/06-phase-2-boundary.md) |
| Permission model | 3-tier: hardcoded deny + glob deny + mode | [`08-phase-3`](./decisions/08-phase-3-boundary.md) |
| Sandbox substrate | Protocol-based; `runc` default, `runsc` (gVisor) opt-in | [`15`](./decisions/15-phase-7-boundary.md), [`21`](./decisions/21-phase-7c-boundary.md) |
| Bundle composition | Pre-LLM resolution; engine zero-diff | [`17-phase-5d`](./decisions/17-phase-5d-boundary.md) |
| Plugin discovery | Entry points + `.py` files, opt-in | [`18`](./decisions/18-phase-5e-boundary.md), [`20`](./decisions/20-phase-5f-boundary.md) |

Full index: [`decisions/`](./decisions).

---

## What's in it

By subsystem (`src/openharness/`):

- **Streaming tool loop** (`engine/`) — the heart; LLM-driven, `end_turn`-terminated.
- **Tools** (`tools/`) — `Read` / `Write` / `Edit` / `Bash` / `Grep`, Pydantic-validated in/out.
- **Permissions** (`permissions/`) — hardcoded sensitive-path deny + glob deny + mode override (`--auto` / `--dry-run`).
- **Hooks** (`hooks/`) — 5 lifecycle events with deny/modify/allow; powers auto-truncation; exposed to plugins.
- **Observability** (`observability/`) — JSON logs with `run_id` / `turn_id` / `agent_depth` for `jq` trace reconstruction.
- **MCP** (`mcp/`) — Model Context Protocol (stdio) to register third-party tool servers.
- **Slash commands** (`commands/`) + **Skills** (`skills/`, lazy-loaded catalog) + **ModeBundle** (`bundles/`, compose prompt+tools+deny+hooks).
- **Plugin hooks** (`plugins/`) — third-party Python via entry points or dropped `.py`; opt-in.
- **Sub-agents** (`engine/`) — recursive `SpawnAgent` with depth limit; context inherited immutably via `dataclasses.replace`.
- **Sandbox** (`execution/`) — Docker via `--sandbox`, runtime-selectable (`runc` / `runsc` gVisor).
- **REPL** — `oh chat` accumulates history across turns via `ConversationCompleteEvent`.
- **Compaction** (`compaction/` + `services/`) — L1 per-tool-result truncation + L2 reactive PromptTooLong recovery.
- **Eval substrate** (`eval/`) — `Sample`/`Score`/`Scorer` + scorers (programmatic + LLM-judge) + cassette record/replay + version-stamped results + `oh eval`. Two consumers ship.
- **Auto memory** (`memory/`) — the LLM decides when to durably remember; two-step inline `Write` + `Edit` of `MEMORY.md`; per-project storage. Gated by a multi-turn eval.
- **Foundations** (`api/` · `protocols/` · `config/` · `prompts/` · `markdown_store/`) — OpenAI-compatible client + stream translation, Pydantic v2 wire types, `OPENHARNESS_*` settings, system prompts, shared Markdown store.

---

## Quality bars

- `mypy --strict` across `src/` · `ruff` lint + format clean · **≥95% coverage gate**
- CI runs lint + type-check + full suite on **Python 3.10 and 3.11** ([`ci.yml`](./.github/workflows/ci.yml))
- Tests pass with **zero external deps**; integration/sandbox tests gate on env vars / Docker / gVisor and skip cleanly
- Differentiated errors, no Python tracebacks in default mode (config error / 401 / 429 / loop turn-limit each surface a distinct message)

---

## Quickstart

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # 1. install uv (one-time)

git clone https://github.com/maisieyang/build-my-own-harness.git
cd build-my-own-harness && uv sync                  # 2. clone + sync

cp .env.example .env && $EDITOR .env                # 3. set OPENHARNESS_API_KEY + BASE_URL
                                                    #    (any OpenAI-compatible endpoint)
uv run oh ask "list 5 git commands"                 # 4. ask
uv run oh chat                                       #    or a multi-turn REPL
```

All settings are `OPENHARNESS_*` env vars (via `pydantic-settings`); see
[`.env.example`](./.env.example). Full command surface: `oh --help` (`ask` /
`chat` / `tools` / `config` / `hooks` / `memory` / `eval`).

---

## How it was built

Solo developer + Claude Code, AI-first: the human stayed at the contract layer
(scope, trade-offs, acceptance) and the agent drove implementation — **built
from scratch and still iterating after ~7 weeks** (20 subsystems, 300+ commits,
solo). What makes it a study rather than just code is that the
**full reasoning trail is preserved**: every trade-off in [`decisions/`](./decisions),
every retrospective in [`learnings/`](./learnings), the plan/execute trail in
[`tasks/`](./tasks) — *not just what was built, but why each trade-off was made
and what each phase predicted before being built.*

- The methodology, distilled → [**PLAYBOOK.md**](./PLAYBOOK.md) (the operating model: learn by rebuilding, human owns the contract, the disciplines that keep speed honest)
- The project-level meta-retro → [`learnings/phase-7.md`](./learnings/phase-7.md)

---

## Other reader lenses

- **Product (PM) lens** → [**PLAYBOOK-PM.md**](./PLAYBOOK-PM.md) — the harness as a product: 6 product decisions made at the keyboard.
- **Apply the rebuild methodology** → [PLAYBOOK.md](./PLAYBOOK.md).

---

## Acknowledgments

Name and module vocabulary share heritage with
[**HKUDS/OpenHarness**](https://github.com/HKUDS/OpenHarness) (MIT) — the
original Python LLM harness. This repo is an **independent, from-scratch
reimplementation** built as a learning artifact: no code shared, implementation
diverges frequently, scope intentionally narrower.
[`REFERENCE.md`](./REFERENCE.md) captures the upstream's v0.1.9 spec used as a
study target, not a copy source.

## License

MIT — see [LICENSE](./LICENSE).
