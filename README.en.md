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

> **A local-first Coding Agent Harness built from scratch in Python.**

OpenHarness is a local-first Coding Agent Harness that I built from scratch in Python and continue
to dogfood. It manages what the model sees next, what its actions can actually affect, when
long-running tasks continue or stop, and how probabilistic behavior can be validated repeatedly.
The model supplies intelligence; OpenHarness takes responsibility for the consequences of action.

The project asks three questions:

1. What should the model see next?
2. How can an Agent keep working within controlled boundaries?
3. Once model decisions shape system behavior, how should a Coding Agent be validated?

## One-Minute Tour

```text
$ uv run oh

>>> Fix the failing tests and run verification
Default → read / edit / execute → return the result

>>> /plan Inspect the implementation and create a verification plan
Plan → read-only exploration → approve the plan → return to Default

>>> /goal Complete the approved plan; done means tests, mypy, and ruff all pass
Goal → keep working → independent Judge checks the completion condition → continue / complete / pause
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

Within a Session, OpenHarness continuously assembles the System Prompt, Tool catalog, and
Conversation. It loads Skills, Plugins, and Memory through progressive disclosure; limits the growth
of individual Tool Results; clears older results; and semantically compacts older history when
needed. Across Sessions, Project Memory preserves reusable knowledge, Snapshots preserve session
state, and Resume recompiles the next Working Set against the current environment and permissions.

This is not only about Context Window capacity. It is about managing the model's limited attention.
A larger window can hold more information, but it cannot determine which information is sufficient,
trustworthy, current, and appropriate for the next action.

Read the full article (Chinese): [Content Management: Managing Limited Attention for Coding Agents](https://maisieyang.github.io/writing/content-management.html)

### 2. Goal, Permission, and Sandbox: Let the Agent Keep Working While the Human Steps Away Without Losing Control

I implemented `/goal` so that a human no longer has to hand the task back after every turn. The user
defines the goal and completion conditions once, the system keeps working, and an independent Judge
decides from execution evidence whether the task is complete.

But Goal only answers why the task should continue and when it should stop. It does not decide what
the Agent may do, who decides when it crosses a boundary, or how much impact an action can have.

| Control question | Mechanism | Responsibility boundary |
|---|---|---|
| Should the task continue, and when is it complete? | Goal Controller + independent Judge | Does not grant new capabilities |
| Has a specific boundary exception been authorized? | Permission | Does not enforce the local execution boundary |
| What can a local action actually affect at most? | Sandbox | Does not decide whether an action matches human intent |

After `/goal` sets the completion conditions, work begins immediately. At the end of every clean
Worker turn, a tool-disabled independent Judge examines only the execution evidence produced after
the Goal was set. If the conditions are met, it stops. If evidence is missing, it feeds the gap into
the next turn. If it cannot decide, it preserves the state and pauses.

Permission and Sandbox guard the action boundary. The Permission Profile expresses the base
authorization intent. The Sandbox compiles its local portion into a verifiable execution boundary.
An out-of-bounds action can receive only a precise, one-time exception. If a new authorization
requires a human decision, the system parks the current continuation instead of letting Goal spin
around the same capability gap.

Together, these mechanisms change where human attention is required:

```text
Watch the Agent turn by turn
        ↓
Define the goal, completion conditions, and base boundaries in advance
        ↓
Return when the task completes or reaches a genuine human decision boundary
```

Read the full article (Chinese): [I Implemented `/goal`, but the Human Still Cannot Leave](https://maisieyang.github.io/writing/goal-external-completion.html)

### 3. Eval: How Should a Coding Agent Be Validated?

After actually building Evals, I no longer see them as special machinery outside software
engineering. They extend software testing into behavior shaped by a model—where a single
deterministic assertion can no longer cover the outcome.

> **Eval begins when model decisions shape system behavior.**

When system behavior depends on LLM output, testing must cover not only the deterministic mechanism
but also the model decision surface.

OpenHarness uses four kinds of evidence to answer different questions:

| Validation method | Question | Cadence |
|---|---|---|
| Mechanism tests (TDD) | Are state machines, permission rules, tool execution, persistence, and failure paths correct? | Daily development |
| Decision-surface Eval | Does system behavior determined by an LLM output satisfy the capability contract for that decision surface? | Daily development |
| Dogfood | Does the complete product work in practice, and which problems remain outside existing tests? | Daily use |
| Public Benchmark | Where does the system stand on shared tasks and external judgments? | Periodically |

These are not four parallel testing layers. TDD, decision-surface Evals, and Dogfood form the daily
development loop. Public Benchmarks become an external coordinate once the system has taken shape.

For the same request, the model may choose different tools, construct different arguments, or react
to failure by correcting itself or repeating the same action. It must also decide what is worth
remembering, when to load additional capabilities, which facts must survive compaction, and whether
the task is actually complete. A behavior becomes a candidate for Eval when a model participates in
the decision, the behavior varies, and the impact on the product is significant.

Every Eval begins by defining its capability claim, inputs, judgment method, and reference policy.
When a tool name, required field, trajectory invariant, or final state can answer the question,
OpenHarness prefers those hard oracles over an LLM Judge. A soft Judge is introduced only when
deterministic checks cannot express the semantic judgment. A probabilistic system also needs more
than a single passing run: `live` observes current model behavior, `record` preserves an accepted
response, and `replay` verifies the dataset, scorer, and recorded behavior without revalidating the
current model.

Eval ultimately tests more than the model. It also tests the person writing the Eval: which behavior
actually matters, which path is merely preferred, and what evidence is enough to believe the result
is better. It turns the Dogfood judgment that “something feels wrong here” into a reproducible trace,
an explicit capability claim, cases, an oracle, and a pass bar.

**Eval turns human judgment and taste into a durable engineering asset.**

Read the full article (Chinese): [How Should a Coding Agent Be Validated?](https://maisieyang.github.io/writing/agent-eval-demystified.html)

## Quick Start

The setup below currently targets macOS. It requires Python 3.10+,
[uv](https://docs.astral.sh/uv/), and an LLM provider with an OpenAI-compatible API.

```bash
git clone https://github.com/maisieyang/open-harness.git
cd open-harness
cp .env.example .env  # Add your API key, base URL, and model name
uv run oh
```

## Development and Validation

Run before submitting a change:

```bash
uv run pytest -m "not integration and not eval" -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
```

For changes that affect model decisions, run the relevant capability evals as described in the
[Eval Guide](./evals/README.md).

## Dogfood and Extensions

[finance-skills](https://github.com/maisieyang/finance-skills) validates OpenHarness Skills and
Plugins against real domain workflows.

More essays are available at [Writing](https://maisieyang.github.io/writing/).

## Acknowledgments

The name and initial module vocabulary come from [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness)
(MIT). This repository is an independent implementation built from scratch.

## License

MIT — see [LICENSE](./LICENSE).
