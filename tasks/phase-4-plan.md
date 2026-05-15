# Phase 4 Implementation Plan — Compaction (Context Management)

> Phase 1-3 archive: [`tasks/plan.md`](./plan.md) / [`phase-2-plan.md`](./phase-2-plan.md) / [`phase-3-plan.md`](./phase-3-plan.md).
> This file is the Phase 4 active plan.
>
> Boundary contract: [`decisions/10-phase-4-boundary.md`](../decisions/10-phase-4-boundary.md).
> Framing basis: Phase 3 retro §4-5 + Codex / OpenHarness WebSearch.

## Overview

**Phase 4 goal**: Make `oh ask` survive long conversations and large
tool outputs without `RequestFailure(400, "context length exceeded")`.
Two-layer defense:

```
Layer 1: per-tool-result truncation       (Codex style, PostToolUse hook)
Layer 2: reactive prompt-too-long retry   (engine-internal, drop oldest pair)
```

**Total scope**: ~5-6 days, 5 capabilities, ~15-20 commits.

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/10-phase-4-boundary.md`](../decisions/10-phase-4-boundary.md) | D14.1 two-layer defense; D14.2 tiktoken + byte-ratio fallback; D14.3 CLI/Settings shape; D14.4 `tool_truncated` log event; D14.5 reactive bounded to 3 retries |

## Task list

### P4-T1: Token counter — `compaction/tokenize.py` 🔜 NEXT

**Acceptance**:
- [ ] `count_tokens(text: str, model: str) -> int` — single public function
- [ ] `tiktoken.encoding_for_model(model)` when available
- [ ] Graceful fallback to `len(text.encode("utf-8")) // 4` for unknown
  models (Qwen / Anthropic-native — important for our Phase 1 Qwen target)
- [ ] `tiktoken` added to `pyproject.toml` dependencies
- [ ] Tests:GPT-4o accuracy ≥ within 5 % of tiktoken truth; Qwen falls
  back; empty string returns 0; very large string benchmarked < 100 ms
- [ ] mypy strict + ruff clean

**Files**:
- `src/openharness/compaction/__init__.py` (new package)
- `src/openharness/compaction/tokenize.py` (new)
- `tests/compaction/test_tokenize.py` (new)
- `pyproject.toml` (+`tiktoken`)

**Sub-units** (small task, 1 commit):
- 1a — `tiktoken` dep + `count_tokens` + tests + commit

---

### P4-T2: Layer 1 — `TruncateToolResultHook`

**Acceptance**:
- [ ] `compaction/truncate.py` — `head_tail_truncate(text, cap_tokens,
  model) -> str` — 50/50 head+tail with middle marker
- [ ] `compaction/hook.py` — `TruncateToolResultHook(cap_tokens, model)`:
  satisfies `Hook` protocol, modifies `ctx.result.output` when too big,
  uses `HookResult.modify_output(...)`
- [ ] `tool_truncated` log event (info) — fields:`tool_use_id`,
  `original_tokens`, `truncated_tokens`, `cap_tokens`
- [ ] Marker text:`"\n... [truncated <N> tokens] ...\n"` — N is the
  number of tokens removed (not bytes), so it matches what LLM sees
- [ ] Tests:
  - small output unchanged (under cap)
  - large output split 50/50 + marker present + total ≤ cap
  - `tool_truncated` log fires with correct fields
  - hook returns `None` (passthrough) when no truncation needed
- [ ] mypy strict + ruff clean

**Files**:
- `src/openharness/compaction/truncate.py` (new)
- `src/openharness/compaction/hook.py` (new)
- `src/openharness/compaction/__init__.py` (exports)
- `tests/compaction/test_truncate.py` (new)
- `tests/compaction/test_hook.py` (new)

**Sub-units**:
- 2a — `head_tail_truncate` pure function + tests
- 2b — `TruncateToolResultHook` wraps the function + tests
- 2c — Hook emits `tool_truncated` log event + extends 8-log inventory

---

### P4-T3: Layer 2 — reactive prompt-too-long handling

**Acceptance**:
- [ ] `api/errors.py` — new `PromptTooLongFailure(RequestFailure)`
  subclass for the specific error pattern
- [ ] `api/client.py` `_translate_openai_error` — when message matches
  one of the known patterns (`"context_length_exceeded"`, `"prompt is
  too long"`, `"Range of input length"`, …), raise
  `PromptTooLongFailure` instead of generic `RequestFailure`
- [ ] `engine/query.py` — within the per-turn block,catch
  `PromptTooLongFailure` once, drop the **oldest tool_use/tool_result
  pair** from messages, retry the same turn. Bounded by
  `_REACTIVE_TRUNCATE_MAX = 3`. After 3 retries → re-raise so the named
  CLI except branch catches it.
- [ ] New log event `reactive_truncate` (warning) — fields:`turn`,
  `attempt`, `dropped_tool_use_ids` (list)
- [ ] Tests:
  - Pattern-match unit test for each known error string
  - Engine integration:stub client raises PromptTooLongFailure twice
    then succeeds → messages got 2 pairs dropped + retry succeeded
  - Bounded:3 consecutive errors → 4th retry re-raises
  - Orphan safety:dropping leaves no `tool_use` without matching
    `tool_result`
- [ ] mypy strict + ruff clean

**Files**:
- `src/openharness/api/errors.py` (+`PromptTooLongFailure`)
- `src/openharness/api/client.py` (+pattern match)
- `src/openharness/engine/query.py` (+except branch + pair-drop)
- `src/openharness/engine/messages.py` (+`drop_oldest_tool_pair` helper)
- `tests/api/test_translation_errors.py` (+PromptTooLong)
- `tests/engine/test_reactive_truncation.py` (new)

**Sub-units**:
- 3a — `PromptTooLongFailure` + translation pattern list + tests
- 3b — `drop_oldest_tool_pair` messages helper + tests
- 3c — `run_query` integration:catch + drop + retry + log + tests
- 3d — Bounded retries:re-raise after 3 + tests

---

### P4-T4: CLI / Settings integration + end-to-end smoke

**Acceptance**:
- [ ] `Settings.tool_result_cap: int = 10000` (env
  `OPENHARNESS_TOOL_RESULT_CAP`)
- [ ] `Settings.auto_truncate: bool = True` (env
  `OPENHARNESS_AUTO_TRUNCATE`)
- [ ] `--tool-result-cap N` and `--no-auto-truncate` CLI flags
- [ ] `cli.py _run_ask`:if `auto_truncate`, register
  `TruncateToolResultHook` on the QueryContext's hook_registry by
  default. If `--no-auto-truncate`, skip registration. Layer 2 is
  always on (no flag — defensive baseline).
- [ ] End-to-end smoke test mirroring Phase 3 smoke case B:run a real
  CLI flow that would have blown context without compaction, assert
  it succeeds + emits `tool_truncated` log
- [ ] README "Phase 3 features" section gets a "Phase 4 — compaction"
  bullet
- [ ] Tests:CLI flag → Settings → hook registration chain (mirror
  P3-T5.5e `TestLoggingFlags`)
- [ ] mypy strict + ruff clean

**Files**:
- `src/openharness/config/settings.py` (+2 fields)
- `src/openharness/cli.py` (+2 flags + hook registration)
- `tests/cli/test_cli.py` (+`TestCompactionFlags`)
- `tests/compaction/test_smoke.py` (new — end-to-end)
- `README.md` (+ bullet)

**Sub-units**:
- 4a — Settings + CLI flags + tests
- 4b — `_run_ask` default hook registration + tests
- 4c — End-to-end smoke (stub provider yields oversized tool_result,
  truncation fires, run completes)
- 4d — README update

---

### P4-T5: Coverage + retro

**Acceptance**:
- [ ] `compaction/` module ≥ 95 % coverage
- [ ] Total coverage stays ≥ 95 %
- [ ] `learnings/phase-4.md` written — focus:two-layer defense, why
  no LLM-summarizer, Codex/OpenHarness comparison
- [ ] Phase 4 DoD checklist all green

**Sub-units**:
- 5a — Coverage gap audit + close
- 5b — `learnings/phase-4.md`
- 5c — DoD closeout + plan checkboxes

---

## Checkpoints

After each capability:**human review** of the resulting trace / log
event(s). Phase 4 is small enough that review cadence is per-task,
not mid-task.

## Risks

| Risk | Mitigation |
|---|---|
| `tiktoken` doesn't support Qwen → wrong token count off by 30+ % | Byte-ratio fallback (D14.2);Layer 2 reactive truncation catches misestimates |
| Pattern match for "prompt too long" misses a provider variant | Pattern list extensible via `_PROMPT_TOO_LONG_PATTERNS` constant in `api/client.py`;tests parameterize known providers |
| Dropping oldest tool pair causes LLM to "lose context" mid-task | Pair-drop preserves causality (tool_use never orphaned from tool_result);after 3 retries, surface failure rather than silently degrade |
| `TruncateToolResultHook` registered before user hooks distorts user-hook view of output | D14 sub-decisions §1:document hook ordering;users can override registration order |

## Risks specifically NOT mitigated (Phase 5+)

- LLM "forgets" a file's middle because we always truncate middle.
  Mitigation deferred:Phase 5+ may add a `read_with_focus(path,
  search_pattern)` tool variant.
- A single oversized prompt (user pastes 50K tokens) blows context
  before any tool runs. Layer 2 truncates oldest tool pair, but if no
  tool ran yet, nothing to drop → bounded retry exhausts → user gets
  `Request failed (HTTP 400)`. Acceptable.

## Pointers

- Boundary: [`decisions/10-phase-4-boundary.md`](../decisions/10-phase-4-boundary.md)
- Phase 3 retro (relevant: §4 contract predictions, §5 input for Phase 4):
  [`learnings/phase-3.md`](../learnings/phase-3.md)
- Codex compaction reference (D14.1 source): [Codex docs](https://developers.openai.com/api/docs/guides/compaction)
- OpenHarness 3-tier reference (D14.1 deferred levels): [`REFERENCE.md`](../REFERENCE.md) §16
