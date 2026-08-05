# Decision 49 — retire the headless repair-loop product line

> Date: 2026-08-05
> Supersedes: the shipped surfaces from loop-runtime L3-L9; preserves D48 `/goal`

## Context

The completion design arrived in three steps. On 2026-07-01, `--verify` added a
deterministic command gate to headless `oh ask -p`; `--max-iter` then rebuilt a
fresh context after each failed gate. Later that day, `--goal-condition` added
an LLM judge for criteria that could not be reduced to an exit code. At that
point the desired product shape was still described as an unattended outer
loop.

On 2026-07-28, D48 made the actual interaction contract explicit: `/goal`
continues the same conversation, evaluates accumulated evidence after every
assistant turn, and feeds checker feedback back into that session. This is not
another spelling of the headless loop. It moves human supervision from every
turn to the task boundary while preserving the context in which the task was
defined.

Keeping both designs made the public surface and project narrative imply three
equal autonomy products even though only `/goal` represents the intended
completion model.

## Decision

`/goal` is the only completion controller.

- Remove `--verify`, `--verify-timeout`, `--goal-condition`,
  `--goal-condition-timeout`, `--max-iter`, `--decompose`, and `--resume-run`.
- Remove the fresh-context repair loop, command gate, repair prompt builder,
  goal decomposer, autopilot queue, run journal, `oh autopilot`, and `oh run`.
- Keep `oh ask -p` as a single-run primitive for scripts, CI, and benchmarks.
- Keep `--isolate`, sandbox, worktree, and `RunSession` as completion-neutral
  execution primitives.
- Keep the independent goal judge, transcript rendering, judge evals, goal
  sentinels, resume, statistics, and the auto-turn cap because D48 uses them
  directly.

Historical plans and dogfood records remain evidence of the design journey;
they are not current product documentation.

## Consequences

The working model still runs executable checks when the goal asks for them, and
the independent judge sees their transcript evidence. `/goal` does not
currently execute a second, deterministic operating-system oracle. If that is
needed later, it must enter as a `/goal`-owned completion contract rather than
reviving a parallel headless product line.

Permission, sandbox, worktree isolation, and completion remain orthogonal. This
decision does not claim that a human can always walk away: unattended
permission policy remains the next material boundary.

D50 subsequently removed the preserved judge's legacy gate abstraction: judge
errors now pause the controller, and each goal is evaluated only against
evidence produced after that goal was set.

## Verification

- Retired flags and subcommands are absent from CLI help and production code.
- `/goal` judge, continuation, terminal sentinel, cap, and resume tests remain.
- Single-shot JSON/stream-json and `--isolate` remain covered.
- Full pytest, `mypy --strict`, Ruff lint, and Ruff format gates pass.
