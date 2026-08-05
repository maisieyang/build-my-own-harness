# Decision 50 — make completion judging `/goal`-owned

> Date: 2026-08-05
> Builds on: D48 session goal, D49 single completion controller

## Context

`/goal` initially reused the semantic gate built for the retired headless
`--goal-condition` loop. The LLM call itself remained useful, but the gate's
binary contract did not match an interactive controller:

- a real `not met` verdict and a judge timeout or parse failure both became
  `passed=False`, so a broken judge could launch up to 25 unnecessary worker
  turns;
- the judge read the entire conversation, allowing evidence from an unrelated
  earlier task to satisfy a newly set goal;
- names, optional-gate helpers, and an event-stream transcript collector kept
  the deleted headless product line alive as architecture.

## Decision

Completion judging is a service owned exclusively by `/goal`.

- `judge_goal_completion` returns `MET`, `NOT_MET`, or `ERROR`.
- Only `NOT_MET` may schedule another worker turn. `ERROR` keeps the goal
  active but pauses automation for explicit human recovery.
- Judge evidence starts at the latest matching `[goal-status] set` sentinel.
  Earlier conversation remains available to the worker but cannot satisfy the
  new completion contract.
- The judge keeps its calibrated `{"score": 0|1, "reason": "..."}` wire
  response schema. Condition and transcript are sent as a JSON data envelope;
  parse and transport failures become controller `ERROR` results.
- Long tool results retain both their diagnostic head and final-verdict tail;
  middle content is bounded so test/build summaries are not truncated away.
- Remove the old `verification` package, optional semantic-gate helper, dead
  headless event-stream transcript collector, and repository-specific pytest
  advice from generic goal prompts.
- `/clear` extinguishes an active goal before clearing conversation history;
  controller state and its durable transcript source cannot diverge.

## Consequences

The worker and judge still share an API client and default model, but not a
conversation or tool authority. The worker proposes and executes actions; the
goal controller owns evidence scope, continuation, pause, and completion.

This does not solve unattended permissions. When a worker turn emits an `ASK`
denial and the judge still returns `NOT_MET`, `/goal` now pauses instead of
spending another auto-turn. A person still needs a task-level permission policy
or ApprovalBroker before they can safely leave. Permission, sandbox, worktree
isolation, and completion remain separate contracts; the next design must
compose them at task start rather than weakening the completion judge.

Compacted or legacy snapshots that no longer contain the set sentinel fall
back to their available history so active goals remain recoverable. Current
snapshots retain the sentinel and receive the strict evidence boundary.

## Verification

- Judge unit tests distinguish `NOT_MET` from every error path.
- REPL integration proves judge errors pause without another worker turn.
- REPL integration proves pre-goal evidence is excluded.
- Goal continuation, cap, terminal sentinel, resume, and judge meta-eval remain
  covered.
- The committed replay gate remains green. Live `qwen-max` re-ratification of
  the structured input prompt is required when provider credentials are
  available.
