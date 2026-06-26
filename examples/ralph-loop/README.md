# ralph-loop — a Boris-style loop you can watch converge

A 38-line bash "Ralph loop" that drives **stock `claude -p`** to fix a broken
module until a deterministic test gate goes green — no human prompting each turn.
This is the loop-engineering spike behind the `loop-runtime` epic
([`tasks/loop-runtime-plan.md`](../../tasks/loop-runtime-plan.md)); once **L1**
(`oh -p` headless entry) lands, `ralph.sh` gets re-pointed from `claude` to `oh`.

> **Not an extension artifact** like the other `examples/` (commands/skills/
> bundles/hooks). It's a runnable demonstration of the outer loop itself.

## What's here

| File | Role |
|---|---|
| `ralph.sh` | The loop: gate → hand to `claude -p` → re-gate → stop. The whole point. |
| `mathy.py` | Source under repair. **Ships buggy** (`a-b`, `%2==1`) so a run shows red→green. |
| `test_mathy.py` | The spec. Its assertions are the gate — the loop never weakens them. |
| `pytest.ini` | Pins this dir as pytest's rootdir, isolated from the repo's `testpaths=["tests"]`. |

## Run it

```bash
cd examples/ralph-loop
bash ralph.sh 5          # max 5 iterations; converges in 1 on this case
git checkout mathy.py    # reset to buggy to run again
```

Needs `claude` (>= 2.x), `uvx`, `jq` on PATH. Each inner iteration is capped at
`--max-budget-usd 0.50`.

## The one thing to notice

The inner `claude -p` may *say* "the fix is in but I need approval to run pytest"
— it can't self-verify, because `python3 -m pytest` isn't in its `--allowedTools`.
**The loop converges anyway**, because the verdict isn't the model's word — it's
the exit code of the *outer* `uvx pytest` at the top of the next iteration. That
gap between "the model thinks it's done" and "the gate proves it's done" is the
line between writing a skill and writing a loop.

This is the **hard-gate** form. Claude Code's in-TUI `/goal` command is the
**soft-gate** cousin: its evaluator judges from what the model *surfaced* in the
transcript, not from an independent exit code — convenient, but pollutable by the
model's self-report. The `loop-runtime` L3 verification gate is deliberately the
hard-gate kind (see `tasks/loop-runtime-plan.md` §4 invariant 1).

## How the chairs map (from `docs/ideas/from-prompt-to-loop-2026.md`)

| In `ralph.sh` | Chair / invariant | Flag / mechanism |
|---|---|---|
| outer `uvx pytest` exit code | verification | the gate is a command, not a prompt |
| `--allowedTools` + `--permission-mode acceptEdits` | permission (fail-closed) | allowlist; unlisted = denied |
| no `--resume`, re-feed GOAL + failure | fresh-context | new session each iteration |
| `for … MAX_ITER` + `--max-budget-usd` | hard rail | iteration cap AND budget cap |
| the GOAL string (written once) | planning | still human — `oh`'s L5 would offload it |
