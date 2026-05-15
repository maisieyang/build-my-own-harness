# Phase 4 Boundary — Compaction (Context Management)

> Status: locked at Phase 4 entry, 2026-05-15.
>
> Rationale + framing: see `learnings/phase-4-framing.md` (post-Phase-3
> retro § "给 Phase 4 的 input" + WebSearch verification of Codex /
> OpenHarness behavior).

## Triggering observation

P3-T5 trace let us watch `output_len` of `tool_complete` events
accumulate in real time. Smoke case B (Phase 3 close-out) actually
**did blow context** on Qwen-plus with 2× full `uv.lock` Read calls.
This isn't a bug — it's the protocol:

> LLM is stateless. Every call replays the full conversation.

Phase 4 makes the harness survive long conversations without raising
`RequestFailure(400, "context length exceeded")`.

---

## In scope

**D14.1 — Two-layer defense, no LLM-as-summarizer (yet).**

Two independent mitigations:

- **Layer 1 — per-tool-result truncation** (proactive, inline):
  - Each `tool_result` exceeding a token cap is truncated head/tail with
    a marker. Codex-style; based on the empirical observation that in
    coding workflows, the *start* (imports, signatures) and *end*
    (errors, final output) of a tool's output carry the most
    LLM-actionable signal — the middle is often noise.
  - Triggered as a **PostToolUse hook** (dogfooding the Phase 3 hook
    system from P3-T4).
  - Default cap: 10000 tokens per tool_result (matches Codex's published
    recommendation).
  - Truncation split: 50 % head + 50 % tail + middle marker
    `"\n... [truncated <N> tokens] ...\n"`.

- **Layer 2 — reactive prompt-too-long recovery** (engine-side):
  - On `RequestFailure` whose message matches a prompt-length-exceeded
    pattern, the engine retries the same turn after dropping the oldest
    `tool_use` / `tool_result` pair(s) from `messages`.
  - Triggered in **`run_query` itself** (not a hook). Reactive recovery
    is loop-control flow, not horizontal capability — keeping the
    retry-and-truncate decision close to the retry boundary keeps the
    semantics clear.
  - Bounded: at most 3 reactive truncation retries per turn (D14.5).

**D14.2 — Token counting via `tiktoken` with byte-ratio fallback.**

- Required dependency: `tiktoken` (small Rust-backed package, OpenAI-
  maintained, supports all major model encodings).
- If a model name has no `tiktoken` encoding (Qwen / Anthropic
  native / etc.), fall back to `len(text.encode("utf-8")) // 4` —
  the published Codex approximation (`num_bytes / 4`).
- Implementation lives in `openharness/compaction/tokenize.py`. Single
  public function: `count_tokens(text: str, model: str) -> int`.

**D14.3 — Settings + CLI flags.**

- `Settings.tool_result_cap: int = 10000` (env
  `OPENHARNESS_TOOL_RESULT_CAP`).
- `--tool-result-cap N` CLI flag overrides.
- `Settings.auto_truncate: bool = True` (env
  `OPENHARNESS_AUTO_TRUNCATE`).
- `--no-auto-truncate` CLI flag disables Layer 1 (Layer 2 still
  guards against blow-up).

**D14.4 — Marker text is observable from logs.**

Truncation hook emits a `tool_truncated` log at INFO level (extending
the 8-log inventory to 9) with `tool_use_id` + `original_tokens` +
`truncated_tokens`. JSONL trace consumers can see the compaction
happened without reading the (now-truncated) payload.

**D14.5 — Reactive truncation is bounded.**

`run_query` allows at most `_REACTIVE_TRUNCATE_MAX = 3` retries within
one turn before re-raising the underlying `RequestFailure`. Each retry
drops one tool_use/tool_result pair (oldest first). The reasoning: a
prompt that needs more than 3 drop-and-retry cycles is structurally
unrecoverable inside the loop — surface the failure to the caller so
they can shrink prompts / split into multiple sessions.

---

## Out of scope (Phase 5+)

- **LLM-as-summarizer (`Full Compact`)** — generating natural-language
  `<analysis>/<summary>` over old messages. Defer for two reasons:
  (1) it adds a synchronous LLM call inside compaction; complexity
  budget; (2) Layer 1+2 covers ~80 % of context blow-ups we observed.
- **Determinístic session memory** — rule-based "merge oldest N into a
  composite". Strictly between truncation and LLM summary; defer until
  we have evidence Layer 1+2 is insufficient.
- **Microcompact** — selective clearing of *which* old `tool_result` to
  drop. Layer 2 currently drops oldest pair; "drop the largest" or
  "drop tool X but not tool Y" needs domain heuristics.
- **Attachments preservation** — `task_focus` / `recent_files` /
  `invoked_skills` carry-over across compaction. Needs a persistent
  side-channel; Phase 5+.
- **Auto compaction trigger** (preventive, at e.g. 80 % of window) —
  Layer 2 is purely reactive (on 400 error). Preventive is a Phase 5+
  optimization once Layer 2 reveals real cost in retries.
- **Cross-session memory** — persistence between `oh ask` invocations.
  Out of scope for the harness itself; a future MCP server's job.

---

## Critical decisions (D14.x)

| ID | Decision | Why |
|---|---|---|
| **D14.1** | Two-layer defense (Layer 1 hook + Layer 2 engine); no LLM-as-summarizer in Phase 4 | 80 % coverage at 20 % complexity; LLM-summarizer is Phase 5+ |
| **D14.2** | `tiktoken` required + byte-ratio fallback | Accurate for OpenAI-family; graceful for Qwen / Anthropic-native |
| **D14.3** | `--tool-result-cap` + `--no-auto-truncate` CLI flags | User can shut Layer 1 off; Settings env mirror per provider-neutral pattern |
| **D14.4** | `tool_truncated` log event added (9th log point) | Observability of compaction itself — trace consumers see *when* compaction fired |
| **D14.5** | Reactive truncation bounded to 3 retries / turn | Prevents infinite drop-and-retry; surfaces structural overflow to caller |

---

## Dependency direction

```
compaction/                       (new package)
   ├── tokenize.py                ← only deps: tiktoken (optional), stdlib
   ├── truncate.py                ← only deps: tokenize.py, stdlib
   └── hook.py                    ← deps: hooks, observability, truncate

engine/query.py                   ← adds 1 except branch for Layer 2 reactive
config/settings.py                ← +2 fields
cli.py                            ← +2 flags
api/errors.py                     ← +PromptTooLongFailure subclass of RequestFailure
```

`compaction/` is downstream of `hooks/` and `observability/`; upstream
of `engine/query.py` (engine imports `compaction.hook` for the default
registration). This matches Phase 3 layering — hook system is foundational,
compaction is a tenant.

---

## Sub-decisions deferred

Three open questions I'm noting but not locking now — these surface
during T2 / T3 and the answer depends on what tests reveal:

- **Should Layer 1 fire BEFORE PostToolUse user hooks or AFTER?** If
  before, user hooks see truncated output (good for moderation hooks
  that worry about size); if after, user hooks see full output (good
  for logging hooks that want full data). Tentative: register Layer 1
  *first* in PostToolUse chain (truncate-then-decorate semantics);
  document so users can override registration order.
- **What error-pattern matches "prompt too long"?** OpenAI says
  `"context_length_exceeded"`; Anthropic uses `"prompt is too long"`;
  Qwen-plus surfaces `"Range of input length"` (observed empirically
  in Phase 3 case B). Phase 4 implements a list of patterns; tests
  parameterize them.
- **Should `tool_use` / `tool_result` always travel as pairs in
  reactive truncation?** Yes — orphan `tool_use` (no matching
  `tool_result`) breaks the LLM's expectation. Drop *pairs*, not
  individual blocks. Locked: pair-drop is the algorithm.

---

## Acceptance for Phase 4 close-out

- [ ] `oh ask` survives Phase 3 smoke case B (2× full uv.lock Read)
- [ ] `tool_result` over `--tool-result-cap` is truncated head/tail
- [ ] Provider returning `"context length exceeded"` triggers Layer 2
  retry without user-facing crash
- [ ] `tool_truncated` log event present when truncation fires
- [ ] Reactive truncation bounded (3 retries → RequestFailure re-raised)
- [ ] mypy strict + ruff clean + coverage ≥ 95 % retained

---

## Pointers

- Phase 3 retro: [`learnings/phase-3.md`](../learnings/phase-3.md) §4-5
- Codex compaction reference: [OpenAI Developers — Compaction guide](https://developers.openai.com/api/docs/guides/compaction)
- OpenHarness 3-tier reference: [`REFERENCE.md`](../REFERENCE.md) §16
- Hooks system that Layer 1 builds on: [P3-T4](../tasks/phase-3-plan.md)
