# PLAYBOOK-PM — The Harness as a Product

> A short companion to [PLAYBOOK.md](./PLAYBOOK.md). That one is the engineering method;
> this one is the handful of calls on OpenHarness that **weren't technical** — they were
> product decisions. An engineer who owns outcomes makes these whether or not anyone hands
> them the title. Six that shaped the project:

| # | The decision | The product tension behind it |
|---|---|---|
| 1 | **Provider-agnostic is an invariant, not a feature.** The same loop, tools, and permission model run any OpenAI-compatible endpoint — by contract, not by an adapter bolted on later. | Lock-in and a simpler codebase vs. portability. Choosing portability also turned the harness into a *controlled-comparison instrument*: hold it fixed, swap the model, attribute the difference. That reframing is the product, not just the flexibility. |
| 2 | **Thin core over orchestration.** No graph builder, no workflow DSL — one streaming tool loop + recursive sub-agents + dynamic skills. | Ship more visible features now vs. stay thin. The bet: scaffolding that compensates for today's model ages into dead weight as models get better at long-horizon planning. Betting on the model six months out is a product call about where value will sit. |
| 3 | **Scope discipline — saying no on purpose.** Tier-0/1 plus one deep extension is a complete harness; sandbox tiers, extra providers, and full summarization compaction were deferred, not dropped. | Feature completeness vs. a shippable, legible core. The hardest product muscle here is declining work that's easy to justify — every deferral is written down with the condition that would reactivate it, so "no" stays honest rather than forgotten. |
| 4 | **A skill is an executable spec, not documentation.** Capability is delivered as something the model executes clause by clause, not prose it skims. | Docs that describe behavior vs. contracts that produce it. Treating the extension surface as executable spec is what lets a non-Anthropic model follow numbered hard-reject rules and cite rule IDs verbatim — the companion [finance-skills](https://github.com/maisieyang/finance-skills) repo is the proof. |
| 5 | **Differentiated errors, no tracebacks in default mode.** Config error, 401, 429, and loop-limit each surface a distinct, human-readable message. | Developer convenience (just raise) vs. end-user experience. A Python traceback is the cheapest thing to ship and the worst thing to read; deciding the error surface *is* part of the product, not an afterthought. |
| 6 | **The eval substrate as a trust feature.** A regression baseline for probabilistic behavior is treated as a product property — "it won't silently degrade" — not just internal QA. | Behavior you can *feel* vs. behavior you can *defend*. For a system that's part code and part model, "won't regress" is something users should be able to rely on, which makes the verification layer a feature, not overhead. *(Still being built out — see PLAYBOOK §5.)* |

---

**What this says about how I think:** product sense, for an engineer, isn't a separate hat —
it's deciding *what not to build*, *where value will sit in six months*, and *what the user
actually experiences at the error boundary*, while you're the one writing the code. These six
were made at the keyboard, not in a planning meeting.

> The full engineering operating model → [PLAYBOOK.md](./PLAYBOOK.md).
