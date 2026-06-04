# Decision 31 — Eval Substrate Boundary (Stage 1 MVP)

> Created 2026-06-03 · 中文
>
> 配套读物：
> - 实验全过程：[`docs/ideas/eval-experiment-day1-focus-state.md`](../docs/ideas/eval-experiment-day1-focus-state.md)
> - 决策方案对比：[`docs/ideas/eval-first-principles.md`](../docs/ideas/eval-first-principles.md)
> - 理论 playbook：[`docs/ideas/eval-mentor-playbook.md`](../docs/ideas/eval-mentor-playbook.md)
> - Substrate code：[`src/openharness/eval/`](../src/openharness/eval/)
> - 首个 consumer：[`evals/focus_state/`](../evals/focus_state/) + [`scripts/spike_focus_state_eval.py`](../scripts/spike_focus_state_eval.py)

---

## 〇、Why this doc

Day 1 (2026-06-03) 在 spike 模式下走完 6 stage（mock → real-file →
capability-anchored → T7 fix → T2 fix + max_tokens → reproducibility ritual），
产出**4 ✓ + 1 ⚠ + 1 ✗ production-grade eval 性质**和 **N=4 跨 model
stability profile**。

Stage 1 substrate 直接从 spike 提取，**没有先写 boundary doc**——这违反了
CLAUDE.md "before any non-trivial implementation, write decisions/" 的方法
论纪律。本文档**事后补齐**：把 substrate 的设计决策、Sample/Scorer 接口
契约、Dataset schema、anti-scope 边界**固化下来**，让未来 Stage 2-5
扩展时有 stable contract。

**这本身是一条 lesson**：spike 之后**立刻**写 boundary doc 是纪律
（哪怕只是 30 分钟），否则 substrate 决策只活在 chat / Python 代码里，
3 个月后讲不清"为什么这么设计"。

---

## 一、Substrate 范围

`src/openharness/eval/` 是 **harness 级 capability**（跟 `observability/` /
`memory/` / `skills/` / `commands/` 同级），不是 service-specific
sub-module。

**Substrate 提供**：
- 数据 shape (`Sample` / `Score`)
- 接口契约 (`Scorer` Protocol)
- 数据集 loader (`load_dataset`)
- async runner (`run_eval`)

**Substrate 不提供**：
- 具体的 service-specific eval（每个 service 自己写 `evals/<service>/`）
- 具体的 Scorer 实现（`scorers.py` 是**初始**实现，未来会扩 LLM-judge）
- CLI / cassette / version-stamping（Stage 2+ 演进）

**Consumer 模式**：
```
src/openharness/eval/        ← substrate (shared)
evals/focus_state/           ← consumer 1
evals/extract/               ← consumer 2 (Phase 17+)
evals/compact/               ← consumer 3 (Phase 17+)
scripts/spike_<service>_eval.py  ← thin entry script per consumer
```

每个 consumer 自带 dataset.yaml + dataset_card.md。substrate 永远不知道
具体的 capability ID 含义。

---

## 二、决策 D31.1-D31.10

### D31.1 — 包位置：`src/openharness/eval/` (推荐 ratified)

**Alternatives**：
- (a) `src/openharness/eval/` —— harness 级顶层包
- (b) `src/openharness/services/eval/` —— services/ 子模块
- (c) `src/openharness/_eval/` —— 下划线标记 internal

**Chosen**: (a)

**Rationale**：
- eval 是 harness 级能力，跨 service 复用（focus_state / extract / compact /
  Phase 18+ agent loop eval 都会用同一个 substrate）
- 跟 `observability/` / `memory/` / `skills/` / `commands/` 同级 —— 表达
  eval 是 first-class harness primitive
- 选 (b) 会让 Phase 18+ agent-level eval 出现 namespace 冲突（agent eval
  跟 services/ 无关）
- 选 (c) 暗示"experimental"，但实际上 substrate 已经过 Day 1 6-stage
  压测，超过 internal/experimental 水位

---

### D31.2 — `Sample` 数据 shape：focus_state-tied (MVP)，generic 留 Phase 17+

```python
@dataclass(frozen=True)
class Sample:
    case_id: str
    capability: str
    shape: str
    messages: list[ConversationMessage]    # ← tied to focus_state input shape
    expected_goal_keywords: list[str]
    capability_assertions: dict[str, list[str]]
    notes: str
```

**Alternatives**：
- (a) tied to focus_state（messages → goal/next_step expected）
- (b) generic Sample[I, O] 用 TypeVar
- (c) 单独的 FocusStateSample / ExtractSample 数据类

**Chosen**: (a)

**Rationale**：
- Stage 1 MVP 只服务 focus_state，generic 是过设计
- Sample 的 input shape (messages) 跟 focus_state 的 input shape 同构 ——
  其他 service 用同样 shape 没问题（extract / compact 也吃 messages）
- expected_goal_keywords 是 focus_state 输出维度，extract/compact 输出
  不同（extract 输出 Memory list，compact 输出 9-slot），到时候 Phase 17+
  refactor Sample 时**有真实需求驱动**，比现在拍脑袋抽象准
- 选 (b/c) 增加 import 复杂度 + Protocol 复杂度，没真实需求

**Phase 17+ refactor trigger**：当 extract.py 装 eval 时，如果 expected
字段无法适配（要写 `expected_memory_count` / `expected_memory_types` 等），
触发 generic 重设计。

---

### D31.3 — `Scorer` Protocol：async + Score return

```python
@runtime_checkable
class Scorer(Protocol):
    @property
    def dim(self) -> str: ...

    async def score(self, sample: Sample, output: FocusState) -> Score: ...
```

**Alternatives**：
- (a) async `score()`
- (b) sync `score()` + 单独的 AsyncScorer Protocol
- (c) Callable 函数（无 Protocol）

**Chosen**: (a)

**Rationale**：
- Stage 3 LLM-judge scorer 必然 async（要 await LLM call）—— **从一开始**
  统一 async 比 Stage 3 时拆 Sync/Async Protocol 干净
- 程序化 scorer 用 `async def score(...): return Score(...)` 就行 ——
  trivially 适配，没有性能 cost（没真正 await 时 async overhead 接近 0）
- 选 (b) 让 runner 要写两套 dispatch 逻辑；选 (c) 失去 Protocol 静态
  检查能力（`isinstance(x, Scorer)` 不工作）

**Future**：Stage 3 LLM-judge scorer 实现这个 Protocol 时，runner 0 行
改动。这是 substrate-vs-consumer 抽象的核心承诺。

---

### D31.4 — `Score` 数据形态：multi-dim 永不 collapse

```python
@dataclass(frozen=True)
class Score:
    dim: str
    value: float | str       # float for binary/graded, str for multi-state enum
    reason: str              # 必填，pass 时也填，debugger-friendly
    case_id: str
```

**Alternatives**：
- (a) `float | str` 联合
- (b) 强制 float (0.0-1.0)
- (c) generic `Score[V]`

**Chosen**: (a)

**Rationale**：
- 现有 3 scorer 都返回 float
- 但 Stage 1.5 候选 `ParseQualityScorer` 必须用 multi-state enum
  (`empty` / `non_json` / `json_invalid_schema` / `json_valid`) 而不是
  binary —— D29.5 (Day 1 §四点九 finding) 锁定这是必须区分的失败 mode
- 选 (b) 强行把 multi-state 塞进 float，丢失语义；选 (c) generic Score
  增加 Scorer Protocol 复杂度

**aggregate 纪律**（D31.4.1）：
- 任何"总分"计算**禁止 collapse 跨 dim 成 single number**
- summary 输出按 dim 各自累加（playbook §四 4.4 Goodhart's law）
- 后续如果需要 overall pass/fail，**乘法不加法**（任一 dim 不及格 → 整体不及格）

---

### D31.5 — Dataset 格式：YAML + Pydantic discriminated union

```yaml
samples:
  - case_id: ...
    capability: T1
    messages:
      - role: user
        content:
          - type: text
            text: ...
```

**Alternatives**：
- (a) YAML（人编辑友好）
- (b) JSONL（machine-friendly + appendable）
- (c) Python module (`evals/focus_state/dataset.py` import as list)

**Chosen**: (a)

**Rationale**：
- 跟 OpenHarness `decisions/` / `tasks/` / settings 的 markdown/YAML
  风格一致
- ConversationMessage 是 Pydantic v2 模型，`model_validate(dict)` 自动
  跑 discriminated union (`type` field) → TextBlock / ToolUseBlock /
  ToolResultBlock 反序列化
- YAML 写 nested message 比 JSONL 可读性高很多
- 选 (c) 让 dataset 跟 code 耦合 → 加 case 要改 Python；选 (b) 写 nested
  message 时 escaping 很丑

**实际验证**：D31.5 在 Stage 1 MVP 上跑通——5 case 包含 18+ ContentBlock
（含 nested Edit 的 `old_string`/`new_string` 多行字符串）全部正确 round-trip。

---

### D31.6 — Capability-anchored case（D31.6.1）+ pre-registered assertions（D31.6.2）

这是 **Day 1 Stage 0c 学到的核心 craft**，substrate 必须固化：

- **D31.6.1**: 每个 Sample 必须 declare `capability` ID（e.g., T1-T8），跑前
  designer 已经知道这个 case 测 prompt 的哪个能力
- **D31.6.2**: `capability_assertions` dict 在 case 编写时 pre-register
  pass/fail 标准（goal_must_contain / goal_must_NOT_contain /
  next_step_must_contain_any_of / next_step_must_NOT_contain）

**Why pre-register**：让每个 case 成为带 falsification 标准的科学假设。
跑前 declare "什么算 pass / 什么算 fail" → 跑后 LLM 输出按这个 declaration
评判 → 反复 fail = prompt 在那个 capability 维度有结构性缺陷。

避免 shape-anchored 时"事后 storytelling"的非科学姿势。

---

### D31.7 — Scorer 类目边界：MVP 只放程序化 scorer

**In MVP**:
- `ParseOkScorer` — 结构 dim
- `GoalKeywordMatchScorer` — 程序化 substring (baseline，跟 capability scorer disagree 时 surface false-positive)
- `CapabilityAssertionsScorer` — 程序化 substring (capability-anchored)

**Out of MVP (Stage 3 之前不上)**:
- LLM-judge scorer（语义维度，要 await LLM call + judge prompt + 防 5 类
  偏差，是独立 capability）
- Multi-judge ensemble（Stage 3+）
- Calibration drift 追踪（Stage 3+）

**Rationale**：
- 程序化 scorer 是第一道闸门（playbook §四 4.1 二维矩阵象限 I），cheap +
  deterministic
- LLM-judge 是第二道闸门，等程序化失败的 case 上跑 —— **顺序不能颠倒**
- Day 1 §四点九 已经实证 substring assertion 在 cross-model 上有 semantic
  上限（T5/T6 false-positive）；Stage 3 LLM-judge 是自然 successor

---

### D31.8 — LIVE-only mode：MVP 不上 cassette

**Alternatives**：
- (a) LIVE-only (MVP)
- (b) cassette default + LIVE on-demand
- (c) hybrid（默认 cassette，新 case 自动 record）

**Chosen**: (a)

**Rationale**：
- Cassette infra (`vcrpy`) 是非平凡 dependency + 文件管理 complexity
- Day 1 已经实证 LLM stochasticity（5/5 → 4/5 → 5/5 等）是 production
  reality —— cassette 解决"deterministic CI"问题但 spike/dev 阶段 LIVE 更
  对得起体感
- 选 (b/c) 在 substrate MVP 阶段是过设计

**Future Stage 4 trigger**: 当 dataset 扩到 30+ case + 跑 N=5 reproducibility
ritual + CI 集成时，cassette 上场。

---

### D31.9 — 不动 services/ 的 prompt：substrate 跟 prompt 改动**解耦**

Day 1 已经修改了 `services/focus_state.py` 的 FOCUS_STATE_SYSTEM_PROMPT
(T7 fix + T2 fix) 和 `max_tokens` (256 → 512)。

**Stage 1 substrate 落地时不动这些**——保持 Day 1 末位稳态，让 substrate
refactor 的 acceptance criteria 是 **"行为 byte-identical vs 上次跑"**。

**Future**: prompt 改动跟 dataset/scorer 改动**分开提交**，每次 prompt 改
动后跑 N 次 spike 验证 capability shift。**避免** prompt + dataset + scorer
一起改，那会让 score 变化归因不清。

---

### D31.10 — Substrate 跟 consumer 的目录分离

```
src/openharness/eval/          ← substrate code (shared)
evals/<service>/               ← consumer dataset + dataset_card (per-service)
scripts/spike_<service>_eval.py ← consumer entry script (per-service)
```

**Rationale**：
- `src/` vs `evals/` vs `scripts/` 三层物理分离 = "什么是 shared, 什么是
  per-service, 什么是 entry" 三个 concern 各自独立
- consumer 加新 case = 编辑 `evals/focus_state/dataset.yaml`，0 Python
  修改 → substrate-vs-consumer 抽象边界**真实可见**
- 第二个 consumer (extract.py) 上场时，只需 `evals/extract/` 新目录 +
  `scripts/spike_extract_eval.py` 新 entry —— substrate **0 行修改**

**Substrate-compounding 验证 (D31.10.1)**：Phase 17 装 extract.py eval 时，
`src/openharness/eval/` 必须 zero diff（除非真正暴露 substrate 设计盲区，
触发明文 retrofit）。这是 Phase 7c retro §3.1 "abstraction-first compounds"
精神在 eval substrate 上的对偶。

---

## 三、Substrate 接口契约（公开 API）

### 数据类（unstable until Phase 17+，可 break）

```python
from openharness.eval import Sample, Score
from openharness.eval.runner import CaseResult
```

| 类 | Stability | 演进规则 |
|---|---|---|
| `Sample` | Phase 17+ refactor | 加字段：允许（向后兼容）；删/改字段：major |
| `Score` | stable | dim/value/reason/case_id 是 minimal 四件套 |
| `CaseResult` | stable | sample/output/scores 是 wrapper |

### Protocol（stable）

```python
from openharness.eval import Scorer
```

`Scorer` Protocol 的契约：
- `.dim: str` (property) —— scorer 的 category name (e.g., "parse_ok", "capability")
- `async def score(sample, output) -> Score` —— 异步评分

`Score.dim` 字段可以**跟 scorer.dim 不同**（e.g., CapabilityAssertionsScorer
返回的 Score.dim = `f"capability_{sample.capability}"`），由 scorer 实现
决定。

### Runner（stable）

```python
from openharness.eval.runner import load_dataset, run_eval
```

`load_dataset(path: Path) -> list[Sample]` —— YAML loader
`run_eval(dataset_path, scorers, client, model) -> list[CaseResult]` ——
async iterator

### Dataset YAML schema (stable，per D31.5)

详见 [`evals/focus_state/dataset.yaml`](../evals/focus_state/dataset.yaml)
的实际形态 + [`src/openharness/eval/runner.py:load_dataset`](../src/openharness/eval/runner.py) 的 docstring。

---

## 四、已知 brittle 维度 + Stage 3 LLM-judge 接管点

Day 1 N=4 跨 model 数据：

| Sample | Stability | Brittleness | Stage 3 fix |
|---|---|---|---|
| T1 | 75% | deepseek-v4-flash 偶发 parse fail | model-specific 噪音，跑多次取 majority |
| T2 | 75% | prompt fix probability shift | acceptable production rate |
| **T5** | **25%** | substring rule 无法区分 "symbol as task subject" vs "symbol as contextual reference" | **LLM-judge 必须接管** |
| **T6** | **75%** | substring rule 无法区分 "read" 作 tool name 引用 vs 英文动词 | **LLM-judge 必须接管** |
| T7 | 100% | rock solid | no action |

Stage 3 LLM-judge rubric 候选（未来实现）：
- T5：`"Is the goal *focused on* finding the implementation (FAIL), or just
  *mentioning* it as part of the broader fix task (PASS)?"`
- T6：`"Does the goal use 'read' as a tool-name verb (FAIL), or as the
  English verb 'examine' (PASS)?"`

LLM-judge scorer 接 Stage 3 substrate 扩展时不需要修改 `protocol.py` ——
直接在 `scorers.py` 加新 class 实现 `Scorer` Protocol 即可。

---

## 五、Scope / Anti-scope 总览

**In Stage 1 MVP**:
- ✅ `Sample` / `Score` 数据类
- ✅ `Scorer` Protocol
- ✅ 3 programmatic scorers (Parse / Keyword / Capability)
- ✅ Async runner + YAML loader
- ✅ Dataset YAML + dataset_card.md
- ✅ Consumer entry script as thin wrapper

**Out（明确不做，留 Stage 2-5）**：
- ❌ Dataset 扩量（Stage 2: 5 → 30+ case）
- ❌ LLM-judge scorer（Stage 3）
- ❌ Multi-judge ensemble + bias 校准（Stage 3+）
- ❌ Cassette / replay mode（Stage 4）
- ❌ `oh eval` CLI 子命令（Stage 5）
- ❌ Score 落盘 + version stamping（Stage 5+）
- ❌ Human-vs-judge calibration ritual（Stage 6+）
- ❌ Cross-model 系统化跑 + 报告（Stage 6+）
- ❌ Generic Sample[I, O]（Phase 17+ extract.py 装 eval 触发）

**永远不做**（out of project scope）：
- ❌ 通用 LLM eval 框架（OpenHarness 是 single-dev learning project，不是
  DeepEval/Inspect AI 的竞品）
- ❌ Hosted eval platform / UI
- ❌ 集成第三方 eval 平台（LangSmith / Braintrust）

---

## 六、Stage 1 验收（Acceptance）

- [x] `src/openharness/eval/` 4 files：ruff ✓ + mypy --strict ✓
- [x] 5 cases 从 Python list 移到 YAML，loader 正确反序列化（含 Edit/Read
      tool input 的 nested string）
- [x] 3 scorers 实现 `Scorer` Protocol：`isinstance(scorer, Scorer)` 全 True
- [x] Spike script 缩到 ~140 行 thin wrapper（dataset.yaml + run_eval + print）
- [x] 跑一次 vs Day 1 末位行为 **byte-identical pattern**：
      Run 2026-06-03 T1✓ T2✓ T5✗ T6✓ T7✓ —— 匹配 Day 1 stability profile
      预期（T5 1/4 是已知 brittle）
- [x] 加第 6 case = 编辑 dataset.yaml，0 Python 改动（contract verified by
      D31.10 的目录分离）

---

## 七、Future-work hooks（每个 hook 都 lock 在 contract 里）

### Stage 2 — Dataset 扩量
- Hook: dataset.yaml 加 sample（D31.10）
- 不动: substrate code

### Stage 3 — LLM-judge scorer
- Hook: 新 scorer class 实现 `Scorer` Protocol（D31.3）
- 接 `summarize()` 调 judge LLM
- 防御 5 类偏差（playbook §四 4.3）：verbosity / self-preference / format /
  position / calibration drift
- 不动: `protocol.py` / `runner.py`

### Stage 4 — Cassette mode
- Hook: 在 `run_eval` 加 `replay: bool` 参数 + cassette path
- 用 vcrpy 包 `infer_focus_state` 的 LLM call
- 不动: `Scorer` / `Sample`

### Stage 5 — CLI + version stamping
- Hook: 新 `src/openharness/cli.py` 子命令 + score 落盘 (`evals/<service>/results/`)
- 每条 result stamp `(date, model, prompt_hash, dataset_version, scorer_versions)`
- 不动: substrate core

### Phase 17+ — 第二个 consumer (extract.py)
- Hook: `evals/extract/dataset.yaml` + `scripts/spike_extract_eval.py`
- 触发 Sample shape 重设计（generic Sample[I, O] or 单独 ExtractSample）
- D31.2 alternatives 在那时重新评估

---

## 八、相关文档

- 实验全过程：[`docs/ideas/eval-experiment-day1-focus-state.md`](../docs/ideas/eval-experiment-day1-focus-state.md)
  （6 stage 完整 case study + cumulative craft lessons）
- 第一性原理决策方案：[`docs/ideas/eval-first-principles.md`](../docs/ideas/eval-first-principles.md)
  （Phase 16 boundary 之前的 8 个待 ratify 题）
- 理论 playbook：[`docs/ideas/eval-mentor-playbook.md`](../docs/ideas/eval-mentor-playbook.md)
  （10 章 first principles + 6 月 4-Milestone 学习路径）
- Stage-by-stage journal 模板：[`docs/ideas/eval-craft-journal.md`](../docs/ideas/eval-craft-journal.md)
  （Before/During/After lab notebook）
