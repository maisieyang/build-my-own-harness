# 给 services/ 加 Eval：一份 first-principles 调研

> 写于 2026-06-02 · 中文版
>
> 配套读物：
> - 项目方法论：[CLAUDE.md](../../CLAUDE.md)
> - services/ substrate 的成型史：[learnings/phase-13.md §3.1](../../learnings/phase-13.md)
> - 关联思考：[docs/ideas/tui-vs-web-frontend-first-principles.md](./tui-vs-web-frontend-first-principles.md)
>
> 这篇不是 Phase 16 的 boundary doc，也不是 plan —— 是动手开 boundary
> 之前要先和自己对齐的 **方向调研**。回答的问题：在 OpenHarness 这种
> "一个人 / 已经有 2000 个 pytest / 95% cov / 7 个 LLM-driven service
> consumer" 的项目里，"加 Eval" 这件事的最小可用形状到底长什么样？

---

## 〇、为什么这件事现在需要想清楚

`services/summarize.py` 跑到 Phase 13 时已经有 **7 个 consumer**
（compact L4 / extract / focus_state / `/compact` REPL / 3 个内部 retry
路径）。每个 consumer 都是 "拼自己的 system prompt → 调 `summarize()`
→ 解析自己的输出格式" 的模式：

| Consumer | 输入 | 输出 | Parse 方式 |
|---|---|---|---|
| `services/compact.py` 的 L4 | 长对话 messages | 9-slot summary 文本 | `<summary>` tag 切片 |
| `services/extract.py` | 一个 turn 的 messages | JSON `{"memories": [...]}` | `json.loads` + Pydantic-ish 校验 |
| `services/focus_state.py` | 最近 6 条 messages | JSON `{"goal": ..., "next_step": ...}` | tolerant `json.loads` + None fallback |
| (Phase 14+ 候选) verified_work / recent_files enrichment | 同 turn | JSON | TBD |

**真正的问题**：

- `tests/services/test_summarize.py` 等 13 个测试文件全部用 stub
  client，yield 固定 deltas → 只验"wiring correct"（layer 顺序、retry
  次数、JSON parse 容错），**不验 "prompt 真的让模型干对了事"**
- 我改 `EXTRACTION_SYSTEM_PROMPT` 的措辞（or 改 model 默认值 qwen-plus
  → qwen-max）时，**没有任何反馈环告诉我 quality 是涨是跌**
- Phase 11 ratification 时 D29.5 把 "extract 应该抽什么类型的 memory"
  写成 prompt 里几行 example —— 这是产品判断，但我**永远不知道这个判断在生产对话里准确率多少**
- 现有 pytest 防 correctness 退化（语法、类型、API 调用顺序），但不防 **quality 退化**（同一个 turn，新 prompt 抽出来的 memory 比老 prompt 差）

**一句话**：我目前迭代 services/ 里的 LLM 子能力是**盲飞**。眼看 unit
test pass → ship → 自己 dogfood 几次 → 凭直觉觉得"差不多" —— 这是
Phase 7c retro §3 一直没承认的盲区。Phase 11/12/13 的 abstraction
compounding 解决了 "substrate 重用" 问题，但没解决 "每个 consumer 的
prompt 是不是真的 work" 这个垂直问题。

Eval 就是给这个盲区装仪表盘。

---

## 一、Eval 是什么 —— 三件套 first principles

撕开所有 marketing 包装，一次 LLM eval 就是这三个东西：

```
                ┌─────────────┐
   Dataset  ──► │   Runner    │ ──► Scorer ──► Score (graded, not binary)
   (固定输入)    │ (跑 service) │      ↑
                └─────────────┘      │
                                     └── (有时) Ground truth label
```

- **Dataset** = 一组 `(input, [optional expected])` 对。每个 case 是
  一次"标准输入"。**关键约束：固定**。改 prompt / 改 model 时输入不变。
- **Runner** = 把 dataset 里每个 input 喂给被测 service，收集 output。
- **Scorer** = 把 (input, output, [expected]) 变成一个数（或几个维度
  的数）。**关键约束：可比**。同一个 dataset 跑两次（A prompt vs B
  prompt），数能告诉我哪个更好。

这三件套和 pytest 看起来像，但**本质不同**：

| 维度 | pytest unit test | LLM eval |
|---|---|---|
| Pass/Fail | binary | graded (0~1 / 多维度) |
| Determinism | 必须 deterministic | 接受 stochastic，但要可重复 |
| 输入边界 | 全空间穷举（边界 / 异常） | 抽样 representative cases |
| 失败语义 | "代码错了" | "**prompt 还不够好**" 或 "模型这次抽风" |
| 跑的成本 | 微秒 / 免费 | 秒 / 烧 LLM token |
| 反馈频率 | 每次 commit | 每次改 prompt / 换模型 / 定期 |

**pytest 防的是"代码 regression"，eval 防的是"产品 quality
regression"**。两者都需要，互不替代。

---

## 二、业界做法 —— 六个标杆，三种范式

我看了 2026 年还在主流的 6 个 LLM eval 框架/平台，**它们用三种不同
范式实现上面的三件套**：

### 范式 A：Pytest-native（DeepEval）

`assert_test(test_case, [metric])` —— 把 eval 写成 pytest 测试，metric
是 callable（含 50+ 内置如 G-Eval 用 chain-of-thought 给分）。

- 代表：[DeepEval](https://github.com/confident-ai/deepeval)（GitHub 7k+
  star，Python>=3.9，pytest plugin 是核心入口）
- 优点：复用 pytest 基建、CI 集成天然、Python 开发者零学习成本
- 缺点：把 eval 跟 unit test 混在同一个 pytest run，**cost / cadence
  不分离** —— 每次 push 都烧 LLM 钱，或者你得加 `@pytest.mark.eval`
  搞 marker 体系，又增加了 mental overhead

### 范式 B：Task / Solver / Scorer protocol（Inspect AI）

UK AISI 出品，被 Anthropic / DeepMind / xAI 内部用。核心抽象：

```python
@task
def my_eval() -> Task:
    return Task(
        dataset=[Sample(input=..., target=...)],
        solver=[generate()],     # 多步 agent 流程
        scorer=match(),          # 评分
    )
```

- 代表：[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)（200+
  pre-built evals 在 `inspect_evals` 仓库；UK AISI 几乎所有自动化
  eval 都走这个）
- 优点：清晰的 Protocol 抽象（Dataset/Solver/Scorer 各自独立可换）；
  内置 Docker sandbox；带 web/VSCode log viewer；面向 agent eval 也面向 prompt eval
- 缺点：基建重 —— 装它 = 装 Inspect 整个 runtime。**对一个 7 个
  consumer 的 substrate 来说是过设计**

### 范式 C：YAML-declarative（Promptfoo）

写一个 `promptfooconfig.yaml`，里面声明 prompts / providers /
tests / assertions，跑 `promptfoo eval` 出 web view。

- 代表：[Promptfoo](https://github.com/promptfoo/promptfoo)（2026-03
  被 OpenAI 收购，要并入 Frontier agent 平台）
- 优点：dataset + scorer 都是数据，不是代码 —— 工程师外人也能改 case
- 缺点：**declarative 抽象层级 = 一刀切**。programmatic scorer
  （如 "解析出的 JSON 是否符合 `Memory` dataclass 的 `__post_init__`
  约束"）在 YAML 里要么硬塞 jsonschema 要么塞 JavaScript snippet —— 这
  跟 OpenHarness 的 Pydantic-strict 风格不对齐

### 平台类（LangSmith / Braintrust）

[LangSmith](https://smith.langchain.com/)（LangChain 生态）和
[Braintrust](https://www.braintrust.dev/) 都是 hosted 平台 —— dataset
管理 + scorer 跑 + annotation UI + dashboard 一站。

- 优点：annotation workflow、人审 / LLM-judge 混跑、release gating
- 缺点：**vendor lock + 数据要发出去**。OpenHarness 是 single-dev
  learning project，不需要 stakeholder dashboard，不打算 ship 给团队
  用 —— **平台的价值集中在 OpenHarness 不需要的那一面**

### 业界对"分工"的现有共识

[inference.net 的 2026 comparison guide](https://inference.net/content/llm-evaluation-tools-comparison/)
和几篇独立 blog 都给同一个答案：

> 一个 lightweight framework 跑 CI gate（DeepEval / RAGAS / Promptfoo）
> + 一个 platform 跑 annotation + dashboard（LangSmith / Braintrust /
> Arize）—— 这是 2026 年保持 eval 可持续的标准分工。

但这个分工是给"3 人以上、有 stakeholder dashboard 需求"的团队设计的。
**OpenHarness 是单人 + 95% cov + 现成 pytest 文化的 learning
project**，分工逻辑不直接适用 —— 我可能只需要一半。

---

## 三、LLM-as-judge 是不是 eval 的标配？

不是。**它是 scorer 的一种实现，不是 scorer 的全部**。

[2026 年的几篇综述](https://futureagi.com/blog/llm-as-judge-best-practices-2026)
和 [Adaline 关于 50%+ bias 的报告](https://www.adaline.ai/blog/llm-as-a-judge-reliability-bias)
归纳出 5 类已知偏差：

| 偏差 | 大小 | 触发场景 |
|---|---|---|
| Position bias | 40% GPT-4 inconsistency | pairwise 比较时换顺序结果变 |
| Verbosity bias | ~15% inflation | 长输出被认为更好 |
| Self-preference | 5-7% boost | 用 GPT-4 当 judge 给 GPT-4 的输出加分 |
| Format bias | 不稳定 | 格式（带 emoji / 不带 emoji）影响判分 |
| Calibration drift | 月度漂移 | judge 模型更新后旧 score 失去对照价值 |

**生产共识**（2026）：

- judge cost 控制在 production LLM cost 的 10-15% 以下
- 5-20% 抽样 + 100% 错误样本
- 单 judge < 75% 人审 agreement 就重新校准（换 prompt / 换 model）
- 重大 release 用 3-judge ensemble（Claude / GPT / Gemini）majority vote
- **小的 distilled judge 比 frontier judge 便宜 10-50 倍**，准确率
  接近 —— 不一定非要烧 Opus 当 judge

**对 OpenHarness 的含义**：

- `extract.py` / `focus_state.py` 的 eval **可以**用 LLM judge（"这
  个抽出来的 memory 是不是 stable、future-useful、不可从代码推断？"）
- 但 **首选是 programmatic scorer**：
  - JSON 解析成功？
  - 抽出来的 `name` 符合 kebab-case regex？
  - `type` 在枚举里？
  - `scope` 是 team 时不含 secret pattern？
  - 抽出来的 memory 数量 ≤ 3（max_records 上限）？
- programmatic 能覆盖 70% 检查 → 剩下 30%（"是不是真的有用"）才上 LLM judge
- judge 用 qwen-plus（同 production model）但 prompt 极简化 + cache
  住 prompt prefix —— 避免 self-preference 用同模型评同模型的尖角案例

---

## 四、Cost 控制 —— VCR cassette 是被低估的工程

eval 有两个跑法：

1. **Live mode** — 真打 LLM API。准确，但慢（10s/case）+ 贵（每个 case 几分钱）+ stochastic
2. **Replay mode** — 用 VCR.py 录一次响应，存成 cassette 文件，CI 重播

业界 2025-2026 出现一批专门的 LLM cassette 工具：

| 工具 | 特点 |
|---|---|
| [vcrpy](https://vcrpy.readthedocs.io/) | 通用 HTTP cassette，老牌；LLM 调用走 HTTP 自然能录 |
| [BAML VCR](https://github.com/gr-b/baml_vcr) | 专门给 LLM 调用的 cassette，pytest fixture |
| [langchain-replay](https://github.com/sixty-north/langchain-replay) | 录 LLM 决策（tool name + args + text），replay 时让 tool **真的执行**（这点很关键） |
| [agent-vcr](https://pypi.org/project/agent-vcr/) | MCP JSON-RPC interaction 录放 |

**核心洞察**（来自 [Anay Nayak 的 cassette 文章](https://anaynayak.medium.com/eliminating-flaky-tests-using-vcr-tests-for-llms-a3feabf90bc5)）：

> HTTP-level cassette 录的是原始请求 —— 它**永远不让你的 tool 代码真的
> 跑**，所以测试停止反映 reality。
>
> langchain-replay 的解法：录 LLM 的决策（"调哪个 tool / 传什么参数 /
> 回什么文本"），replay 时 yield 决策，但让 tool 真的 execute。

**对 OpenHarness 的含义**：

- services/ 的 LLM 调用纯粹是 `messages + system_prompt → text`，**没有
  tool execution 嵌在里面**（D29.2 锁了 `tools_disabled=True`）。
  所以**最简单的 HTTP-level cassette 就够用** —— 不需要 langchain-replay
  这种半路 hook tool 的复杂方案
- 录一次 cassette、commit 进 repo / 用 git-lfs / 单独的 `evals/cassettes/`
  目录 → CI 跑 replay 模式完全 deterministic + 免费
- 改 prompt 时手动 `oh eval --record` 重录一次 → diff cassette 文件
  → 看 LLM 响应是不是真的变了 → 跑 scorer 看 quality 是涨是跌
- **Live mode 只在 ratify "新 prompt OK" 的瞬间用一次**，平时 CI 全是
  replay

这给了一个非常诱人的 cost 模型：**每个 prompt iteration 烧 1 次 LLM
钱（录 cassette），之后无限次免费 replay**。

---

## 五、OpenHarness 的特殊性 —— services 的接口已经是完美 eval seam

写了上面四节再回头看 services/，发现一个很 lucky 的事：

**`summarize()` 的接口形态本身就是 service-level eval 的最小契约**。

```python
async def summarize(
    *,
    messages: list[ConversationMessage],
    system_prompt: str,
    model: str,
    api_client: SupportsStreamingMessages,
    max_tokens: int = 2048,
    timeout_seconds: float = 25.0,
    tools_disabled: bool = True,
) -> str:
```

input = `(messages, system_prompt, model, api_client)`，output = `str`。
**这就是 Dataset 的 case shape**。

每个 consumer（extract / focus_state / compact L4）的 eval 形态都是：

```
dataset case = (fixed messages, the consumer's system_prompt, fixed model)
output       = LLM-generated str
scorer       = the consumer's parser + 业务断言
```

具体到三个现有 consumer：

### 5.1 extract.py 的 eval shape

- **Dataset case** = `(messages: 一个 turn 的 4-8 条 message, expected: list of memory descriptors)`
- **Runner** = `extract_memories_from_turn(...)` 真跑（含 `summarize()` + JSON parse + `_build_memory_from_record`）
- **Scorer 维度**：
  - 结构维度（programmatic）：返回 ≤ 3 条；每条 name 合法 kebab-case；type/scope 在枚举内；body 非空；team-scope 不含 secret
  - 语义维度（LLM-judge 或人审）：抽出的 memory 是否对应 expected 中至少一条；有没有抽出"应该丢弃"的（debugging trace / API surface 文档化的事 / 一次性 question / secret）

### 5.2 focus_state.py 的 eval shape

- **Dataset case** = `(messages: 最近 6 条, expected: 简短 goal + next_step)`
- **Runner** = `infer_focus_state(...)`
- **Scorer 维度**：
  - 结构（programmatic）：JSON 有效；返回 `goal` / `next_step` 字段；都是 str-or-None
  - 语义（LLM-judge）：infer 出的 goal 是否和 expected goal 等价（rubric: "name the same task"）；next_step 是不是 actionable

### 5.3 compact L4 的 eval shape

- **Dataset case** = `(messages: 一段长对话, expected: 9-slot 各槽位的关键事实)`
- **Runner** = `full_compact(...)` 出来的 9-slot summary
- **Scorer 维度**：
  - 结构：9 个 `<slot_name>` tag 都存在
  - 语义：每个槽位的 LLM-judge faithfulness（"这个槽位的内容是否准确反映了 dataset 里对应的内容、没有 hallucinate 多余信息"）

**这三套 eval 共享 ~80% 基建**：
- 同一套 dataset 文件格式（JSONL / YAML）
- 同一套 LLM cassette 机制
- 同一套 `Scorer` Protocol（input/output/case → `Score`）
- 同一个 `oh eval` 子命令

差异只在 **每个 service 自己的 system_prompt + 自己的 scorer 实现**
—— 这正是 Phase 11 substrate 的设计哲学的对偶面：substrate 提供
shared 机制，consumer 提供 specific 语义。Eval 复用同一个分层。

---

## 六、三个候选方案的对比

### 方案 1：薄壳 pytest plugin（minimal）

```
evals/
  test_extract_eval.py    # 用 pytest marker @pytest.mark.eval
  test_focus_state_eval.py
  conftest.py             # provide eval-specific fixtures (cassette dir, judge model)
```

跑法：`pytest -m eval --replay` / `pytest -m eval --record`

- ✅ 复用现有 95% cov / pytest CI / pytest-asyncio / VCR.py 一切
- ✅ 几乎不引入新代码
- ❌ eval 和 unit test 同一个 pytest run —— **cadence 强行耦合**。每次
  push 跑 eval 烧钱（除非 `-m "not eval"` 默认排除，但那就丢失了 CI gate
  的作用）
- ❌ DeepEval 也是这个范式，但**生产经验是 "marker 体系容易变成垃圾桶"**
  —— `@eval` / `@slow_eval` / `@expensive_eval` / `@nightly_eval` 一层
  套一层
- ❌ Scorer 没有独立抽象，散落在测试函数体里 —— 跨 service 不能复用
  programmatic checker

### 方案 2：独立 `oh eval` 子命令 + Protocol 抽象（heavyweight）

```
src/openharness/eval/
  __init__.py             # Public: Dataset / Scorer Protocol, Sample, Score
  dataset.py              # JSONL loader
  runner.py               # Async runner with cassette mode
  scorers.py              # Built-in scorers: JSONStructure, RegexMatch, LLMRubric
  cli.py                  # oh eval subcommand
evals/
  extract/
    dataset.jsonl
    scorers.py            # consumer-specific scorers (calls eval/scorers.py)
    cassettes/            # gitignored or LFS
  focus_state/
    ...
```

跑法：`oh eval [--service extract] [--mode {replay,record,live}] [--judge qwen-plus]`

- ✅ 跟 production 路径解耦 —— cost / cadence 各管各
- ✅ Scorer 是 Protocol，跨 service 复用；和 OpenHarness 的
  `BaseTool` / `BaseProvider` / `MemoryStore` 风格一致
- ✅ 可以做 cost report（每次 eval 花了多少 token / 钱）
- ✅ 单独 schedule（nightly / 改 prompt 时手动触发）
- ❌ 是一整个新 phase 的 capability（~5 task：Protocol + Dataset loader
  + Runner + 3 scorer + CLI + 3 个 service 的实际 eval suite）
- ❌ 新的 boundary doc / plan / retro

### 方案 3：Hybrid —— `services/eval/` 模块 + 双入口（middle ground）

```
src/openharness/services/eval/
  protocol.py             # Sample / Scorer / Score (Protocol)
  scorers.py              # Reusable: JSONStructure, FieldPresence, LLMRubric
  runner.py               # Async runner with VCR cassette support
evals/                    # data (gitignored cassettes/ subdir)
  extract/
    dataset.yaml
    scorer.py             # consumer-specific scorer wiring
  focus_state/
    ...
```

跑法：

```bash
# Path A: 自动化（CI gate / 定期）
oh eval extract --mode replay     # CI-friendly, deterministic, free
oh eval extract --mode live       # 真跑 LLM（手动触发）

# Path B: pytest 入口（开发时单 case debug）
pytest tests/services/eval/test_extract_eval.py::test_case_xyz --record
```

- ✅ 既是 CLI 又能 import 进 pytest debug 单 case
- ✅ Scorer 复用 OpenHarness Protocol 模式（同 substrate 哲学）
- ✅ cassette 默认 replay → CI 跑全部 eval 还是免费 + deterministic
- ⚠️ 比方案 1 多 ~3 task 基建，比方案 2 少 ~2 task（CLI 子命令薄壳）
- ⚠️ 数据 / 代码分离 —— dataset.yaml + scorer.py 分两个地方，要建心智
  模型说"哪里写什么"

---

## 七、我的初步推荐

**方案 3（Hybrid）+ 阶段性实施**。

具体说：

### Phase 16 = 最小可用 eval substrate + 给一个 service 装上

- **Substrate**（这部分是不变的基建）：
  - `services/eval/protocol.py` — `Sample` / `Scorer` / `Score` Protocol
  - `services/eval/scorers.py` — 内置 3 个 reusable scorer：
    - `JSONStructureScorer`（parse 成功 + schema 校验）
    - `FieldPresenceScorer`（必需字段非空、长度上限、regex 匹配）
    - `LLMRubricScorer`（最小 LLM-judge：单 model + rubric prompt，
      不做 ensemble，不做 calibration drift 追踪 —— 那是 Phase 17+）
  - `services/eval/runner.py` — async runner，含 VCR cassette wrapper
    （**用现成 vcrpy，不重造**）
  - `oh eval <service>` CLI 子命令，含 `--mode {replay,record,live}` flag
- **首个 service 落地**：选 `focus_state.py` —— 它输出最 contained
  （单 JSON 对象、两个字段），dataset 最容易构造（10-20 个 turn
  snippet 就有意义），LLM-judge 最容易写 rubric（"goal 和 expected
  goal 是否描述同一件事"二选一）
- **不做的**（明确推到后续）：
  - 多 judge ensemble
  - calibration drift 追踪
  - human annotation UI
  - cost dashboard
  - Inspect AI 整套 sandbox / Docker

### Phase 17 = 给剩下两个 LLM-driven service 装 eval（compounding test）

- 跑 extract.py 和 compact L4 的 eval suite
- 复用 Phase 16 的 substrate **零修改**（substrate-compounding 模式
  的第 N+1 次压测，和 `summarize()` 第 8 个 consumer 的精神同构）
- 如果某个 service 把 substrate 撑破了 → 触发 "premise wrong"
  escalation 重 reopen Phase 16 的 boundary

### Phase 18+ = LLM-judge calibration + bias 监控（如果到时候判断有必要）

只有当下面任意一条成立时再做：
- focus_state / extract 的 LLM-judge score 和我手审 agreement < 75%
- 我自己 dogfood 发现 eval pass 但产品体感差 —— 说明 judge 的 rubric
  本身有偏差
- 引入第二个 base model（OpenAI / Anthropic）需要跨 model 公平比较

---

## 八、还没拍板的题（next-step ratification gate）

进入 Phase 16 boundary doc 之前要先 ratify 的几件事：

1. **首个 eval 落地的 service**：focus_state.py（我推荐，理由见 §七）
   vs extract.py（dataset 更有"产品价值感"但 scorer 复杂） vs compact L4
   （最大但 dataset 最难构造）？

2. **Dataset 文件格式**：JSONL（appendable / machine-friendly）vs YAML
   （human-writable / OpenHarness Settings 风格一致）vs Python module
   （type-safe / 但不太能让外人改）？倾向 **YAML**（跟 `decisions/` /
   `tasks/` / settings 一致）。

3. **Cassette 存放策略**：
   - (a) commit 进 repo（`evals/<service>/cassettes/`）—— 简单，但 repo 膨胀
   - (b) gitignore + 每次 CI 重录 —— CI 慢 + 烧钱
   - (c) git-lfs —— OpenHarness 目前没 LFS，引入要 ask first
   - (d) 单独 `evals-cassettes/` 仓库，git submodule —— 重，但
     production-grade
   - 倾向 **(a) commit 进 repo，单个 case cassette ~ 5-20 KB，按目前
     dataset 规模（10-50 case/service）不超 1MB total**

4. **judge model 选择**：
   - 用 production model（qwen-plus）—— 简单，零成本引入
   - 用同 provider 不同 model（qwen-max）—— 避免完全 self-preference
   - 引入第二个 provider —— ratify "新依赖" 的事，违反 SPEC.md "Ask
     first" 规则
   - 倾向 **同 provider 不同 model**（DashScope 已经 wire 进来了，多
     model 切换是 model name 字符串差异，零基建变化）

5. **CI gate threshold**：
   - 硬 gate（任意 service 的 mean score < 0.8 → CI fail）—— 严格但
     可能 noisy
   - 软 gate（PR comment 显示 score delta，人审决定）—— 适合 single-dev
   - 不上 CI gate，eval 只在我手动 review prompt 改动时跑 —— 最简
   - 倾向 **不上 CI gate**（OpenHarness 是 single-dev project，CI gate
     的价值在多人协作；目前只要让我**改 prompt 时跑一次 eval 看数**
     就够了）

6. **是否复用 `summarize()` 作为 LLM-judge 的调用入口**：
   - YES（复用 retry / timeout / PTL 机制）—— Phase 7c retro §3.1 的
     精神延续，第 8 个 consumer
   - NO（judge 应该 fail-fast，不要 retry 把 score 数据污染） ——
     production 经验
   - 倾向 **YES**，但 judge 调用要 `timeout_seconds=10`（短于 production
     默认），retry attempts=1（不 2），避免 retry 数据污染

---

## 九、为什么不直接抄 DeepEval / Inspect

最后回到 Phase 7 retro §3.1 那个 "abstraction-first compounds" 的视角
问一句：**抄一个现成的 eval 框架进来到底是 net win 还是 net loss？**

| 假设抄 DeepEval | 推断结果 |
|---|---|
| 新依赖 | `deepeval`（依赖 `openai` / `langchain` 一堆 transitive deps） |
| Pydantic 版本冲突可能 | DeepEval 有些 metric 用 Pydantic v1，OpenHarness 是 v2 strict |
| 50+ 内置 metric 用到 | 0~2 个（structural + G-Eval） |
| 跟 OpenHarness Protocol 风格匹配 | 一般 —— DeepEval 是 class-based + decorator |
| 学习成本 | 1-2 天熟悉 metric 接口 |
| **真正写的代码量** | dataset + scorer 还是要自己写。框架省的是 "怎么 invoke metric" 这层 boilerplate ~50 行 |

| 假设抄 Inspect AI | 推断结果 |
|---|---|
| 新依赖 | `inspect_ai`（包含 Docker / web viewer 等一堆我不要的) |
| Sandbox 框架是核心 | OpenHarness 已经有自己的 permissions / hooks / sandbox 思路；冲突 |
| Solver/Scorer Protocol 设计 | **这部分思想可以借鉴**，但代码可以自己写 100 行 |
| **真正写的代码量** | dataset + scorer + 适配 Inspect Task 结构 |

**结论**：

- DeepEval / Promptfoo 是给"不想从头建 eval 基建的团队"的快捷方式 ——
  OpenHarness 不是那个 use case，**自建 100~200 行 substrate 反而更
  fit OpenHarness 的 Pydantic / Protocol / async-first 风格**
- Inspect AI 的 **Dataset/Solver/Scorer 三分** 是好抽象，**借鉴但不引入**
- vcrpy 是 mature stdlib-style 依赖，**值得直接引入**（HTTP cassette
  是非平凡基建，自建不划算）

这和 Phase 1 决定"自建 anti-corruption layer 而不是 LangChain"的判断
同构 —— **重要抽象自己写一遍，无差别基建直接复用业界**。

---

## Sources

- [Inspect AI（UK AISI）框架介绍 + Solver/Scorer 抽象](https://github.com/UKGovernmentBEIS/inspect_ai)
- [Inspect AI Scorers 文档](https://inspect.aisi.org.uk/scorers.html)
- [DeepEval（pytest-native eval）GitHub](https://github.com/confident-ai/deepeval)
- [DeepEval G-Eval metric 文档](https://deepeval.com/docs/metrics-llm-evals)
- [Promptfoo（OpenAI 2026-03 收购）vs 业界 alternatives](https://www.braintrust.dev/articles/best-promptfoo-alternatives-2026)
- [Anthropic Evaluations Framework（template library + multi-trial 模式）](https://github.com/anthropics/courses/blob/master/prompt_evaluations/README.md)
- [LLM Evaluation Tools 2026 Comparison Guide（inference.net）](https://inference.net/content/llm-evaluation-tools-comparison/)
- [AI Agent Eval Frameworks 2026: Testing Guide & Tools](https://www.digitalapplied.com/blog/ai-agent-eval-frameworks-testing-guide-2026)
- [LLM-as-Judge Best Practices 2026 — Calibration / Bias / Cost（FutureAGI）](https://futureagi.com/blog/llm-as-judge-best-practices-2026)
- [LLM-as-a-Judge 50%+ Bias Tests（Adaline）](https://www.adaline.ai/blog/llm-as-a-judge-reliability-bias)
- [Eliminating Flaky Tests: VCR for LLMs（Anay Nayak）](https://anaynayak.medium.com/eliminating-flaky-tests-using-vcr-tests-for-llms-a3feabf90bc5)
- [langchain-replay（录 decision，replay 时让 tool 真执行）](https://github.com/sixty-north/langchain-replay)
- [BAML VCR（专门给 LLM 调用的 cassette）](https://github.com/gr-b/baml_vcr)
- [vcrpy 文档](https://vcrpy.readthedocs.io/en/latest/usage.html)
