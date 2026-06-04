# Decision 32 — Eval Stage 3 LLM-judge Scorer

> Created 2026-06-04 · 中文
>
> 配套读物：
> - 上一级 substrate boundary：[`decisions/31-eval-substrate-boundary.md`](./31-eval-substrate-boundary.md)
> - Day 1 + Stage 1/2 实验：[`docs/ideas/eval-experiment-day1-focus-state.md`](../docs/ideas/eval-experiment-day1-focus-state.md)
> - Stage 2 N=3 数据揭示的 6 个 brittleness 维度：[`evals/focus_state/dataset_card.md`](../evals/focus_state/dataset_card.md) §Stage 3 LLM-judge 待解决

---

## 〇、Stage 3 触发点 + 范围

Stage 2 N=3 数据明确了 substring assertion 的 2 类 semantic 上限：

1. **Symbol presence 二义性**（T5/T6）：goal/next_step 包含某个 forbidden symbol，
   是 "task subject"（真 fail）还是 "contextual reference"（false-positive
   fail）？substring 不区分
2. **Semantic synonym distribution**（T4/T7）：LLM 用同一语义不同措辞表达
   recovery / verification 时，substring 列穷举不全；跨语言（中文 / 英文）
   也是同一类问题

Stage 3 解决：在 4 个 brittle capability 上加 LLM-judge scorer。**Stage 1
substrate boundary D31.3 + D31.7 已经 hook 这件事**——加 scorer 0 修改 runner。

---

## 一、决策 D32.1-D32.7

### D32.1 — Selective coverage：T4 / T5 / T6 / T7（不是 8 个全 cover）

**Alternatives**：
- (a) Selective：仅 4 个 brittle 的（T4/T5/T6/T7）
- (b) Comprehensive：8 个 capability 全 LLM-judge
- (c) Minimal：仅 T5 + T7

**Chosen**：(a)

**Rationale**：
- T1 / T2 (rock solid 100% Stage 2 N=3) + T3 / T8 (67% 是 deepseek noise
  不是 substring 上限) 都已 production-grade，再上 LLM-judge 是 over-engineering
- 选 (b) 让 LLM cost 翻倍 + 引入 judge stochasticity 到无需的维度
- 选 (c) 把 T4 brittleness（最严重的，1/3）留下未修

**Cost 预算**：8 case × 4 rubrics activated × 1 judge call = ~4 judge calls/run
（其他 4 case 走 default no-rubric 返回 "NA"）。约 0.05-0.10 USD/run。

**Future**：Stage 4+ 若 T1/T2/T3/T8 出现新 brittleness，加 rubric 即可，
**scorer 类 + 注册表机制都不改**。

---

### D32.2 — Rubric 位置：`src/openharness/eval/rubrics.py` registry

**Alternatives**：
- (a) `rubrics.py` Python dict `{capability_id: rubric_str}`
- (b) `evals/focus_state/rubrics.yaml` 同 dataset 目录
- (c) Per-sample `judge_rubric` field 在 dataset.yaml

**Chosen**：(a)

**Rationale**：
- Rubric 是 **per-capability** 不变量，跟 Sample (per-case) 分离——选 (c)
  让同 capability 不同 sample 的 rubric 漂移失控
- 选 (b) 要写 loader + format validator，**substrate code 复杂度上升**没有
  对应 craft 收益（rubric iterate 频率比 dataset 低）
- 选 (a) 跟 substring `capability_assertions` 在 Python 里 model-agnostic
  的姿势一致，code review 时 rubric 跟 scorer 并排可见

**Iterate 规则**：rubric 改动 = git commit (改 code 同样的 ceremony)。
playbook §六 6.3 "judge prompt iterate 是 craft 不是 hot patch" 在这条
落地。

---

### D32.3 — Scorer 运行时状态：constructor 注入 api_client + model

```python
class CapabilityLLMJudgeScorer:
    def __init__(
        self,
        api_client: SupportsStreamingMessages,
        model: str,
        rubrics: dict[str, str] | None = None,
    ) -> None: ...
```

**Alternatives**：
- (a) Constructor 注入 client/model（推荐）
- (b) `score(sample, output, *, client, model)` 方法签名传
- (c) 全局 singleton api_client

**Chosen**：(a)

**Rationale**：
- 选 (b) 破坏 `Scorer` Protocol（D31.3 锁的 `score(sample, output) -> Score`
  契约）—— runner 要为这个 scorer 走 special path，违反 substrate 不动 invariant
- 选 (c) 全局 state 反 OpenHarness `from __future__ import annotations` 风格
- 选 (a) 让 scorer 自己持有 LLM call 的依赖，**Scorer Protocol 不变**，
  runner 0 修改

**额外收益**：构造 scorer 时显式知道用哪个 model 当 judge —— self-preference
接受这件事**在调用点可见**（D32.5）。

---

### D32.4 — Score.value 4 状态：1.0 / 0.0 / "NA" / "ERROR"

**Score.value 类型**: `float | str`（D31.4 已锁联合类型）

| Value | 含义 | 何时出现 |
|---|---|---|
| `1.0` | judge PASS | rubric 评判通过 |
| `0.0` | judge FAIL | rubric 评判不通过 |
| `"NA"` | 不评判 | 该 capability 没注册 rubric（D32.1 selective）/ output.goal is None（短路） |
| `"ERROR"` | judge 调用 / parse 失败 | LLM call exception / 非 JSON / schema 不合 |

**Rationale**：
- "NA" vs "ERROR" 必须区分（Day 1 Stage 0e lesson：failure mode 永不
  collapse）
- aggregate 时 "NA" 不算 fail（capability 主动 skip），"ERROR" 算 fail
  （应该判但出问题了）
- summary 显示按 dim 分组 pass/fail 时，"NA" 行不参与分母

---

### D32.5 — Bias 防御：rubric 内显式 + 接受 self-preference

playbook §四 4.3 五类偏差应对：

| Bias | 处理 |
|---|---|
| **Position** | N/A（不做 pairwise，单 case 评） |
| **Verbosity** | Rubric 显式写 "Length does NOT factor into score" |
| **Self-preference** | **接受**（单 model 环境，judge = main model = `settings.model`）。drift 在 dataset_card stability profile 上 surface |
| **Format** | 短路：output.goal is None 时返回 "NA"，不进 judge |
| **Calibration drift** | 不在 MVP，Stage 4+ score 落盘时 stamp `judge_model + rubric_hash` |

**为什么接受 self-preference**：
- 用户环境只有 deepseek 一个 model（user 早期 confirmed），换 judge model
  需要新 provider 接入 = ratification 级 / 钱
- 接受的代价：score 系统性偏高 5-7% + prompt A/B 相对差距压缩
- 现阶段 craft 目标是 "证明 LLM-judge 比 substring 抓更多语义" 不是
  "拿到 absolute production score"
- Stage 4+ 接入第二 provider 后第一件事就是跑 cross-judge 看 drift

---

### D32.6 — LLM-judge 跟 CapabilityAssertionsScorer **共存**，不替换

**Alternatives**：
- (a) 并行跑两个 scorer，disagreement 当 calibration 信号
- (b) LLM-judge 优先，substring 作 fallback
- (c) Substring 失败时才上 LLM-judge

**Chosen**：(a)

**Rationale**：
- Substring 在 T1/T2 100% 稳定 → 它在那里是 cheap + correct，不需要 judge
- T4/T5/T6/T7 substring 失败时 → LLM-judge 是否给"真"的 PASS 是 **rubric
  calibration 的唯一证据**
- 选 (b) 让 substring 沦为 quality fallback，丢失它在 T1/T2 上 deterministic
  的产品价值
- 选 (c) 加 if-else 条件分支到 substrate runner，违反 D31.10.1 "扩展不
  修改" invariant

**Disagreement 当信号**：
- substring PASS + judge PASS = strong signal correct
- substring FAIL + judge PASS = substring brittleness（Stage 3 修了这个）
- substring PASS + judge FAIL = substring false-positive 漏掉（rare，但
  可能存在，待 Stage 3 验证）
- substring FAIL + judge FAIL = real prompt failure（如 T5 qwen identify-impl trap）

后两类是 Stage 3 跑完最值钱的产物。

---

### D32.7 — Rubric prompt 设计 5 条原则（playbook §四 4.3 落地）

1. **二选一 (binary 1/0)** 不五分制（信号清晰）
2. **Chain-of-thought 显式要求**：rubric 强制 LLM "reason FIRST, then score"
3. **PASS / FAIL pattern 各给 2 个 example + 1 个 borderline**
4. **Rubric prompt < 500 字**（playbook §四 实证 > 800 字 score 噪音 +30%）
5. **强制 JSON 输出**：`{"reason": "...", "score": 0 or 1}`，parse 失败标
   "ERROR" 跳过（**不 fallback 给 0**，避免 distribution 污染）

---

## 二、Substrate 接口契约（Stage 3 不动）

`Scorer` Protocol 完全不变（D31.3）。`CapabilityLLMJudgeScorer` 实现这个
Protocol，自带 client/model 依赖。

```python
# scorer 调用形态（substrate 视角不变）
score = await scorer.score(sample, output)
```

新增 public API：

```python
from openharness.eval.scorers import CapabilityLLMJudgeScorer
from openharness.eval.rubrics import CAPABILITY_RUBRICS

# usage in spike script
scorer = CapabilityLLMJudgeScorer(client, model)  # rubrics= default CAPABILITY_RUBRICS
```

---

## 三、Stage 3 验收（Acceptance）

- [ ] `src/openharness/eval/rubrics.py` 含 4 个 rubric (T4/T5/T6/T7)，每个
      < 500 字，符合 D32.7 5 条原则
- [ ] `CapabilityLLMJudgeScorer` 实现 `Scorer` Protocol：`isinstance(scorer, Scorer) == True`
- [ ] runner 0 修改（D31.10.1 invariant）
- [ ] spike 跑出来 8 case 各产生 4 个 score：parse + keyword + capability_substring + capability_llm_judge
- [ ] T4/T5/T6/T7 case 的 LLM-judge score 是 1.0 或 0.0（不是 NA/ERROR），其他 4 case 是 "NA"
- [ ] Disagreement detection：substring vs judge 不一致时输出 ⚠ flag

---

## 四、Future Stage 4+ hooks

### Stage 4 — Multi-judge ensemble
- 加第二 provider（claude/gpt）当 judge
- Hook: `LLMJudgeScorer` 接受 `models: list[str]`，跑 N 个 model 取 majority
- 不动: `Scorer` Protocol（D31.3）

### Stage 5 — Calibration drift 追踪
- Hook: score 落盘时 stamp `(judge_model, rubric_hash, timestamp)`
- Stage 5 dataset_card 自动算月度 judge agreement，跌破 75% 触发 rubric review
- 不动: Score 数据类（D31.4）

### Stage 6 — Human-vs-judge calibration ritual
- 30 case sample 人审 + LLM-judge 对照
- Hook: `tests/eval/calibration_focus_state.py` 类型的测试 + 月度跑
- 不动: scorer 实现

---

## 五、相关 doc cross-reference

- 实验完整 case study: [`docs/ideas/eval-experiment-day1-focus-state.md`](../docs/ideas/eval-experiment-day1-focus-state.md)
- Substrate Stage 1 决策: [`decisions/31-eval-substrate-boundary.md`](./31-eval-substrate-boundary.md)
- 6 个 brittleness 维度: [`evals/focus_state/dataset_card.md`](../evals/focus_state/dataset_card.md)
- 理论 playbook §四 4.3 5 类偏差: [`docs/ideas/eval-mentor-playbook.md`](../docs/ideas/eval-mentor-playbook.md)
