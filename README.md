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

> **An LLM agent harness rebuilt from scratch in Python, to a production bar —
> every subsystem owned by hand, every trade-off on record.**
> *What I cannot create, I do not understand.*

---

## What is a harness?

An LLM can only *talk*: you give it a prompt, it returns text. To make it
**act**, **remember**, and **stay safe**, you have to wrap a layer around it.
That layer is the harness.

At the bottom of this entire repository sits one loop:

```
while True:
    stream = llm.stream(messages)          # streaming API call
    parse tool_use blocks                  # what does the model want to do?
    for each tool_use:
        check permission                   # gate it
        execute tool                       # do it
        append tool_result to messages     # feed it back
    if stop_reason == "end_turn": break    # the model says it's done
```

That is `engine/run_query` — the heart. **The LLM itself is the orchestrator;
the loop just turns its words into actions and feeds the results back.**
Everything else in this repo exists because running that loop for real,
unattended, on real codebases, exposes a chain of problems — and each
subsystem is the fix for one of them.

## The problem chain (what's inside, and why)

Each ring below exists because the previous ring exposed it.

**1. The loop runs — but the model must not do whatever it wants.**
Tool calls pass a permission gate *before* dispatch: hardcoded sensitive-path
denies, glob deny rules, mode overrides (`--auto` / `--dry-run`), and an
unconditional red line for irreversible git actions. A middleware chain of
lifecycle **hooks** (`hooks/`) can deny, modify, or observe every step — it is
also the seam that compaction, mode bundles, and plugins hang off.
→ `permissions/` · `hooks/`

**2. Sessions get long — and the context window overflows.**
Per-tool-result truncation plus reactive `PromptTooLong` recovery keep the
loop alive past the window.
→ `compaction/` · `services/`

**3. Sessions end — and the model forgets everything.**
A Claude-Code-style auto-memory: the LLM itself decides what is worth
remembering, persists it as Markdown per project, and gets it back next
session. Gated by a multi-turn eval, inspectable via `oh memory`.
→ `memory/` · `markdown_store/`

**4. Capability should grow without touching the core.**
Four extension mechanisms, all feeding the same tool registry and hook
catalog: **skills** (lazy-loaded Markdown expertise), **slash commands**
(user-authored `/<name>` prompts), **plugins** (third-party Python via entry
points or dropped `.py` files, Claude-Code plugin format included), and
**MCP** (Model Context Protocol servers over stdio). **Mode bundles** compose
prompt + tool whitelist + denies + hooks into one switchable mode.
→ `skills/` · `commands/` · `plugins/` · `mcp/` · `bundles/`

**5. One agent's context isn't enough.**
`SpawnAgent` makes the agent loop itself a tool — recursive delegation with a
depth limit and immutable context inheritance.
→ `tools/spawn_agent.py`

**6. Executing model-chosen commands can wreck the host.**
Opt-in process-level isolation: Docker via `--sandbox`, gVisor via
`--sandbox-runtime runsc`, behind an `ExecutionEnvironment` protocol so the
engine never knows which substrate it's on.
→ `execution/`

**7. And still — a human sits in the chair for every iteration.**
The interactive CLI leaves three chairs occupied by a person: *planning*,
*verification*, *gatekeeping*. The **loop-runtime** layer hands all three to
the harness:

```bash
oh ask -p "fix the failing tests; do not touch assertions" \
  --output-format json --verify "pytest -q" --max-iter 5 --isolate
```

Headless print mode (`-p`, JSON / stream-JSON, exit codes) is the atom.
A **verification gate** decides done-ness deterministically — a command's
exit code (`--verify`) or an independent LLM judge for semantic criteria
(`--goal-condition`); the model's self-assessment is never the gate. An outer
**repair loop** re-feeds the gate's feedback into a *fresh* context each round
until the gate passes or the iteration cap hits. Around it: declarative
headless permission policy (fail-closed, no TTY prompts), goal
self-decomposition (`--decompose`), a cron-style intake queue
(`oh autopilot`), git-worktree isolation (`--isolate`), and an append-only
per-run journal that makes runs resumable (`--resume-run`) and inspectable
(`oh run show`). You write the goal and the acceptance check once — then
walk away.
→ `verification/` · `services/` (worktree · run journal · run session) · `cli.py`

Underneath it all: an OpenAI-compatible streaming client, Pydantic v2 wire
types, `OPENHARNESS_*` config, and structured JSON logs with
`run_id` / `turn_id` / `agent_depth` — a full trace reconstructable with `jq`.
Provider-agnostic: the same loop runs Qwen, DeepSeek, or any
OpenAI-compatible endpoint.
→ `api/` · `protocols/` · `config/` · `prompts/` · `observability/`

## Quick start

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # 1. install uv (once)

git clone https://github.com/maisieyang/build-my-own-harness.git
cd build-my-own-harness && uv sync                  # 2. clone + sync

cp .env.example .env && $EDITOR .env                # 3. set OPENHARNESS_API_KEY + BASE_URL
                                                    #    (any OpenAI-compatible endpoint)
uv run oh                                           # 4. enter the REPL
                                                    #    (/ pops the command menu)
uv run oh ask "list 5 git commands"                 #    or one-shot ask
```

All configuration is `OPENHARNESS_*` environment variables (via
`pydantic-settings`); see [`.env.example`](./.env.example). Full command
surface: `oh --help` (`ask` / `chat` / `tools` / `config` / `hooks` /
`memory` / `plugins` / `snapshot` / `eval` / `autopilot` / `run`).

## Quality contract

- `mypy --strict` across all of `src/` · `ruff` lint + format clean ·
  **≥95% coverage gate** on the stable core
- CI runs lint + type-check + the full suite on **Python 3.10 and 3.11**
  ([`ci.yml`](./.github/workflows/ci.yml))
- Tests pass with **zero external dependencies**; integration / sandbox tests
  are gated behind env vars / Docker / gVisor and skip cleanly when absent
- TDD as discipline: tests are written first and **seen red** before the code
  that turns them green — a green that was never red is no green at all
- Differentiated errors, no raw Python tracebacks in default mode

## How it was built

Solo developer + Claude Code. The human stays at the contract layer — scope,
trade-offs, acceptance criteria; the agent drives the implementation. Built
from scratch and still iterating.

The part you can't get from reading the code: **three append-only trails**
preserve the complete design context of every module, written *around* the
code, never after the fact —

| Trail | What | When written |
|---|---|---|
| [`decisions/`](./decisions) | Boundary docs — what's in scope, what's out, which invariant holds | **before** each phase |
| [`tasks/`](./tasks) | Capability-level plans (never sub-task granularity) | **before** each phase |
| [`learnings/`](./learnings) | Retrospectives — which abstractions held, what to predict next | **after** each phase ships |

Any design decision in the project can be reconstructed by reading its
triplet in order: boundary → plan → retro. Start with
[`tasks/README.md`](./tasks/README.md) for the phase index, or
[`learnings/openharness-first-principles.md`](./learnings/openharness-first-principles.md)
for the compass this README's narrative comes from.
[`REFERENCE.md`](./REFERENCE.md) is the frozen cognition map reverse-engineered
from the upstream project — the anti-toy baseline everything was built against.

## The bigger picture

This harness is the substrate layer. The same build-to-understand move runs
through three repos:

- **harness** → **build-my-own-harness** (you are here) — the production-bar
  agent runtime.
- **plugin** → [**finance-skills**](https://github.com/maisieyang/finance-skills)
  — the same move run into a vertical: study Anthropic's open-source
  `financial-services` skills, then build
  [`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk)
  from scratch. It runs *on* this harness.
- **method** → [**my-skills**](https://github.com/maisieyang/my-skills) — the
  working method itself, encoded as reusable skills (forked from agent-skills;
  only what the substrate lacks).

## Credits

Name and module vocabulary follow
[**HKUDS/OpenHarness**](https://github.com/HKUDS/OpenHarness) (MIT) — the
original Python LLM harness. This repo is an **independent, from-scratch
reimplementation** built as a learning artifact.
[`REFERENCE.md`](./REFERENCE.md) captures the upstream v0.1.9 spec as a study
target, not a copy source.

## License

MIT — see [LICENSE](./LICENSE).
