# Evaluation handbook

OpenHarness capability evals are manual contributor workflows. They measure
model-dependent behavior that ordinary unit tests cannot prove, and they never
run in CI or in the default test suite.

Every eval invocation must explicitly select `live`, `record`, or `replay`.
A bare command fails before it can call a model.

For a Chinese version, see [README.zh-CN.md](./README.zh-CN.md).

## Choose a validation level

Use the cheapest level that can answer the current question. Every change is
validated, but a full live eval is not part of the edit-test inner loop.

| Level | When | What to run | Cost |
|---|---|---|---|
| L0 — fast check | Every edit | Review, one reproduction, related unit tests | Seconds, no model |
| L1 — deterministic | A coherent change is ready | Targeted pytest, types, lint, cassette replay | Minutes, no model |
| L2 — targeted live | Dogfood passes | One to three affected cases with `--mode live --case` | Small model cost |
| L3 — ratification | The candidate behavior is frozen | The dataset card's full live stability procedure, then `record` | Highest cost |

The normal CI and contributor gate is:

```bash
uv run pytest -m "not integration and not eval" -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
```

## Choose a mode

| Mode | Calls the configured model | Writes cassettes | Use it for |
|---|---:|---:|---|
| `replay` | No | No | Checking loaders, scorers, and previously recorded behavior |
| `live` | Yes | No | Diagnosis, dogfood follow-up, and stability observation |
| `record` | Yes | Yes | Replacing the committed baseline after ratification |

`record` is a baseline-maintenance operation, not an ordinary test command.
Review the resulting cassette diff before committing it. Replay proves that a
recorded response still passes the current scorer; it does not prove that a
changed prompt, tool description, context assembly path, judge, model, or
provider still behaves correctly live.

## Select the model

Manual evals resolve the model in this order:

1. `--model MODEL` for this command;
2. `OPENHARNESS_MODEL` in the process environment;
3. `OPENHARNESS_MODEL` in the project `.env`;
4. otherwise, fail with an explicit configuration error.

There is no runtime fallback to a historical reference model. The reference
policy belongs to each capability's `dataset_card.md`. Because model identity
is part of a cassette key, replaying an older baseline may require an explicit
model that matches that card:

```bash
uv run oh dev eval tool_choice --mode replay --model qwen-max
```

`live` and `record` also require the provider configuration in the project
`.env`. `replay` does not make a provider request.

## Standard workflow

### 1. Iterate with deterministic checks

While the implementation is still moving, use a direct reproduction and the
related unit tests. Do not pay for a full live run after every small edit.

```bash
uv run pytest tests/tools/test_grep.py -q
```

### 2. Dogfood the affected behavior

Exercise the real REPL workflow. Obvious routing, timeout, rendering, or state
machine problems should be fixed before eval.

### 3. Run a targeted live smoke

Once dogfood is credible, run only the affected case or cases:

```bash
uv run oh dev eval error_feedback \
  --mode live \
  --case A6-grep-launch-denied
```

`--case` filters the dataset before inference, so unselected cases do not call
the model. An unknown case identifier fails and prints the available catalog.

### 4. Ratify a frozen candidate

Read the capability's dataset card, then run its declared full live procedure,
reference policy, sample count, and pass bar. Do not infer a universal `N` from
another capability.

```bash
uv run oh dev eval error_feedback --mode live
```

### 5. Record and verify the baseline

Only after the live result meets the dataset contract should the reference
cassette be replaced:

```bash
uv run oh dev eval error_feedback --mode record
uv run oh dev eval error_feedback --mode replay
git diff -- evals/error_feedback
```

Run every committed replay gate manually when a scorer, dataset loader,
cassette implementation, or shared eval substrate changes:

```bash
uv run pytest -m eval -q --no-cov
```

The aggregate gate uses the ratified cassette identity declared by each
dataset contract; it is intentionally independent of the current `.env`.

## Command reference

```bash
# Discover all capability evals.
uv run oh dev eval --help

# Inspect one eval's options.
uv run oh dev eval error_feedback --help

# Deterministic replay.
uv run oh dev eval error_feedback --mode replay

# One live case.
uv run oh dev eval error_feedback --mode live --case CASE_ID

# Full live dataset.
uv run oh dev eval error_feedback --mode live

# Record the ratified response baseline.
uv run oh dev eval error_feedback --mode record

# Temporarily select another model.
uv run oh dev eval error_feedback --mode live --model MODEL
```

## Capability catalog

| Eval | What it measures | Contract |
|---|---|---|
| `focus_state` | Extracting the current goal and next step from conversation state | [dataset card](./focus_state/dataset_card.md) |
| `tool_choice` | Selecting, parameterizing, and withholding tools | [dataset card](./tool_choice/dataset_card.md) |
| `error_feedback` | Recovering correctly after tool failures | [dataset card](./error_feedback/dataset_card.md) |
| `skill_trigger` | Deciding whether and which skill to load | [dataset card](./skill_trigger/dataset_card.md) |
| `memory_decision` | Deciding whether and how to write project memory | [dataset card](./memory_decision/dataset_card.md) |
| `memory_read` | Deciding whether and which project memory to read | [dataset card](./memory_read/dataset_card.md) |
| `memory_compact` | Preserving relevant facts while excluding noise during compaction | [dataset card](./memory_compact/dataset_card.md) |
| `permission_review` | Reviewing exact permission requests without broadening authority | [dataset card](./permission_review/dataset_card.md) |
| `verify_judge` | Independently deciding whether a goal is actually complete | [dataset card](./verify_judge/dataset_card.md) |

The catalog is navigation. The linked dataset card is authoritative for the
capability claim, reference policy, scorers, pass bar, stability requirement,
known gaps, and re-ratification procedure.

## Artifact layout

```text
evals/<capability>/
├── dataset.yaml       # versioned cases and expected behavior
├── dataset_card.md    # evaluation contract and pass bar
├── cassettes/         # model responses written by record
└── results/           # retained run evidence when the eval persists it
```

Production runner and scorer code lives under `src/openharness/eval/`. The
legacy `scripts/spike_*_eval.py` files are thin launch adapters behind
`oh dev eval`; contributors should use the CLI shown in this handbook.

## When an eval changes

- Runner, loader, scorer, or cassette code: add deterministic tests and run
  the affected replay plus the aggregate replay gate.
- Dataset or scorer contract: update its dataset card and replay the committed
  artifacts. Record only if the accepted baseline intentionally changes.
- Prompt, tool description, context assembly, judge behavior, model, or
  provider: run targeted live checks and then the dataset card's full live
  re-ratification procedure.
- A new capability eval: add its dataset, dataset card, runner/scorers, CLI
  registration, manual-safety tests, and replay gate.

## Troubleshooting

### Missing `--mode`

The command is intentionally fail-closed. Choose `replay`, `live`, or `record`
explicitly.

### Missing model configuration

Set `OPENHARNESS_MODEL` in `.env`, or pass `--model` for this run. The eval will
not silently select a historical cassette.

### Cassette missing

Confirm that the selected model matches an existing cassette and the dataset
card's reference policy. A missing cassette is not permission to record a new
baseline automatically.

### Replay passes but live fails

Replay only proves the stored response and scorer still agree. Treat the live
failure as current behavior and investigate prompt, provider, model, context,
and tool-schema changes before recording anything.

### A case is visibly broken before eval

Return to L0/L1. Fix the deterministic or dogfood failure first; eval is not a
substitute for obvious product validation.
