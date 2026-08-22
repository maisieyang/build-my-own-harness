# OpenHarness

<p align="center">
  <a href="README.md"><strong>简体中文</strong></a> ·
  <a href="README.en.md"><strong>English</strong></a>
</p>

[![CI](https://github.com/maisieyang/open-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/maisieyang/open-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **A local-first Coding Agent Harness, independently built in Python.**

OpenHarness currently exposes a CLI, while its engineering focus is the Agent Runtime: managing
what the model sees next, how tasks continue or stop, what model actions can actually affect, and
how key model-mediated decisions can be validated repeatedly.

The project asks three questions:

1. What should the model see next?
2. How can an Agent keep working within explicit boundaries?
3. Once model decisions shape system behavior, how should a Coding Agent be validated?

**Engineering evidence:** 2,791 stable tests, 95.06% stable-core coverage; 9 Eval contracts,
6 / 6 replay gates; SWE-bench Lite 170 / 300.
[See the validation methods and evidence boundaries →](#engineering-evidence-baseline)

## Quick Start

The setup below currently targets macOS. It requires Python 3.10+,
[uv](https://docs.astral.sh/uv/), and an LLM endpoint with an OpenAI-compatible API.

```bash
git clone https://github.com/maisieyang/open-harness.git
cd open-harness
cp .env.example .env  # Add your API key, base URL, and model name
uv run oh              # Add --auto to auto-approve exact permission requests
```

Once launched, OpenHarness enters the REPL. Depending on the task, you can work directly, plan
before acting, or let Goal manage continuous execution:

```text
Default  Explore, edit, and verify; return control after one turn
/plan    Explore read-only and produce a plan for approval; return to Default after approval
/goal    Work toward a goal across turns; independent Judge decides whether to continue / complete / pause
```

## The Three Problems I Am Working On

### 1. Content Management: Compiling the Working Set for the Next Model Turn

A single LLM API call has no memory of its own. Context is the bounded input that the Harness
reconstructs and submits before every call.

Context is therefore not an ever-growing transcript. It is the **Working Set** that the Harness
compiles for the model's next turn. It must manage three kinds of content:

| Content | Question it answers | Typical sources |
|---|---|---|
| Task | What are we trying to accomplish now? | User, Goal, Project Instructions |
| Evidence | What has already been learned or verified? | Conversation, Tool Results, Memory, Snapshots |
| Capabilities | What actions are currently available? | Tools, Skills, Plugins, Permissions, Plan |

Within a Session, OpenHarness continuously assembles the System Prompt, tool catalog, and
Conversation. It loads Skills, Plugins, and Memory through progressive disclosure; limits the growth
of individual Tool Results; clears older results; and semantically compacts older history when
needed. Across Sessions, Project Memory preserves reusable knowledge, Snapshots preserve session
state, and Resume recompiles the next Working Set against the current environment and permissions.

This is not only about Context Window capacity. It is about managing the model's limited attention.
A larger window can hold more information, but it cannot determine which information is sufficient,
trustworthy, current, and appropriate for the next action.

Read the full article (Chinese): [Content Management: Managing Limited Attention for Coding Agents](https://maisieyang.github.io/writing/content-management.html)

### 2. Goal, Permission, and Sandbox: Keeping the Agent Working While the Human Steps Away—Without Losing Control

I implemented `/goal` so that a human no longer has to advance the task one turn at a time. The user
defines the goal and completion conditions once, OpenHarness keeps it moving, and an independent
Judge decides from execution evidence whether the task is complete.

But Goal only answers why the task should continue and when it should stop. It does not decide what
the Agent may do, who decides when it crosses a boundary, or how much impact an action can have.

| Control question | Mechanism | Responsibility boundary |
|---|---|---|
| Should the task continue, and when is it complete? | Goal Controller + independent Judge | Does not grant new capabilities |
| Has a specific boundary exception been authorized? | Permission | Does not enforce the local execution boundary |
| What is the maximum impact a local action can have? | Sandbox | Does not decide whether an action matches human intent |

After `/goal` sets the completion conditions, work begins immediately. At the end of every clean
Worker turn, a tool-disabled independent Judge examines only the execution evidence produced after
the Goal was set. If the conditions are met, it stops. If evidence is missing, it feeds the gap into
the next turn. If it cannot decide, it preserves the state and pauses.

Permission and Sandbox guard the action boundary. The Permission Profile expresses the base
authorization intent. The Sandbox compiles the local portion of that profile into a verifiable
execution boundary.
An out-of-bounds action can receive only a precise, one-time exception. If a new authorization
requires a human decision, the system parks the current continuation instead of letting Goal spin
around the same capability gap.

Together, these mechanisms change where human attention is required:

```text
Supervise every Agent turn
        ↓
Define the goal, completion conditions, and base boundaries in advance
        ↓
Return when the task completes or reaches a genuine human decision boundary
```

Read the full article (Chinese): [I Implemented `/goal`, but the Human Still Cannot Leave](https://maisieyang.github.io/writing/goal-external-completion.html)

### 3. Eval: How Should a Coding Agent Be Validated?

After building and running Evals, I no longer see them as special machinery outside software
engineering. They extend software testing into behavior shaped by a model—where a single
deterministic assertion can no longer cover the outcome.

> **Eval begins when model decisions shape system behavior.**

When system behavior depends on LLM output, testing must cover not only the deterministic mechanism
but also the model decision surface.

OpenHarness uses four kinds of evidence to answer different questions:

| Validation method | Question | Cadence |
|---|---|---|
| Mechanism tests (TDD) | Are state machines, permission rules, tool execution, persistence, and failure paths correct? | Daily development |
| Decision-surface Eval | Does behavior determined by an LLM output satisfy the capability contract for that decision surface? | Daily development |
| Dogfood and real use | Does the complete product solve real tasks and remain worth using? Which problems are not yet captured by tests? | Continuous use |
| Public Benchmark | Can the core coding loop complete public tasks end to end under external evaluation? | Periodic runs |

TDD, decision-surface Evals, Dogfood, and real use form a continuous development loop. A public
benchmark adds a bounded external coordinate for the core coding loop; its score belongs to the
composite system of the model, Harness, tools, run budget, and execution environment.

**Eval turns human taste into an engineering asset.**

Read the full article (Chinese): [How Should a Coding Agent Be Validated?](https://maisieyang.github.io/writing/agent-eval-demystified.html)

## Engineering Evidence Baseline

As of 2026-08-22:

- **Software mechanisms:** 2,791 stable tests pass, with 95.06% stable-core coverage.
  `mypy --strict`, Ruff, and the format check all pass; the [CI](./.github/workflows/ci.yml)
  matrix covers Python 3.10 and 3.11.
- **Agent decisions:** 9 capability Eval contracts; 6 have completed live-model ratification,
  with replay gates currently at 6 / 6. See the [Eval Guide](./evals/README.md).
- **Public tasks:** OpenHarness 0.4.0 paired with qwen3.7-max solved 170 / 300 tasks on
  [SWE-bench Lite](./benchmarks/swebench/TAXONOMY.md), for a 56.7% resolved rate.

Replay only guards regressions in recorded behavior. A benchmark measures the composite system of
the model, Harness, tools, run budget, and execution environment. Neither substitutes for Dogfood
or feedback from real users.

## Development and Validation

The day-to-day CI and contributor gates are:

```bash
uv run pytest -m "not integration and not eval" -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
```

For changes that affect model decisions, also run the relevant capability Evals described in the
[Eval Guide](./evals/README.md).

## Plugins and Extensions

[finance-skills](https://github.com/maisieyang/finance-skills) is a vertical finance capability
package built on the Harness foundation. It reuses the OpenHarness Runtime and loads domain
knowledge and workflows through Skills and Plugins.

More essays are available on my blog,
[Maisie’s World｜梅茜的世界](https://maisieyang.github.io/writing/).

## Acknowledgments

The name and initial module vocabulary come from
[HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness) (MIT). This repository is an independent
implementation built from scratch.

## License

MIT — see [LICENSE](./LICENSE).
