# Claude Code Didn't Come from Nowhere: 8 Forces Behind the Agent Harness Wave

> Written 2026-04-27 · English version · 中文版 in [`why-harness-2025.md`](./why-harness-2025.md)
>
> This is a thinking log from my
> [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness)
> project, where I'm rebuilding a Claude-Code-style harness in Python from
> scratch. The hands-on perspective shapes how I read "why now."

---

## Opening

Starting in 2024, a new category of AI products began clustering together.
None were chat-interface descendants of "AI assistant." They came with tools,
memory, and the ability to dispatch sub-tasks autonomously:

- Anthropic's **Claude Code**
- OpenAI's open-source **Codex**
- **Manus** for fully autonomous agents
- Open-source projects: **OpenHarness**, **Aider**, **Continue**, **Cline**, …

The industry settled on a name: **agent harness**. The LLM is the brain; the
harness provides hands (tools), eyes (search/observation), memory
(persistence), and safety boundaries (permissions/sandboxing).

So why this specific two-year window? Why not 2022 when ChatGPT broke out, or
2023 when GPT-4 launched?

My thesis: this is **not one breakthrough**. It's **eight forces crossing
thresholds simultaneously**. Below I unpack each — and call out where my own
judgments might be wrong.

---

## Layer 1: Three inflection points in foundational model capability

Whether harnesses are even *possible* depends on the model substrate.
Three inflection points — and you need all three.

### 1. Long context windows: from 4K to 200K, from structurally impossible to structurally possible

GPT-3.5 in 2022 had a 4K context. Assume one tool call eats ~600 tokens
(command + output + model reasoning). That's roughly 6 tool calls before
overflow — nowhere near "complex task" territory.

By 2024:

- Claude 3 Sonnet: **200K**
- GPT-4 Turbo: **128K**
- Gemini 1.5 Pro: **1M** (with caching)

200K turns 100+ tool calls into routine. A Claude Code session — from initial
planning to final PR — often involves 50-100 read/edit/grep/bash/test
invocations. **In the 4K era, that wasn't "not yet popular." It was
physically impossible.** This is the most foundational precondition.

> ⚠ **Where I might be wrong:** 200K isn't a "minimum threshold" — it's the
> Claude-Code-style threshold. Cursor works fine in 32K through more
> aggressive truncation and retrieval. There's no canonical answer to "how
> much context does a harness need."

### 2. Tool calling: from "works" to "reliable" — a compounding phase change

Long context isn't enough. The model has to actually call tools *correctly*.
Compare:

- 2023, early GPT-4: tool-calling first-attempt success ~70%
- 2024, Claude 3.5 Sonnet: **>95%**

A 25-point gap. **The compounding effect is astronomical.** Consider a
50-step task:

| Per-step success | All-50-steps success |
|---|---|
| 70% | 0.7⁵⁰ ≈ **0.000008%** (effectively unusable) |
| 95% | 0.95⁵⁰ ≈ **7.7%** (works with retry) |
| 99% | 0.99⁵⁰ ≈ **60.5%** (genuinely useful) |

The curve is **convex**. The jump from 95% → 99% delivers more value than
90% → 95%. 2024-2025 models squeezed past the "50-step task is usable"
threshold.

> ⚠ **Where I might be wrong:** benchmark numbers vary widely (BFCL,
> ToolBench, MMAU don't agree). The "95%" is intuitive, not measured. But
> the convex-curve logic holds — and the next inch up unlocks the next
> agent generation.

### 3. The METR task-length curve: machine-izing long tasks

METR's early-2025 benchmark showed: **the length of tasks AI can complete
(measured by human-expert time) doubles every 7 months.**

- Mid-2024: models stably handle **~30-minute** human tasks
- Mid-2025: **multiple hours**
- If the curve holds, mid-2026: **a full workday**

The essence of a harness is **machine-izing long tasks**. If models could
only stably perform 30-second tasks, you wouldn't need tool loops, planning,
sub-agents — chat completion would suffice.

It's *because* models can now stably do long tasks that harnesses became
meaningful.

> ⚠ **Where I might be wrong:** the METR curve might not extrapolate
> linearly. Pre-training scaling laws are showing slowdown signals on some
> axes; whether agent capability keeps doubling every 7 months is honestly
> questionable.

---

## Layer 2: The economic threshold

Capability is ready. Next question: **can you afford to run it?**

### 4. Per-token cost dropped 10×, crossing the "everyday use" threshold

In 2023, GPT-4 was $30/M input, $60/M output. A 100-step harness task
(averaging 5K input + 1K output per step):

```
100 steps × (5K × $30/1M + 1K × $60/1M) = 100 × $0.21 = $21 per run
```

$21 per run. **This is luxury-tier** — VCs can burn it; individual
developers cannot.

By 2024-2025:

- Claude 3.5 Haiku: $0.80/M input, $4/M output
- Claude 3.5 Sonnet: $3/M input, $15/M output
- GPT-4o-mini: $0.15/M input, $0.60/M output

The same 100-step task: ~$0.50 on Sonnet, pennies on Haiku. **From luxury
to commodity.** Developers run dozens of trial-and-error loops; companies
run a harness invocation in CI on every PR.

Once the economic threshold flips, **what's technically possible becomes
commercially usable**. This isn't technical progress — it's a business
inflection.

---

## Layer 3: The ecosystem trigger

With foundational capability and economics in place, you need a "trigger"
to make the category explode. **This layer is the most decisive moment** —
when the first two layers are ready, *which day* the next two forces happen
barely matters, but once they happen, the category sticks.

### 5. First-party vendors enter: Claude Code and Codex legitimize the category

In 2023, agent experiments belonged to grassroots vs. academia: LangChain,
AutoGPT, BabyAGI, various PhD demos. All wore a **"toy"** label — fun to
play with, not entrustable with real work.

In 2024-2025:

- 2024 Q4: Anthropic ships **Claude Code**
- 2025: OpenAI open-sources **Codex**
- IDE integrations from every vendor follow

First-party entry does three things:

1. **Sets the reference standard.** People stop arguing over "how should an
   agent framework work" and just look at how Claude Code does it.
2. **Legitimizes the category.** No longer "my own toy" — now "what
   OpenAI is doing."
3. **Squeezes out the middle.** "Framework builder" positions (LangChain
   et al.) get compressed; new startups pivot to "build apps."

That move turned **harnesses from a research topic into a product category**.

### 6. MCP: a USB-C moment for the agent tool ecosystem?

Anthropic released **MCP (Model Context Protocol)** in November 2024 — an
open protocol that decouples agents from tools.

The hardware analogy: USB-C. Before USB-C, every framework either built its
own tool set or wrote N adapters for N other frameworks. After MCP, a tool
vendor writes one server and every MCP-compatible harness can use it.

By early 2026, MCP has **hundreds of public servers** (GitHub, Slack,
Notion, various databases). Claude Code, Cline, and Continue all support it
natively.

> ⚠ **Where I might be wrong:** this is the call I'm **least confident
> about**. MCP is barely over a year old; calling it "the USB-C moment" is
> bold. OpenAI could ship an incompatible protocol (e.g., Responses API
> with built-in tools), splitting the ecosystem. But **within the Anthropic
> camp at least, MCP is the de-facto standard.**

---

## Layer 4: Post-hoc narrative vs. real cause

The last two forces I'm pulling out separately — because they're more like
"narratives constructed *after* harnesses became popular," not "causes
that drove the popularity." This distinction matters; otherwise you think
you've understood when you're really just hearing your own echo.

### 7. User mental model shifting from "conversation" to "delegation"

The story usually goes:

> 2022-2023: ChatGPT made AI = chatbox. By 2024 developers complained — "I
> don't want to chat, I want it to do the work." Harnesses provide an
> "agent does the whole thing" product form, hitting the demand.

It sounds clean. But run it in reverse: **if harnesses didn't actually work
yet (the first 6 forces hadn't aligned), would users "want delegation"?**

No. User mental models follow **what already works**. Before Cursor
existed, you weren't crying out for "an editor that understands the entire
codebase" — because such a product didn't exist, so you couldn't fantasize
about it.

"User mental model shift" **is the result, not the cause.** It happened,
but the causality runs the other way.

### 8. Model API commoditization → harness as the value layer

The story:

> DeepSeek, Qwen, Llama drove chat completion toward zero cost. Value
> migrated from "model APIs" to "application-layer orchestration."
> Harnesses, as the application-layer's core abstraction, captured the
> value. Like CPUs becoming cheap — the OS is where the real value sits.

The analogy is seductive. Same caution: **value-layer migration is
balance-sheet logic, not "why users need harnesses" logic.**

If only OpenAI existed (no model commoditization), harnesses would still
take off — because the capability is in place and the demand is there.

---

> Distinguishing **cause from rationalization** matters because it shapes
> **what you predict next**. If you believe harnesses took off because
> "users want delegation," you'll go build more conversational products.
> But if you see clearly that foundational capability + economics arrived
> first, you'll watch the **next capability jump** and ask what it makes
> possible.

---

## Conclusion

Eight forces resonating — **three foundational model capabilities (#1, #2,
#3)** + **one economic threshold (#4)** + **two ecosystem triggers (#5,
#6)** drove harness adoption. The last two (#7, #8) are narratives
constructed after the fact.

What I'll be watching over the next 12-18 months:

- **Will the METR curve hold?** This is the biggest uncertainty. If agent
  capability slows from 7-month doubling to annual doubling, the entire
  harness category's ceiling gets locked.
- **Will MCP win?** Or does OpenAI ship an incompatible scheme that splits
  the ecosystem? This decides whether the agent stack stays open or
  fragments.
- **Second-generation harnesses.** First-gen harnesses (Claude Code style)
  are `stop_reason`-driven tool loops. Will a fundamentally different
  paradigm emerge — say, end-to-end RL-trained agents where "the loop" as
  an abstraction disappears?

One personal note: I'm rebuilding a Claude-Code-style harness in Python from
scratch
([build-my-own-harness](https://github.com/maisieyang/build-my-own-harness)).
It's both a way to master Python production-grade engineering and a way to
**stress-test or refute** the judgments in this post by getting my hands on
the actual abstractions. If you're thinking about this category too, I'd
welcome the conversation.

---

## Author's note

A few writing principles for this post (full meta-note in the
[Chinese version](./why-harness-2025.md)):

- **Honesty over authority.** Every layer flags "where I might be wrong."
  Most tech blogs skip this. Readers reward it.
- **Data over adjectives.** Token prices, context sizes, compounding math
  are concrete numbers. Vague phrases ("massive improvement") got cut.
- **Personal angle over panoramic survey.** The build-my-own-harness
  experience grounds claims in something specific — the differentiator
  others can't replicate.
- **Falsifiability.** The conclusion names what would refute the thesis,
  rather than asserting one final answer.
- **Distinguishing cause from narrative.** Layer 4 is arguably the most
  valuable section — most industry analysis treats narrative as cause,
  which leads to wrong predictions.
