# Decision 33 — Eval Stage 4 Cassette Mode (Record / Replay / Live)

> Created 2026-06-04 · 中文
>
> 配套读物：
> - Substrate boundary：[`decisions/31-eval-substrate-boundary.md`](./31-eval-substrate-boundary.md)
> - Stage 3 LLM-judge：[`decisions/32-eval-stage3-llm-judge.md`](./32-eval-stage3-llm-judge.md)
> - 实验全过程：[`docs/ideas/eval-experiment-day1-focus-state.md`](../docs/ideas/eval-experiment-day1-focus-state.md)

---

## 〇、Stage 4 触发点

Day 1 + Stage 1/2/3 累积跑了 18 次 spike。每次都 LIVE 真打 LLM：
- 主 inference：8 case × 1 call = 8 calls
- LLM-judge：4 case (T4/T5/T6/T7) × 1 call = 4 calls
- **每次 12 LLM calls，约 0.20 USD + 1-2 分钟**

如果接 CI gate / 跑 nightly stability profile / 反复 iterate prompt，这个 cost
+ stochasticity 不可持续。

**Cassette mode** = 一次录制响应（vinyl record，retain 模拟时代术语），
后续 replay 完全 deterministic + 0 cost。**production eval craft 的必经路**。

---

## 一、架构核心：cassette boundary 在 LLM call 抽象层

### D33.1 — Cassette 两个边界：infer_focus_state 输出 + judge summarize 原文

**Alternatives**：
- (a) HTTP-level (vcrpy)
- (b) `api_client.stream_message` (event-iterator level)
- (c) `summarize()` 返回 str (str-level)
- (d) `infer_focus_state` + judge `summarize` 两个独立 cassette (高层级)

**Chosen**：(d)

**Rationale**：
- (a) vcrpy 跟 OpenAI async streaming SDK 有兼容性 risks，需要额外配置
- (b) 要 serialize `ApiStreamEvent` (含 discriminated union)，复杂度高
- (c) infer_focus_state 内部调 summarize，但**也内部 parse JSON 成 FocusState**——
  cassette 在 summarize 层会让 replay 时还要重做 JSON parse + tolerant fence
  strip。reproducibility 不完美
- (d) **每个 LLM call 抽象 cassette 自己的输出形态**：
  - infer cassette = `{goal, next_step}` JSON（FocusState 已 parse 完）
  - judge cassette = raw str（judge JSON 由 scorer 自己 parse）
  分别在最合适抽象层 capture，replay 时跳过所有中间步骤

**Cassette boundary 几何对应**：

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: LLM HTTP API (deepseek/qwen endpoint)              │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: OpenAICompatibleApiClient.stream_message            │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: summarize() — collect deltas → raw str             │
├──────── ★ Stage 4 cassette boundary for JUDGE call ─────────┤
│ Layer 4: infer_focus_state → parse FocusState               │
├──────── ★ Stage 4 cassette boundary for INFER call ─────────┤
│ Layer 5: scorer (CapabilityLLMJudgeScorer parses judge raw) │
├─────────────────────────────────────────────────────────────┤
│ Layer 6: runner / spike entry                                │
└─────────────────────────────────────────────────────────────┘
```

---

### D33.2 — 3 modes：record / replay / live

| Mode | LLM call? | Save cassette? | Use case |
|---|---|---|---|
| `live` (默认) | ✓ | ✗ | dev/iterate, 不动 cassette 状态 |
| `record` | ✓ | ✓ (覆盖) | 新建/刷新 cassette baseline |
| `replay` | ✗ | ✗ | CI gate / 复跑历史 / 0 cost stability check |

**replay 缺 cassette 时的行为**：raise `FileNotFoundError` 含 "run record mode
first" 提示。**不 silently fall back to live**——那会让 CI 在没察觉时变贵。

---

### D33.3 — 文件结构：每 (model, kind, case) 一个 JSON 文件

```
evals/focus_state/cassettes/
├── deepseek-v4-flash/
│   ├── infer/
│   │   ├── T1-tool-name-abstraction.json
│   │   ├── T2-tool-input-refines-intent.json
│   │   └── ...
│   └── judge_T4/
│       └── T4-tool-error-recovery.json   ← 仅 4 个文件 (T4/T5/T6/T7 各 1)
│   └── judge_T5/
│       └── T5-tool-chain-high-level.json
│   ...
└── qwen-plus/
    └── ... (same structure)
```

**为什么 1 case 1 file**：
- 加新 case = 加文件，不动现有
- diff 干净，PR review 友好
- record mode 不会意外破坏其他 case 的 cassette

**JSON schema** (每个 cassette 文件)：

```json
{
  "case_id": "T1-tool-name-abstraction",
  "model": "deepseek-v4-flash",
  "kind": "infer",
  "recorded_at": "2026-06-04T16:30:00Z",
  "request_summary": "first 200 chars of input messages, for human eyeballing",
  "response": {
    "goal": "...",
    "next_step": "..."
  }
}
```

`response` 字段对 infer cassette 是 dict（FocusState 已 serialize），对 judge
cassette 是 str（raw judge JSON）。

---

### D33.4 — Substrate 改动范围：cassette.py 新文件 + runner 加 2 参 + scorer 加 2 参

**改动 surface**：
- 新文件：`src/openharness/eval/cassette.py` (CassetteMode / CassetteKey /
  CassetteStore + 2 cassetted helper functions)
- runner.py：`run_eval` 加 `cassette_root` + `cassette_mode` 两个可选参数
  （默认 None / "live"，向后兼容）
- scorers.py：`CapabilityLLMJudgeScorer.__init__` 加 `cassette_store` +
  `cassette_mode` 两个可选参数
- spike：env var `OPENHARNESS_EVAL_MODE` 控制模式 + 自动构造 cassette path

**substrate Protocol 不变**（D31.3 锁）。**Sample / Score 不变**（D31.4 锁）。

---

### D33.5 — Cassette key 不含 prompt hash：dataset version 跟 cassette lifecycle 解耦

**Cassette key** = `(case_id, model, kind)`。**不含** prompt_hash / max_tokens /
其他参数。

**Rationale**：
- Stage 4 cassette 的目标是 "复现一次 specific run 的 LLM 行为"。如果 key
  含 prompt hash，则任何 prompt 改动都让 cassette 失效——但 prompt 改动是
  Stage 4 重新 record 的明确触发点，不是隐式
- 用户 record 之后 prompt 改了：replay 会**返回旧 prompt 的响应**——这是
  feature 不是 bug（让你看到 prompt 改动跟旧 response 的对比）
- 若想做 "prompt 改动 → cassette 自动 invalidate"，是 Stage 5+ 的事（score
  落盘 + prompt_hash stamp）

**用户工作流**：
```
1. 改 prompt
2. 跑 record mode → 覆盖 cassette
3. 跑 replay mode (CI / iteration) → 用新 cassette
```

简单清晰，无 surprise。

---

### D33.6 — Anti-scope：4 件不做

- ❌ 不 cassette streaming events（D33.1 已 lock cassette 在更高抽象层）
- ❌ 不做 cassette 自动失效逻辑（D33.5）
- ❌ 不做 cassette 跨 model 复用（每 model 独立 dir）
- ❌ 不上 CLI 子命令——`OPENHARNESS_EVAL_MODE` env var 控制就够，Stage 5
  会接 `oh eval --mode replay` 进 CLI

---

## 二、Stage 4 验收 (Acceptance)

- [ ] `src/openharness/eval/cassette.py` 含 `CassetteMode` / `CassetteKey` /
      `CassetteStore` + 2 cassetted helper：
      - `cassetted_infer_focus_state(mode, store, case_id, ...)` → FocusState
      - `cassetted_judge_call(mode, store, case_id, capability, ...)` → str
- [ ] `run_eval(dataset_path, scorers, client, model, *, cassette_root=None, cassette_mode="live")`
- [ ] `CapabilityLLMJudgeScorer(api_client, model, *, cassette_store=None, cassette_mode="live")`
- [ ] Spike 支持 env var `OPENHARNESS_EVAL_MODE=record|replay|live`
- [ ] Acceptance test：
      1. `OPENHARNESS_EVAL_MODE=record` 跑一次 → 12 cassette 文件创建
      2. `OPENHARNESS_EVAL_MODE=replay` 跑一次 → **0 LLM call**, byte-identical
         output 跟 record 那次
      3. mypy --strict + ruff 全 clean
      4. 现有 LIVE 行为不变（默认 mode）

---

## 三、Future Stage 5+ hooks

- Stage 5 — CLI 子命令：`oh eval focus_state --mode replay` 对应 env var
- Stage 5 — Score 落盘 + version stamping：score record 跟 cassette 文件
  paired
- Stage 6 — Automatic cassette invalidation：score record 检测 prompt_hash
  漂移时自动重 record
- Phase 17+ — extract.py / compact.py cassettes：复用 CassetteStore 抽象，
  不动 substrate

---

## 四、相关 doc

- 实验全过程：[`docs/ideas/eval-experiment-day1-focus-state.md`](../docs/ideas/eval-experiment-day1-focus-state.md)
- 6 月 4-Milestone 路径：[`docs/ideas/eval-mentor-playbook.md`](../docs/ideas/eval-mentor-playbook.md) §八
- Substrate boundary：[`decisions/31-eval-substrate-boundary.md`](./31-eval-substrate-boundary.md)
- Stage 3 LLM-judge：[`decisions/32-eval-stage3-llm-judge.md`](./32-eval-stage3-llm-judge.md)
