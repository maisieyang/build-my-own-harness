# Decision 35 — Eval Coverage Map (Harness 决策面 → Eval 优先级)

> Created 2026-06-04 · 中文
>
> 配套读物：
> - 前置 substrate boundary：[`31-eval-substrate-boundary.md`](./31-eval-substrate-boundary.md)
> - Stage 3 judge：[`32-eval-stage3-llm-judge.md`](./32-eval-stage3-llm-judge.md)
> - Stage 4 cassette：[`33-eval-stage4-cassette.md`](./33-eval-stage4-cassette.md)
> - Stage 5 results：[`34-eval-stage5-results-persistence.md`](./34-eval-stage5-results-persistence.md)
> - 首个 consumer（也是 misuse 教训来源）：[`evals/focus_state/`](../evals/focus_state/)

---

## 〇、Why this doc

D31-D34 把 eval **substrate** 建好了。但 **substrate 是层级无关的**——它不
回答"应该建哪些 eval / 不该建哪些 eval / 哪个先建"。

2026-06-04 在 qwen-plus vs qwen3.7-max 的 cross-model 对照中，发现
focus_state eval 被**拿去回答它原本不为之设计的问题**（model strength
比较 / memory pivot readiness 判断），结果信号失真——3 次 substring 失败
里 2 次是 scorer brittleness，T3 catastrophic None 是 model noise，整体
"哪个 model 更好"无法定论。

根因不是 focus_state eval 设计不行——它在自己原始 scope 上**够好**。
根因是 **eval 没有显式的能力声明（capability claim），导致被误用到不
合身的能力面上**。

这个 misuse 暴露了一个更深层的缺失：**整个 harness 的"eval 应该覆盖
哪些决策面、每个怎么 contract、优先级是什么"从来没显式过**。本文档
固化这个 contract，让 Phase 16+ 在开任何新 eval 前先去这张 map 上
定位它属于哪个决策面 + 优先级是否到了。

这是**先写 boundary doc 再开 implementation**的实践——D31 自我反思
里写的"spike 之后立刻写 boundary doc 是纪律"的纠正动作。

---

## 一、第一性原理

### 1.1 Harness eval 的定义

> **Harness = 确定性代码 × 概率性模型行为。Eval 存在于"概率性那一面
> dominate 涌现行为"的地方。**

- 纯确定性逻辑（路径解析、JSON 解析、atomic write）→ **unit test**
- 纯概率输出（裸模型给文本）→ 不是 harness eval，是 **model eval**
- **Harness eval = 测"具体 harness 代码 + 概率模型"组合在某个能力面上
  的涌现行为质量**

### 1.2 推论：eval 种类数 ≈ 决策面种类数

Harness eval 的种类数 ≈ **harness 暴露给模型的"决策面"的种类数**。
每个决策面 = 一个独立 capability surface = 需要一份独立的 eval（独立
dataset + 独立 scorer + 独立 capability claim）。

**强塞多个决策面进同一个 eval 是反模式**——focus_state eval 的 misuse
正是这个反模式的实例。

---

## 二、决策 D35.1-D35.10

### D35.1 — OpenHarness 的 7 个决策面（ratified taxonomy）

**Chosen**：从当前 `src/openharness/` 反推，harness 暴露给模型的决策面分
7 类。这张表是本 map 后续所有优先级决策的**底层枚举**：

| # | 决策面 | 模型在干什么 | 现状 |
|---|--------|--------------|------|
| 1 | **Secondary LLM pass**（focus_state / extract / compact summarize）| 一次性结构化输出 | ✅ focus_state eval |
| 2 | **Tool 选择 + input 构造** | 看 user msg + tool 列表，决定调哪个 + 怎么填 | ❌ 仅 unit test (mock LLM) |
| 3 | **Tool 链 / agentic 主循环** | 用 tool_result 推进、识别完成、recover from error | ❌ 仅 unit test + dogfood |
| 4 | **Inline 决策类 side effect**（何时写 memory、何时停、何时报错） | 在主循环中嵌入"何时执行非显式 side effect"的判断 | ❌ 没 eval |
| 5 | **Plugin / Skill 调用** | 看 user msg + 可用 skill 列表，决定何时 invoke | ❌ 没 eval |
| 6 | **Sub-agent dispatch** | 决定何时 spawn 子 agent、传什么 context | ❌ 没 eval |
| 7 | **End-to-end task completion** | 给定真实任务，从头跑到完成 | ❌ 仅 dogfood |

**Rationale**：
- 7 个面分别对应不同的失败模式、不同的 input population、不同的 scorer
  形态——见 D35.2
- 这张表是 **enumeration**，不是 **classification taxonomy**——每加一个新
  决策面（比如 future 的 dynamic skill loading）要 explicit 进这张表，不能
  默默归到现有 7 项里

**Alternatives 不选**：
- (a) "把决策面合并成 3 大类 (prompt / tool / agentic)"——失败模式区分度
  丢失，无法独立优先级
- (b) "干脆不分类，按 service 名字建 eval（extract eval / compact eval
  / memory eval）"——focus_state misuse 的根因正是这个：按 service 名字
  分会导致同一个 service 的 eval 被混用到不同决策面

### D35.2 — 每个决策面有独立的 (input, scorer) 形态

**Chosen**：

| 决策面 | input population | scorer 形态 |
|--------|------------------|-------------|
| #1 secondary pass | 合成 conversation | JSON parse + 语义 judge |
| #2 tool 选择 | task + tool registry | "正确 tool 是否被调"（categorical） |
| #3 agentic loop | multi-step task | "最终是否完成" + turn 数 / 成本 |
| #4 inline 决策 | 触发 side-effect 的对话片段 | "side effect 是否触发 + 触发对了吗" |
| #5 plugin | task + plugin set | capability preservation + 新增能力 |
| #6 sub-agent | "需要 spawn 的 task" | spawn 决策 + sub-context 正确性 |
| #7 E2E | 真实任务 | task success rate + cost / time |

**Rationale**：input 形态不同 → dataset.yaml schema 不同；scorer 形态
不同 → Stage 1 substrate 的 `Scorer` Protocol 实现不同。即使 Runner 是
通用的，consumer 层是 fully decoupled per surface。

**关键不变量**：**Stage 1-5 substrate（Runner / Cassette / Scorer Protocol /
Results）是 surface-agnostic 的**——新决策面进入时**不需要扩 substrate**，
只需要：(a) 新建 `evals/<surface>/` consumer 目录；(b) 实现该面的 Scorer。
这是 D31 substrate 设计的回报点。

### D35.3 — 每份 eval 必须显式声明 3 个 contract（required header）

**Chosen**：每个新 `evals/<consumer>/dataset_card.md` 必须以下面 3 个
声明开头，**no exceptions**：

1. **Capability claim**："这份 eval 声明 harness 在 [决策面 #X] 上达到
   [Y 水平]" —— 必须 reference 本 map 的 #1-#7 之一
2. **Input spec**：输入数据是什么形态（合成 conversation / task description
   / full session / 真实任务），样本数 N、population 来源
3. **Judgment spec**：success criterion 是 binary / scalar / human-eyeballed
   / LLM-judge，每个 scorer 的输出语义

**Rationale**：focus_state eval 的 misuse 根因正是 capability claim 没有
显式——它 implicit 声明了"FOCUS_STATE_SYSTEM_PROMPT 的 prompt 质量"，
但被读成"model strength 比较" / "memory pivot readiness"。**显式
contract = 误用前的 contract violation 信号**。

**Forcing function**：D31 substrate Stage 6+ 扩展时，`oh eval <name>` 启动
路径会在 dataset_card.md 头部找不到这 3 个声明时**直接拒绝运行**（实施
推迟到 Phase 16+，但 contract 现在就立）。

### D35.4 — focus_state eval 的 capability claim 追溯性补齐

**Chosen**：focus_state eval 的 capability claim 追溯性写明：

> **Capability claim**：这份 eval 测试 `FOCUS_STATE_SYSTEM_PROMPT`
> （`src/openharness/services/focus_state.py:63-78`）这个**特定 prompt
> 在 secondary pass 形态下的 prompt-quality**。属于决策面 #1。
>
> **不为之设计**：
> - **跨 model 强弱比较**——substring assertion 是为单 model 校准的，
>   model-coupled brittleness 让 cross-model 结果无法直接比较。如果未来
>   有 cross-model 需求，需要另开 decision doc 改 judge model（参见 D35.8）。
> - **memory pivot readiness 判断**——这是决策面 #4 的范围，需要独立
>   dataset + 独立 scorer。
> - **harness 整体能力评估**——它只是 #1 的一个 instance；harness 还有
>   #2-#7 没覆盖。

**Action**：在 `evals/focus_state/dataset_card.md` 顶部插入这段 capability
claim（独立 commit，标 "追溯性补齐 D35.4"）。

### D35.5 — Eval 建设优先级（按 main path 驱动）

**Chosen**：不是"7 个面全建"，而是**按主路径优先级驱动**——决策面什么
时候进入 harness 主流量，对应 eval 什么时候开建。

| Priority | 决策面 | 触发条件 | ROI |
|----------|--------|----------|-----|
| **P0** | **#4 inline 决策（memory pivot）** | Phase 16 直接挡 | 高，且短期需要 |
| P1 | #2 tool 选择 + #3 agentic 主循环 | harness 的"主菜"，目前裸跑 | 高，工作量大 |
| P2 | #1 cross-model 扩展 | 现有 eval 加完 T1/T2/T3/T8 judge + judge model 第三方化 | 中（边际改进） |
| P3 | #5 plugin、#6 sub-agent | 等真有用户路径再说 | 低（推测性） |
| P3 | #7 E2E | 等有稳定的 task population 来源 | 低（dogfood 暂时够） |

**Rationale**：
- CLAUDE.md "Don't add features beyond what the task requires" 在 eval
  设计上同样有效——pre-emptively 建 eval 是 over-engineering
- 优先级跟随**主推进 phase**，不是"按决策面编号顺序"
- P3 不是"以后不建"，是"等触发条件出现再回来看 map"

**Anti-scope**：本 doc **不**承诺"全部 7 个面都会建 eval"。P3 项可能
永远不到触发条件，that's fine。

### D35.6 — Substrate 复用 ≥ 重建（不变量）

**Chosen**：新决策面的 eval **必须复用 Stage 1-5 substrate**（`src/openharness/
eval/`）。不允许为新决策面新建独立 framework。

**Rationale**：
- substrate 是 surface-agnostic 的（D35.2 关键不变量）
- 新决策面缺的是 dataset shape + scorer 实现，**不是** runner / cassette /
  results persistence
- 重建 framework 会导致 substrate 漂移 + 维护成本爆炸

**Forcing function**：新 `evals/<consumer>/` 目录 review 时必须验证它依赖
`from openharness.eval import ...`，不允许 vendored 一份本地 runner。

### D35.7 — Anti-scope：明确不做的事

**Chosen**：本 map 显式列出**不做**的设计选择，给未来 review 一个 explicit
violation 检查清单：

1. ❌ **建 monolithic "harness eval"**——把多个决策面塞进一个 dataset
2. ❌ **扩 focus_state eval 去 cover 别的决策面**（如 memory pivot）
3. ❌ **先建 framework 再想 dataset**——substrate 已建，现在缺 consumer
4. ❌ **按 service 名字组织 eval**（extract eval / compact eval / memory eval）
   ——focus_state misuse 的根因；改为按**决策面**组织
5. ❌ **同一个 eval 同时回答多个 capability claim**——拆分

### D35.8 — Cross-model 比较是一个独立决策，不是"加 case"能解决的

**Chosen**：如果未来需要在某个决策面上做 cross-model 比较，**必须单独
开一份决策 doc**，至少要 ratify：

1. **Judge model 第三方化**：D32.5 的 self-preference 接受是 single-model
   前提下的；cross-model 下要换成固定第三方 judge model（候选：frontier
   模型如 GPT-4 / Claude，或 cross-vendor 中立 model）
2. **Assertion calibration 解耦**：substring blocklist 是 model-coupled 的
   （linguistic style 差异），需要扩 LLM-judge 覆盖率到 100%
3. **N 扩到 ≥ 20**：8 个 case 在 cross-model 下 1-2 个差异落进噪声

**Rationale**：focus_state 这次的 cross-model 尝试**暴露了上面 3 个隐性
约束**。临时加 case / 加 rubric **不解决** cross-model 比较的 methodology
缺陷。

### D35.9 — focus_state misuse 是设计教训，进 playbook 而不是删 eval

**Chosen**：focus_state eval 在它原始 scope 上**保留不变**。misuse 教训
写进 `docs/ideas/eval-mentor-playbook.md` 新章节 §"Capability claim discipline"，
作为未来 consumer 的 cautionary tale。

**Rationale**：CLAUDE.md §"three properties" 第三条 "review is a walkthrough,
not a stamp"——eval 也可能"测试通过但能力轮廓对不上"。这是方法论级别
的教训，**比 eval 本身更值得固化**。

**Lesson 一句话**：**没有 explicit capability claim 的 eval，迟早会被
误用到不合身的能力面上**。

### D35.10 — Map 自身的演进规则

**Chosen**：
1. **新决策面出现**（如 Phase 17+ 的 streaming chunk decoding、Phase 20+ 的
   dynamic skill loading）→ 必须开一份 decision doc 把它**加进 D35.1 表
   并 ratify 优先级**，不能默默 fold 进现有 7 项
2. **Map 上的决策面 deprecate**（如果 #6 sub-agent 永远没进主路径）→ 也
   要 explicit 标 deprecated，并写明 why；不能默默移除
3. **优先级 reshuffle**（P3 上升到 P1）→ 写新 decision doc 引用本 doc，
   不在 D35 本身做 in-place 修改（保持决策历史可追溯）

**Rationale**：Map 是 living document，但 "live" 不是 "悄悄改"——
explicit 演进路径才能让 3 个月后的读者讲清"为什么 P0 从 #4 变成 #3"。

---

## 三、Acceptance criteria for Phase 16

基于 D35.5 P0 = #4 inline 决策（memory pivot），Phase 16 的 boundary doc
**必须**包含：

1. **D35.3 三声明**写进 `evals/memory_decision/dataset_card.md`（或类似命名）
   作为 gating eval 的 contract
2. **Input population**：3-5 个对话片段 cover (a) user 表达 preference; (b)
   user 做 correction; (c) project 状态变化
3. **Judgment spec**：是否判断"该写 memory" + 是否 inline 调 Write 工具 +
   写出的 frontmatter 是否合法 + 是否同步 Edit MEMORY.md
4. **Pass bar**：在 gating eval 上达到 ≥ X% 才允许走 LLM-self-decides 架构
   （X 在 Phase 16 boundary 里 ratify）

---

## 四、References

- D31 — eval substrate 起源
- D32 — Stage 3 LLM-judge（self-preference 接受的前提）
- D33 — Stage 4 cassette
- D34 — Stage 5 results persistence
- 实验：`docs/ideas/eval-experiment-day1-focus-state.md`
- Playbook：`docs/ideas/eval-mentor-playbook.md`（D35.9 行动项扩 §"Capability claim discipline"）
- Consumer: `evals/focus_state/`（D35.4 行动项追溯补齐）
