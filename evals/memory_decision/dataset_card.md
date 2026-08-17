# memory_decision Eval Dataset Card

## Reference policy

The reference model is the model configured through the normal
`OPENHARNESS_*` environment, currently `qwen3.7-max`. Model or prompt changes
require a manual live re-ratification followed by record and replay. Replay
validates the stored behavior and scorers; it does not replace live evidence.

## Capability claim

This eval verifies the production typed-memory decision surface:

```text
user fact
   ↓
decide whether it deserves durable memory
   ↓
MemoryUpsert or deliberate skip
   ↓
harness persists the record and regenerates the index
```

The model decides semantics: whether to remember and which memory category
fits. The harness owns paths, schema validation, atomic writes, deduplication,
and index maintenance. The eval executes the real typed Memory tools against an
isolated filesystem store; it does not simulate the retired Write/Edit
protocol.

## Input specification

The dataset contains six synthetic single-turn samples:

- two cold-start cases with an empty store;
- three warm-start cases with existing typed records;
- one trivial case that must not create durable memory.

Each request receives the production Memory prompt, the generated discovery
index, and the schemas for `MemoryList`, `MemoryShow`, `MemoryUpsert`, and
`MemoryDelete`. A case may take several model turns because a model can inspect
the catalog before deciding.

| Field | Purpose |
|---|---|
| `case_id` | Stable cassette key. |
| `capability` | Selects the memory-type judge rubric. |
| `shape` | Cold, warm, or trivial-skip observation slice. |
| `user_msg` | The single user fact or question. |
| `expect_write` | Historical field name; now means “expect `MemoryUpsert`”. |
| `expected_memory_type` | Canonical category for judge calibration. |
| `pre_populated_files` | Existing typed records seeded before the run. |

## Judgment specification

Four independent dimensions are scored:

| Dimension | Scorer | Claim |
|---|---|---|
| `memory_decision_judgment` | `JudgmentScorer` | The model upserts a durable fact and skips an ephemeral one. |
| `memory_decision_payload` | `PayloadValidScorer` | The upsert contains non-empty `name`, `description`, `type`, and `body`, with a valid type. |
| `memory_decision_persistence` | `PersistenceIntegrityScorer` | The chosen record exists after real execution and every seeded record retains its exact content fingerprint. |
| `memory_decision_type_judge` | `MemoryTypeLLMJudgeScorer` | The selected category is defensible under the capability rubric. |

A case passes only when every applicable dimension is `1.0`. `NA` is allowed
only when a dimension is inapplicable, such as payload validation on the
trivial-skip case. The warm-start pass bar is at least 80%; with the current
three warm cases that means 3/3.

## Running the eval

The eval is always manually triggered:

```bash
uv run oh dev eval memory_decision --mode live
uv run oh dev eval memory_decision --mode record
uv run oh dev eval memory_decision --mode replay
```

Run one case while developing:

```bash
uv run oh dev eval memory_decision --mode live --case M3-warm-correction
```

Do not record a failing live run. After all live cases pass, record the new
reference cassettes, then replay them to validate the deterministic gate.
Replay executes the recorded typed Memory calls against a fresh seeded fixture
before scoring, so persistence checks observe current store behavior and exact
seed-content fingerprints without making a model request.

## Deliberate boundaries

This dataset does not claim to cover conflict resolution, cross-session recall,
concurrent writes, memory deletion, secret handling, or long-term aging. Those
require different inputs and consumers and should be added as separate cases or
capabilities when dogfood provides a concrete failure.

Dataset discipline:

- do not delete a failing case to raise the score;
- do not weaken an assertion to accommodate a known defect;
- add a case only with a stable capability claim and observable outcome;
- update live evidence whenever prompt, tool descriptions, context assembly, or
  the reference model changes.
