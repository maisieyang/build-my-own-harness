# Decision 34 — Eval Stage 5 Score Persistence + Version Stamping

> Created 2026-06-04 · 中文
>
> 配套读物：
> - Substrate boundary: [`decisions/31-eval-substrate-boundary.md`](./31-eval-substrate-boundary.md)
> - LLM-judge: [`decisions/32-eval-stage3-llm-judge.md`](./32-eval-stage3-llm-judge.md)
> - Cassette mode: [`decisions/33-eval-stage4-cassette.md`](./33-eval-stage4-cassette.md)

---

## 〇、Stage 5 触发点

Stage 4 cassette 解决了 **"cost + reproducibility"**：replay 0 cost +
byte-identical 输出。但 cassette **只保留 LLM 响应**——不保留 score 历史。

production engineering insight：**eval iterate 半年后想知道"我 6 月改 prompt
v1 → v2，T5 capability 从 0/3 → 2/3"**——没办法。score 跑完就丢。

Stage 5 = **每次 run 留 JSONL 痕迹 + 全部影响 score 的 axes 版本 stamp**：
- 半年后能 trace 任何 score 变化的根因（prompt 改？dataset 改？rubric 改？model 漂？）
- 可以遍历 `results/` 文件夹算 N=10 stability 趋势
- prompt A vs B 对比有可 grep 的历史依据

---

## 一、决策 D34.1-D34.7

### D34.1 — Results 格式：JSONL（每行一条 record）

**Alternatives**：
- (a) JSONL — 1 file per run, 1 metadata line + N case records
- (b) Per-run JSON — 单 JSON object 含整个 run
- (c) SQLite — queryable structured
- (d) Per-case JSONL — 1 file per case, append-only across runs

**Chosen**：(a)

**Rationale**：
- JSONL line-by-line append-safe，stream-readable，cat / grep / jq 友好
- (b) JSON 整 object 内存 friendly 但大文件解析没 streaming
- (c) SQLite overkill 单 dev project；引入 schema migration 复杂度
- (d) per-case 累积 6 个月后单文件几千行，diff 不友好

**Filename schema**: `{timestamp}_{model}_{mode}.jsonl`
- `timestamp`: `2026-06-04T16-30-00`（ISO 但 `:` 替换 `-` filename-safe）
- `model`: e.g. `qwen-plus`
- `mode`: `live` / `record` / `replay`

---

### D34.2 — 文件位置：`evals/focus_state/results/`

跟 `cassettes/` 同级 evals/ 下 consumer 目录。不进 substrate。

`.gitignore` 策略：**默认 commit**（小文件，每个 1-5 KB；半年后 trace
需要历史）；如果累积过多用户可手动 gitignore。

---

### D34.3 — Per-run 第一行 header：全 axes 版本 stamp

```json
{
  "type": "run_header",
  "started_at": "2026-06-04T16:30:00.123456+00:00",
  "completed_at": "2026-06-04T16:31:42.789012+00:00",
  "model": "qwen-plus",
  "judge_model": "qwen-plus",
  "cassette_mode": "live",
  "dataset_path": "evals/focus_state/dataset.yaml",
  "dataset_sha256": "a1b2c3...",
  "prompt_sha256": "d4e5f6...",
  "prompt_excerpt": "You watch a conversation between a user and an AI assistant. Your job is to...",
  "rubric_sha256s": {"T4": "ab12...", "T5": "cd34...", "T6": "ef56...", "T7": "gh78..."},
  "scorer_classes": ["ParseOkScorer", "GoalKeywordMatchScorer", "CapabilityAssertionsScorer", "CapabilityLLMJudgeScorer"],
  "n_cases": 8
}
```

**6 个 axes** 全 stamp：
1. **model + judge_model** — 哪个 LLM 跑的
2. **cassette_mode** — replay vs live；replay 时 score 是 cassette 录的旧值
3. **dataset_sha256** — 加新 case 或 mutate 现 case 自动检测
4. **prompt_sha256** — `FOCUS_STATE_SYSTEM_PROMPT` 改了 score 必然有变
5. **rubric_sha256s** — 4 个 LLM-judge rubric 各自 hash；改 rubric =
   score 维度变
6. **scorer_classes** — 加新 scorer 或换实现时 sample shape 一致但 dim
   集合可能不同

`prompt_excerpt` (前 200 字) 给人审 friendly 的可读 hint，不依赖 hash
对照 git history。

---

### D34.4 — Per-case 后续行：lean record

```json
{
  "type": "case_result",
  "case_id": "T7-edit-done-followup-required",
  "capability": "T7",
  "output": {"goal": "...", "next_step": "..."},
  "scores": [
    {"dim": "parse_ok", "value": 1.0, "reason": "goal parsed"},
    {"dim": "capability_T7", "value": 1.0, "reason": "ALL capability assertions passed"},
    {"dim": "capability_T7_llm_judge", "value": 1.0, "reason": "proposes verifying the log..."}
  ]
}
```

**Lean 原则**：不存 input messages —— input 在 dataset.yaml，dataset_sha256
能 trace 用的是哪个版本。**冗余信息浪费空间 + 让 diff 噪音大**。

**Score record schema**：跟 `Score` dataclass 对齐（dim / value / reason）。
不存 case_id（在外层 record 里）。

---

### D34.5 — 不做：自动 invalidate / 自动 compare / CI gate

**Stage 5 MVP 只 ship 落盘**。这些 follow-up 都不做：

- ❌ 自动检测 prompt_hash 变化 → invalidate 旧 cassette（Stage 6）
- ❌ 自动 compare 最近 N 次 run 算 trend（CLI tool, Stage 6）
- ❌ CI gate（"如果新 run capability_T7 < 上次 run → fail CI"）（Stage 6+）
- ❌ 跨 model 自动汇总（"qwen-plus 跟 deepseek-v4-flash 同 dataset 对比"）

Stage 5 = 数据有了，**怎么用是 Stage 6+ tool 的事**。

---

### D34.6 — Substrate 改动范围：runner 不写盘，spike 写

**改动 surface**：
- 新文件: `src/openharness/eval/results.py` (RunMetadata + hash helpers + write_run_results)
- spike: 跑前算 metadata, 跑后 write_run_results
- **substrate runner 不变**（D31.10.1 invariant 不破）

write_run_results 不在 runner.py 里：
- runner 关注 "load → iterate → aggregate"
- 写盘是 consumer 关注（不同 service 可能用不同存储格式）
- 这同 D33 cassette 一样—— consumer 决定 cost / persistence 策略

---

### D34.7 — Hash 算法 + 时间戳格式

**Hash**: SHA-256, hex digest. 不用 md5（防 collision），不用 git-style
short hash（避免 collision 风险）。

**时间戳**: `datetime.now(timezone.utc).isoformat()` (ISO 8601 with timezone).
Filename-safe form 用 `:` → `-` 替换。

**字段顺序**: JSON dict key 按 stable order 写 (Python 3.7+ dict insertion
order)；不依赖 sort，依赖 dataclass field order。

---

## 二、Stage 5 验收 (Acceptance)

- [ ] `src/openharness/eval/results.py` 含：
      - `RunMetadata` dataclass
      - `compute_file_hash(path)` / `compute_text_hash(s)` / `compute_rubric_hashes(rubrics)`
      - `write_run_results(path, metadata, results)`
      - `build_result_filename(metadata)`
- [ ] Spike 跑出来 `evals/focus_state/results/{timestamp}_{model}_{mode}.jsonl` 新建
- [ ] 文件第一行是 `type=run_header` 含全 6 axes hash
- [ ] 后续 8 行是 `type=case_result` 每个 case 一条
- [ ] 同 prompt + 同 dataset 两次跑：metadata 里 dataset_sha256 + prompt_sha256 + rubric_sha256s 完全一致
- [ ] mypy --strict + ruff 全 clean
- [ ] Substrate runner 0 修改

---

## 三、Future Stage 6+ hooks

- **Stage 6 — Comparison tool**: `oh eval compare results/{A}.jsonl results/{B}.jsonl`
  — auto diff 哪个 dim 变了
- **Stage 6 — Trend dashboard**: `oh eval trend --capability T7 --last 10` —
  扫 results/ 出 10 次 T7 score 趋势
- **Stage 6 — Cassette auto-invalidation**: 落盘 prompt_hash 跟 cassette
  record_time prompt_hash 对比，不一致 warn
- **Stage 6 — CI gate**: `oh eval gate --threshold 0.8 --capability T7` —
  返回非 0 退出码触发 CI fail
- **Stage 7 — Human-vs-judge calibration ritual**: per-month 30 case
  sample 人审 result，跟 LLM-judge 对照 agreement %

---

## 四、相关 doc

- 实验全过程：[`docs/ideas/eval-experiment-day1-focus-state.md`](../docs/ideas/eval-experiment-day1-focus-state.md)
- Substrate D31：[`decisions/31-eval-substrate-boundary.md`](./31-eval-substrate-boundary.md)
- LLM-judge D32：[`decisions/32-eval-stage3-llm-judge.md`](./32-eval-stage3-llm-judge.md)
- Cassette D33：[`decisions/33-eval-stage4-cassette.md`](./33-eval-stage4-cassette.md)
