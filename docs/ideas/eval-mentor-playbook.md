# 假如我是 eval 专家，我会怎么给 OpenHarness 装 eval

> 写于 2026-06-02 · 中文版 · first-person mentor playbook
>
> 配套读物：
> - 方案对比 / Phase 16 决策草拟：[docs/ideas/eval-first-principles.md](./eval-first-principles.md)
> - 项目方法论：[CLAUDE.md](../../CLAUDE.md)
> - services/ substrate 由来：[learnings/phase-13.md §3.1](../../learnings/phase-13.md)
>
> 这篇不教你"用 DeepEval / Promptfoo 三个命令把 eval 跑起来" —— 那种
> 文章网上一抓一大把，看完不会让你变成 eval 专家，只会让你变成
> "会装 DeepEval 的人"。
>
> 这篇是 **eval 专家的思考路径** —— 我假装自己已经在 LLM 产品团队
> 跑过五六个 eval lab 了，今天接手 OpenHarness，会按什么顺序问问题、
> 在哪几步会停下来思考、专家和业余真正的分水岭在哪里。最后给一份
> **6 个月的能力建设 milestone**，让你能用这个项目把自己练成下次
> 别人面试问"你怎么给一个 LLM 产品做 eval"时，能给出有结构、有分寸、
> 不空喊"我们用 LLM-as-judge"的答案。
>
> 我把这篇当成给一年前的我自己写的，所以语气直接，省略客套。

---

## 〇、上来先说一件让人不舒服的事

如果你过去三年写过 web / backend / data pipeline，你大概已经习惯了
**"测试 = pytest"** 这套世界观。RED-GREEN-REFACTOR，覆盖率 80%，CI gate，
fail fast。这套世界观在传统软件里非常 work，因为传统软件的行为是
**确定性的** —— 同样的输入产生同样的输出，你测的是"代码到底做没做
正确的事"。

LLM 系统打破了这个前提。

LLM 是**条件概率分布上的采样**。同样的 input 跑 10 次会产生 10 个略
有不同的 output —— 不是 bug，是 by design。这意味着：

- 一个 pytest assert 类型的 "binary pass/fail" 测不了"prompt 质量"，
  因为 prompt 质量本身就是一个 **graded** 概念（"这次抽出的 memory
  比上次好 12%"）
- 一个测试通过不代表产品 work，只代表代码没崩
- 你**真正想知道的事** —— "我改了 prompt，产品体验是涨了还是跌了" ——
  pytest 完全测不出来

如果你以为 eval 是 "升级版的 pytest"，你会写出非常糟糕的 eval：要么
用 string equality 比对 LLM 输出（永远是 0% pass），要么用 80% 模糊匹
配（永远是 100% pass，毫无区分度）。我见过很多团队在这一步卡了半年。

**Eval 不是测试代码，Eval 是测试产品判断。** 你测的不是"模型有没
有按你的代码走"，是"你的 prompt + dataset + judge 这三件套组成的
产品判断 pipeline，是不是真的在解决用户问题"。

这是 step 0 —— 上来如果不把这件事吞下去，后面所有 step 都是装样子。

---

## 一、Eval 的世界观 —— 仪表盘，不是断言

### 1.1 三件套是 first principles，不是某个框架的接口

撕掉所有 marketing：

```
   Dataset       ──►   Runner   ──►   Scorer   ──►   Score
  (固定输入)        (跑被测对象)    (给输出打分)     (graded)
```

- **Dataset** = `(input, [optional expected])` 的固定集合
- **Runner** = 把每个 input 喂给被测对象，收集 output
- **Scorer** = `(input, output, expected) → number(s)`
- **Score** = 一个数（[0,1]）或一组数（多 dim）

这三件套是**所有 eval 框架的最大公约数**。DeepEval / Inspect AI /
Promptfoo / LangSmith / Braintrust 都是这三件套的不同投影。Inspect
AI 的 Solver 是 Runner 的 fancier 版本，G-Eval 是 Scorer 的一种实
现，Promptfoo 的 YAML config 是把这三件套**声明化**。

如果你脑子里只装了某一个框架的 jargon（"我们用 G-Eval 跟 RAGAS
metric"），你永远跳不出那个框架的盒子。装的是 first principles，
你能给任何 eval 平台快速做出 trade-off 判断。**记 jargon 不如记
Dataset/Runner/Scorer**。

### 1.2 Eval 跟 pytest 是兄弟，不是一回事

| 维度 | pytest | LLM eval |
|---|---|---|
| Pass/Fail | binary | graded |
| Determinism | required | 接受 stochastic，要可重复 |
| Input | 全覆盖 | 代表性抽样 |
| Failure 的含义 | "代码错了" | "**产品判断还不够好**" |
| 单次成本 | 微秒 / 免费 | 秒 / 烧 token |
| 反馈频率 | 每次 commit | 改 prompt / 换模型 / 定期 |
| 关键问题 | "代码做了它应该做的事吗?" | "我设计的 prompt 真的解决了用户的问题吗?" |

**关键 reframe**：pytest 是**测代码的工具**，eval 是**测产品判断的
仪表盘**。仪表盘的本质是给你 actionable 信号 —— 看完仪表盘你要知道
该改什么、改完应该往哪个方向跑。

如果你的 eval 跑完只输出 "78%"，没人知道接下来该改什么 —— 那这块
eval 就是装饰品。**专家眼里的好 eval 是"能引导你下一次 prompt 改
动方向"的 eval**。

### 1.3 **专家盲区揭示** —— 大家都会犯的第一个 framing 错

**错**：把 eval 当成"我以为我做对了，但要 cover my ass 弄个测试证明
一下"。这种心态出的 eval 通常长这样：

```yaml
# 一个新手会写的 eval（真的见过）
- input: "What is 2 + 2?"
  expected: "4"
- input: "What is the capital of France?"
  expected: "Paris"
```

跑完一看，模型答对了，pass rate 100%。然后？什么也学不到，因为你
**已经知道**这些题该答什么。这个 eval 是 confirmation tool，不是
discovery tool。

**对的姿势**：eval 是给你 **找到模型出错的地方** 的工具。专家造
dataset 时心里想的是 "我猜模型会在哪里翻车，让我去构造能让它翻
车的 case"。如果 eval 跑完发现 100% pass，专家的第一反应是 **"我
的 dataset 太简单了"**，不是 "我的产品很 strong"。

**记住这句话**：**好的 eval 是 designed to fail —— 故意造能让产
品翻车的 case，然后修产品**。pass rate 不是越高越好；pass rate 是
**让你知道这个 dataset 还能不能用**的指标 —— 100% 意味着 dataset
要升级，0% 意味着产品有问题，70-90% 之间最有信息量。

---

## 二、找到 Seam —— 你到底在 eval 什么？

在动手造 dataset 之前，你得先回答：**eval 的对象是哪一层**？

这是专家和业余的第二个分水岭。业余说"我们要 eval 我们的 LLM 产品"。
专家说"等等，你 eval 的是 model layer / prompt layer / pipeline layer
还是 system layer？这四层 dataset 和 scorer 完全不同"。

### 2.1 LLM 系统的四层 seam

```
 ┌─────────────────────────────────────────────────────────────┐
 │  System layer: 整个产品对真实用户的端到端价值                │
 │  (任务完成率 / 用户留存 / 客服 ticket 减少率)                │
 ├─────────────────────────────────────────────────────────────┤
 │  Pipeline / Agent layer: 多步交互、tool use、retrieve →     │
 │  reason → act loop。OpenHarness 的 `oh ask "<task>"` 在这层  │
 ├─────────────────────────────────────────────────────────────┤
 │  Prompt layer: 单 prompt → 单输出的关系。OpenHarness 的     │
 │  `services/extract.py` + `EXTRACTION_SYSTEM_PROMPT` 在这层   │
 ├─────────────────────────────────────────────────────────────┤
 │  Model layer: 给定 prompt，模型本身的能力                    │
 │  (推理 / 知识 / 安全。这是 model card 上的事，不是产品的事)  │
 └─────────────────────────────────────────────────────────────┘
```

每一层有不同的 eval 形态、不同的 dataset 来源、不同的 cost：

| Layer | Dataset 来源 | Scorer 形态 | 单次 cost | 业内代表 |
|---|---|---|---|---|
| Model | MMLU / HumanEval / GSM8K 等公开 benchmark | exact match / parser | 中 | OpenAI Evals / lm-eval-harness |
| Prompt | 你自己构造的 (input, expected) | structural + LLM-judge | 低 | DeepEval / Promptfoo |
| Pipeline | 任务描述 + final state | end-state assertion | 高 | Inspect AI / τ-bench |
| System | 真实用户 conversation log | 业务 metric | 极高 | LangSmith / 内部 dashboard |

**关键认识**：**eval 你不该做的层，比 eval 你该做的层更重要**。

举例：OpenHarness 不该做 Model layer eval —— 你不会重新发明 MMLU，
那是模型厂的事。OpenHarness 也不该上来就做 System layer —— single-dev
project 没有真实用户 log，不存在端到端 business metric。

OpenHarness 真正能做的是 **Prompt layer** 和（如果野心大）**Pipeline
layer**。Phase 16 应该明确锁定在 **Prompt layer**，理由见下一节。

### 2.2 OpenHarness 的实际 seam 在 `services/summarize()` 上

打开 `src/openharness/services/summarize.py`，你会看到：

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

这个接口的 shape 是 **"input messages + system_prompt → output str"**。
**这恰好就是 Prompt layer eval 的最小契约** —— eval 一次 = 喂一组
固定的 (messages, system_prompt)，看输出 str 是否符合预期。

更妙的是 OpenHarness 现在有 **7 个 consumer 共享这同一个 substrate**：

| Consumer | system_prompt 来源 | 输出 parse 方式 |
|---|---|---|
| `compact.py` L4 | `_L4_COMPACT_SYSTEM_PROMPT`（9-slot summary） | `<slot>` tag 切片 |
| `extract.py` | `EXTRACTION_SYSTEM_PROMPT`（durable memory 抽取） | JSON parse + Pydantic 校验 |
| `focus_state.py` | `FOCUS_STATE_SYSTEM_PROMPT`（goal + next_step） | tolerant JSON parse |
| `/compact` REPL | reuse compact L4 prompt | 同 L4 |
| (Phase 14+) verified_work / recent_files enrichment | TBD | JSON |

每个 consumer 都是 **"拼自己的 system_prompt → 调 summarize() → parse
自己的输出"**。这意味着：

- 你不应该 eval `summarize()` 本身 —— 它没有语义，只是 wrapper
- 你应该 eval **每个 consumer 自己的 system_prompt + parser 组合**
- 三个 consumer 的 eval 形态 **共享 80% 基建**（runner、cassette、
  scorer protocol），只在 dataset 内容和具体 scorer 逻辑上有差异

这是 OpenHarness lucky 的地方 —— **Phase 11 的 substrate 抽象设计正
好对偶到 eval 的层级**。Phase 16 的 eval substrate 也会有类似的
substrate-vs-consumer 关系：eval substrate 提供 shared 机制，每个
service 提供 specific 语义。Phase 7c retro §3.1 的 "abstraction-first
compounds" 在 eval 层会复刻一次。

### 2.3 **专家盲区揭示** —— Pipeline eval 是甜蜜陷阱

很多团队第一次做 eval 的时候，看到 SWE-bench / GAIA 这种 agent
benchmark 上线，会两眼放光想"我们也跑这个"。这是典型的 **甜蜜陷阱**。

Pipeline-layer eval 看起来最性感 —— "我们的 agent 在 SWE-bench 上
拿了 25%"，听起来很 enterprise。但实际成本：

- 一个 SWE-bench case 跑一次几分钟、几块钱
- 完整 dataset 几百到几千 case
- 单次跑全集要几百块、几小时
- 改 prompt 后重跑 → 再几百块、几小时
- 而且 **failure mode 极难诊断** —— agent 在 step 7 答错了，是因为
  step 3 的 retrieval 不行？还是 step 5 的 prompt 不对？还是 step
  7 的 tool spec 设计错？

业余想"我们一上来就 cover 所有层"，结果 6 个月跑了一次完整 SWE-bench，
没改进任何 prompt，没积累任何 dataset，钱烧光了。

专家会说："先把 prompt layer 的反馈环建起来 —— 改一次 prompt 5 分
钟内知道效果。Pipeline 层 dataset 等 prompt 层 mature 了再上"。

**OpenHarness 上的具体建议**：
- Phase 16 / 17：只做 Prompt layer eval（focus_state → extract → compact L4）
- Phase 18+：再上 Pipeline layer，target 是 `oh ask "<task>"` 的几个 controlled task（比如"修一个 import 错误"、"添加一个 pydantic field"）
- **永远不做** System layer（你没有真实 user）和 Model layer（你不是 model lab）

---

## 三、Dataset 构造 —— 真正区分专家和业余的地方

我说过 "业余卡在这一步"。我再说一遍：**dataset 构造是 eval 全流程
中最被低估、最容易做坏的一步**。Scorer 设计错了你会知道（score
没区分度）。Runner 搭错了你会知道（CI 跑挂）。**Dataset 造错了你
不会知道** —— 你只会持续被一个有偏的 dataset 误导半年。

### 3.1 一个 dataset 的"反组成"—— 不该是什么

**反组成 1**：不是从生产 log 随便 sample 100 个。生产 log 里 95%
的 case 都是"用户问了一个简单问题，模型答对了" —— 这些 case 在 eval
里是 **deadweight**，不区分 A prompt 和 B prompt。你 sample 1000 个
deadweight + 50 个有区分度的，相当于把信号稀释了 20 倍。

**反组成 2**：不是 "我编 20 个有代表性的 case"。这种 dataset 看
起来 OK，但有两个致命问题：
- 你编的 case 里所有 **你已经知道答案** 的题，模型大概率都答得对
  （pass rate 100%，毫无 discriminator）
- 你编的 case 漏掉 **你不知道你不知道** 的 failure mode（dataset
  对自己的盲区无知）

**反组成 3**：不是 "我把 50 个 case 都标了 expected output，让 LLM
判断输出是否等于 expected"。这把 eval 变成"模型是不是逐字 mimic 我
预设的答案"，完全不是产品判断。

### 3.2 一个好 dataset 的三类 case

```
                    ┌── Representative (60-70%)
                    │   日常 case；产品 90% 用户场景的抽样
                    │
   Dataset(50-200个)─├── Edge (20-30%)
                    │   边界 / 异常 / 模糊场景；
                    │   设计来挑战产品判断力
                    │
                    └── Adversarial (5-15%)
                        故意构造的 trick case，
                        目标是 stress-test prompt 的盲区
```

**Representative**：抽样产品高频场景。**关键约束：覆盖 input 分布的
不同 region，不是平均分配 case 数**。如果 70% 用户问 A 类问题，30%
问 B 类，dataset 应该 70:30。

**Edge**：业余 dataset 100% 缺这个。Edge case 是 "产品判断真正发挥
作用的地方"，比如：
- 模棱两可的输入（用户的意图不明确）
- 多义的 expected output（有好几种合理答案）
- 极端长度（超长 / 极短）
- 跨语言 / 跨格式
- prompt 里 example 之间的 gap region（example 没覆盖到的临界场景）

**Adversarial**：专家会特意 construct case 来 stress-test。比如对
`extract.py`，adversarial case 可能是：
- 一段对话里**故意混入一个 fake secret**（"我的 API key 是 sk-fake-1234"）
  —— 测试 secret pattern blocking 是否真的 work
- 一段对话**只讨论 trivial debugging**（"我 typo 了一个 import"）
  —— 测试是否会被 false-positive 抽出"durable memory"
- 一段对话**讨论用户的私人偏好**（"我喜欢 Python，但讨厌 type
  annotations"）—— 测试 scope=private 和 scope=team 是否能正确区分

### 3.3 Ground truth 的四种来源 —— 每种都有自己的偏差

**来源 1：人审标注（gold label）**
- 你（或专家）逐 case 写 expected
- 偏差：标注者偏好被锁进 dataset，可能不代表"产品应该长什么样"
- 适合：少量 case（< 100）、高质量、用作 calibration baseline

**来源 2：LLM 合成（silver label）**
- 用一个更强的 LLM（比如 Claude Opus）生成 expected
- 偏差：silver label 自带 LLM 的偏好，eval 时再用 LLM-judge → 双重
  LLM 偏好叠加，score 虚高
- 适合：dataset 扩量、bootstrap 阶段
- 关键约束：**至少 10-20% 的 silver label 必须被人审核校**，否则
  整个 dataset 是空中楼阁

**来源 3：用户反馈（feedback signal）**
- 从生产里收集"用户标了 👍/👎 的 case"
- 偏差：survivorship bias —— 满意的用户不点 👍，不满的用户点 👎 但
  通常已经放弃产品了
- 适合：长期收集，作为 eval dataset 的补充信号
- OpenHarness 上下文：你没有用户，所以这条暂时用不上

**来源 4：编程化生成（programmatic）**
- 用 template / 组合 / 规则生成 case
- 偏差：生成出来的 case 极有可能是 **distribution 上的"中段"**，
  缺乏 edge / adversarial
- 适合：构造 stress-test set、覆盖率验证

**OpenHarness 上的具体配方**：

对 `focus_state.py` 这种小输出（goal + next_step），我会这么造 dataset：

1. **人审 30 个 representative case**：从我自己用 OpenHarness 的真实
   session log 里手挑 30 段 6-message 片段，每段我手写 expected goal
   + next_step。这 30 个是 **gold standard**。
2. **LLM 合成 30 个 edge case**：让 Claude Opus 帮我生成"对话不清晰
   的"、"用户中途切换 topic 的"、"全是 tool use 没有 text 的"等。
   然后我**逐个 review**（这一步不能省）。
3. **手写 10 个 adversarial**：故意构造 trick，比如"用户问了一个问题但
   助手在分析一个完全无关的事"、"用户说 done 但其实还有未完成的事"。

总共 70 个 case，2-3 小时工作量。这是**人类专家工作量**，没有任何捷径。
"我用 LLM 一晚上生成 5000 case" 的 dataset 价值远低于这 70 个人审 case。

### 3.4 Dataset 的 meta-dataset —— 专家会追踪的东西

业余的 dataset 是一个 JSON 文件，里面 100 个 case。

专家的 dataset 是一个文件夹，里面：

- `dataset.yaml` — 100 个 case
- `dataset_card.md` — meta 文档：每类 case 几个、来源、标注日期、
  谁标的、known bias
- `coverage.md` — 我这 dataset 覆盖了产品行为空间的哪几个维度，没
  覆盖的承认在哪里
- `version_log.md` — 这 dataset 的演进史

**为什么要这些**：因为 **dataset 本身会 drift**。半年后你不记得当时
为什么选这些 case，新加的 case 跟旧 case 的分布是否一致，retire
掉的 case 为什么要 retire。没有 meta-dataset，你的 eval 半年后会
**自带偏差却没人知道**。

### 3.5 **专家盲区揭示** —— "更多 dataset = 更好" 是错的

新手最常见的错觉："我们要 scale dataset"。你打开论文，看到 MMLU
14000 题、HellaSwag 70000 题，觉得 dataset 越大越好。

不对。**dataset size 是为了减小 statistical variance；超过某个临界
点，多加 case 不会让 eval 更可信，反而会稀释信号**。

- 50 个 carefully curated case 的 representative power > 5000 个
  scraped case
- 一个 frontier-LM 团队（Anthropic / OpenAI）的内部 prompt-layer eval
  通常每 prompt 50-300 case，**很少超过 500**
- 超过 500，单次 run 成本爆炸，但 score 区分度的边际增益已经趋零

**判断 dataset 够大的方法**：跑 A prompt 和 B prompt 各 3 次，看 score
的 **方差**。方差小于 mean 差异的 1/3 → dataset 够大。方差大于
mean 差异 → 加 case。但如果你已经 200 个 case 了方差还大，**问题
不是 dataset 太小，是 scorer 噪音太大**（去 §四 修 scorer，不是
加 dataset）。

**OpenHarness 上的具体规模建议**：

| Service | Phase 16/17 dataset size | 理由 |
|---|---|---|
| focus_state | 50-80 | 输出 schema 简单，case 易构造 |
| extract | 30-60 | 单 case 信息量大（全 turn 上下文），人审成本高 |
| compact L4 | 20-40 | 单 case 上下文极长（trigger 是 token 阈值），构造成本最高 |

总量 ~150 case，**完全够支撑 OpenHarness 在 Phase 17 后 12 个月内
的 prompt 迭代**。半年后如果发现 score 不区分度，先去看 scorer，
不要先去看 dataset 量。

---

## 四、Scorer 设计 —— 二维矩阵 + 单元化

到这一步如果你的 dataset 已经造好，恭喜 —— 后面的工程相对清晰。
Scorer 设计有 framework，按 framework 走基本不会出错。

### 4.1 二维矩阵 —— 把 scorer 分四象限

```
                        程序化 (programmatic)
                                │
                                │
         结构 (structural)      │   语义 (semantic)
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        │  象限 I:              │  象限 III:           │
        │  JSON parse 成功?     │  (基本不存在)         │
        │  字段非空?            │  程序化判断语义       │
        │  regex match?         │  → fuzzy string match │
        │  schema validate?     │  (噪音大，不推荐)      │
        │                       │                       │
        ├───────────────────────┼───────────────────────┤
        │                       │                       │
        │  象限 II:             │  象限 IV:             │
        │  (基本不存在)         │  LLM-as-judge:        │
        │  语义判断结构         │  faithfulness?        │
        │  → 没意义             │  helpfulness?         │
        │                       │  rubric agreement?    │
        │                       │                       │
        └───────────────────────┴───────────────────────┘
                                │
                                │
                          LLM-judge
```

**核心配方**：象限 I 是第一道防线（cheap、deterministic、可
解释），象限 IV 是第二道防线（贵、stochastic、覆盖语义）。象限
II 和 III 基本不用，前者没意义，后者噪音大。

**Scorer 的应用顺序**：
1. 先跑象限 I —— 如果 structural check 都过不了，根本不用上 judge
2. 象限 I 通过的 case 才上象限 IV
3. 最终 score 是 `structural_score * semantic_score`，**乘法不是加法**
   （任何一项不及格都该让 case 整体不及格）

### 4.2 程序化 scorer 的 sub-types

我把象限 I 进一步拆成 5 类，按经验每个 service 都会用到 3-4 类：

| Sub-type | OpenHarness 例子 |
|---|---|
| **Parse 成功** | `json.loads(output)` 不 raise |
| **Schema validate** | 字段都存在、类型对、枚举值合法（Pydantic v2 strict 直接 reuse） |
| **Regex / pattern** | `name` 是 kebab-case、`memory_id` 是 `01H[A-Z0-9]+`、不含 secret pattern |
| **Length bound** | 输出 token 数在 [10, 500]、字段长度上限 |
| **Cross-field consistency** | `extract` 的 type 是 `feedback` 时 scope 必须是 `private` 等业务约束 |

OpenHarness 的优势：**这 5 类全可以 reuse 已有代码**。Pydantic v2
strict 模式直接给你 schema validate；`memory.signature` 的 regex
已经存在；`check_team_memory_secrets()` 已经存在。**写 scorer 应该
是从产品代码里"切下来"，不是新建**。

### 4.3 LLM-as-judge 的 5 个设计 trap

LLM-judge 写错了 eval 就废了。业内 2026 年总结的 5 类偏差我必须
让你记住，**不记住你后面会反复中招**：

| 偏差名称 | 触发条件 | 缓解 |
|---|---|---|
| **Position bias** | pairwise 比较时换 A/B 顺序，judge 倾向第一个 | 跑两次 (A,B) + (B,A) 取平均；或单独打分而不 pairwise |
| **Verbosity bias** | judge 给长输出更高 score | rubric 里明确说"长度不计分"；或对输出长度归一化 |
| **Self-preference** | 用 GPT-4 当 judge 评 GPT-4 输出，分被自抬 5-7% | judge 用不同 model family（OpenHarness 用 qwen-max 评 qwen-plus 输出） |
| **Format bias** | 输出带 emoji / markdown / code fence 时 score 不同 | rubric 里说"格式不计分"；或 strip format 后比较 |
| **Calibration drift** | judge 模型半年升级，旧 score 失去对照价值 | 月度跑 calibration 集，跟 human-eval baseline 比 |

**写 judge rubric 的 5 条原则**（这是专家手感）：

1. **二选一比五分制好**。"输出是否准确反映了 input？yes/no" 比
   "1-5 分" 信号清晰得多
2. **chain-of-thought 显式要求**。让 judge 先写理由再给分；不写理由
   的 judge score 噪音大 30%+
3. **example 给两个 + 解释为什么 + 一个 borderline**。三个例子里要
   有"明显 yes / 明显 no / 灰色地带的边界"
4. **prompt 短**。judge prompt > 800 字时 score 噪音会上升；目标
   < 500 字
5. **judge 输出强制结构化 JSON**。`{"score": 0|1, "reason": "..."}`，
   parse 失败 case 标 invalid 跳过，**不要 fallback 给 0**（会污染
   分布）

### 4.4 Composite score 的陷阱

你的每个 case 跑出来一组 (structural_score, semantic_score, ...)，
最终要 aggregate 成什么？

**业余做法**：一个总分，比如 `0.6 * structural + 0.4 * semantic`，
然后 dataset 平均，得到 "我们 prompt 的总分 0.83"。

**专家做法**：**多维 score，永远不 collapse**。

```
focus_state eval result:
  - parse_success_rate:        96%  (3 case 失败)
  - schema_valid_rate:         94%
  - goal_judge_score (mean):   0.78
  - next_step_judge_score:     0.71
  - dataset-mean composite:    0.71  (受 next_step 拖)

  失败 case breakdown:
  - 3 case parse 失败 (judge 看了发现 LLM 加了 markdown 体)
  - 8 case judge 觉得 next_step 模糊
  - 12 case 是 borderline (judge 自己也 50% confidence)
```

这个 multi-dim 输出告诉你 **下一步该改 prompt 哪部分**（next_step
的指引不够具体），而 "总分 0.71" 啥也告诉不了你。

**Goodhart's law warning**：一旦你把 composite score 当成优化目标，
你的 prompt 会被 over-optimize 到这个 score 上 —— 但 score 是真实
质量的 proxy，proxy 优化到极限会偏离原本的真实质量。专家追踪
**多个 score + 它们之间的关系**，不是 single number。

### 4.5 **专家盲区揭示** —— Scorer 自己也是 dataset

新手以为 scorer 是 "一段固定逻辑"。专家知道 scorer 是 **第二个
dataset** —— 你的 judge rubric 是 prompt，你的 reference example 是
case，你的 calibration set 是 ground truth。Scorer 也会 drift，也
需要 version、需要 review、需要 retire 老 example。

**实操规则**：scorer 文件改动应该和 dataset 改动一样郑重 —— 进 git
commit 时写清楚 "改 rubric 第 X 条因为 case 12 在 borderline 反复
横跳"，6 个月后你想理解 score 趋势时能查到。

---

## 五、Runner + cost 工程 —— 让 eval 跑得起 + 留得下

到这一步，dataset 和 scorer 都搞好了 —— eval 已经可以工作。但能
工作不等于能 **持续工作**。Runner 这层处理的核心问题：**让 eval
跑得起（cost）+ 留得下（reproducibility）**。

### 5.1 Cost / determinism / replay 三角

eval 跑一次的真实成本：

```
单次 eval cost = N case × (LLM API 调用 cost + judge 调用 cost)
              + 人力 review failure case 的时间
```

OpenHarness 假设：50 case × (qwen-plus 调用 0.02$ + judge 0.01$) = 1.5$
per run。一周改 5 次 prompt，一年 ~390$ —— 不大但累计。

但 cost 不是真问题，**determinism 才是**。LLM 是 stochastic 的，
跑两次同一 case 可能 score 不同。如果你的 eval **每次 score 都漂**，
你根本不知道 prompt 改动效果。

**三角解法**：

```
        ┌────────────────┐
        │     LIVE       │  真打 LLM API
        │   (录 + 评)    │  作用: 录 cassette、final ratify
        └───────┬────────┘
                │
                ▼
        ┌────────────────┐
        │    RECORD      │  打 LLM 并存 cassette
        │  (eval + 缓存)  │  作用: 改 prompt 后录新 baseline
        └───────┬────────┘
                │
                ▼
        ┌────────────────┐
        │    REPLAY      │  从 cassette 读
        │   (CI 默认)    │  作用: deterministic + 免费
        └────────────────┘
```

**模式之间的迁移规则**：
- LIVE 模式只在 ratify "新 prompt 真的 OK" 的瞬间用一次
- RECORD 模式在改 prompt 后跑一次，diff cassette → 看 LLM 响应是不是
  真的变了 + 看新 score
- REPLAY 模式是 CI 默认 + 日常 dev 默认 —— 完全 deterministic + 免费

### 5.2 VCR cassette —— 被低估的工程

[vcrpy](https://vcrpy.readthedocs.io/) 是 mature Python 库，HTTP-level
record/replay。LLM 调用走 HTTP，自然能录。

**OpenHarness 上的 cassette 设计**：

```
evals/
  focus_state/
    dataset.yaml
    scorers.py
    cassettes/
      qwen-plus/
        case-001-vague-goal.yaml   # 一个 case 一个 cassette
        case-002-tool-only.yaml
        ...
```

**关键设计决策**：

1. **一个 case 一个 cassette 文件**，不是大 cassette 包所有 case ——
   case 增删时 diff 干净
2. **cassette 路径按 model 分子目录** —— 换 model 重录，旧 cassette
   保留作对照
3. **cassette commit 进 git**（按 [eval-first-principles.md §八](./eval-first-principles.md) §8 第 3 条），单 case ~10KB，
   150 case 1.5MB 量级 —— repo 能扛
4. **cassette filter 掉非确定字段** —— request body 里的 `request_id`、
   `timestamp` 之类要 normalize，不然 replay 时 hash 不对

### 5.3 CI 集成的 trade-off

eval 上 CI 是一个 product engineering 决策，不是技术决策。

| 模式 | 何时用 | OpenHarness 推荐? |
|---|---|---|
| **Hard gate**（mean score < threshold → CI fail） | 多人协作团队、有 release pipeline | ❌ 单人项目 over-engineering |
| **Soft gate**（PR comment 显示 score delta） | 中型团队、需要 review buffer | ⚠️ 你一个人，没人 review |
| **Push-trigger replay only**（每次 push 跑 replay 模式） | 个人项目、cost 极敏感 | ✅ 推荐 |
| **Schedule live**（nightly / weekly 跑一次 live） | 想知道 model drift / API 变化 | ⚠️ Phase 17+ 可考虑 |
| **手动触发**（改 prompt 时 `oh eval` 手跑） | 早期 / dataset 还在演进 | ✅ Phase 16 起手 |

**OpenHarness 实操**：
- Phase 16/17：CI 只跑 replay 模式（免费 + 强 determinism）+ 我手动改
  prompt 时跑 record
- Phase 18+：考虑 weekly schedule live 跑（catch API drift / model
  silently 升级）

### 5.4 **专家盲区揭示** —— Replay 模式的隐藏坑

VCR 模式有一个 **致命 trap** 容易踩：**replay 的 score 不是产品真实
score，是"上次 record 时的 LLM 响应被现在的 scorer 打分的结果"**。

举个让人不舒服的具体例子：

```
Day 1: prompt A → record cassette  → scorer α → score 0.75
Day 5: prompt 没改 → 改 scorer α → α' → replay 旧 cassette → score 0.85
```

你以为 prompt 涨了 0.10？**没有**。LLM 响应跟 Day 1 完全一样，是
**scorer 变了**。

专家防这个坑的方法：

1. **scorer 改动后必须 LIVE 重跑** —— 不能只 replay
2. **cassette 文件里嵌 scorer hash** —— scorer 一改，CI 自动 invalidate cassette
3. **每次 record 时存的不只是 LLM response，还有当时 scorer 给的 score** —— 后续 diff 时直接看 raw score 变化，不重新打分

OpenHarness 的最小实现：每个 cassette 文件 header 加 `scorer_version:
<hash>`，replay 时 mismatch 就 warn。

---

## 六、Calibration loop —— 让 eval 反哺 prompt 迭代

到这一步你已经有可用的 eval。但 eval **本身不会让 prompt 变好** ——
eval 是 thermometer，温度计不能让发烧的人退烧。

让 eval 变成 actionable 反馈环的环节叫 **calibration loop**：

```
                ┌──────────────────────────────────┐
                │  改 prompt (or 换 model / dataset) │
                └───────────────┬──────────────────┘
                                │
                                ▼
                ┌──────────────────────────────────┐
                │  跑 eval (LIVE 录 cassette)        │
                └───────────────┬──────────────────┘
                                │
                                ▼
                ┌──────────────────────────────────┐
                │  Multi-dim score 出来              │
                │  + 失败 case breakdown             │
                └───────────────┬──────────────────┘
                                │
                                ▼
                ┌──────────────────────────────────┐
                │  人审 N 个失败 case              │
                │  → 归类 failure mode             │
                └───────────────┬──────────────────┘
                                │
                                ▼
                ┌──────────────────────────────────┐
                │  根据 failure mode 改 prompt        │
                │  (不是改 dataset!)                 │
                └───────────────┬──────────────────┘
                                │
                                └──── 回到第一步
```

### 6.1 失败 case 归类是 calibration 的核心

你跑一次 eval，得到 12 个失败 case。**业余**：去改 prompt 让这 12 个
case 都过。**专家**：先归类，**12 个 case 大概只有 3-4 种 failure
mode**，针对每个 mode 想 prompt 改进。

OpenHarness 上的 failure mode 归类示例（focus_state.py）：

| Failure mode | 典型 case | Prompt 改进方向 |
|---|---|---|
| "对话不清晰时 LLM 强行编 goal" | 用户只说了 "hmm" | prompt 加 "如果无法推断，返回 null" |
| "LLM 把 tool name 当 goal" | 全是 tool use 的 turn | prompt 加 "不要把 tool 名字当作 goal" |
| "JSON 带 markdown fence" | LLM 套了 ```json ``` | 改 prompt 措辞 / 增强 parser tolerance |

**这是 prompt engineering 的真正样子**：你不是凭直觉改措辞，你是
**针对一类 failure mode 做最小可定向改动**。每次改 prompt 都对应
一个具体的归类，git commit 写 "address failure mode F3: tool-only
turn"。

### 6.2 Judge agreement 追踪

这是专家的另一个 ritual。每月一次，从 dataset 里 sample 30 个 case，
你自己人审打分，跟 LLM-judge 打分做对比：

```
Agreement = % (你的 score 跟 judge score 同 sign)
```

- agreement > 85%：judge 还 work，继续用
- agreement 75-85%：警告，看是不是 dataset drift
- agreement < 75%：**judge prompt 必须重新校准，停止信任 eval 结果**

agreement 跌破时常见原因：
- judge 模型悄悄升级了（API 厂改了 default version）
- dataset 加了新类型 case，judge prompt 没 cover
- 你的人审标准变了（半年前的你和现在的你判断不同）

不做 judge agreement 追踪的 eval 是 **盲飞**。半年后你以为 score
0.85 是好，其实 judge 已经 drift 到给所有东西都 0.85。

### 6.3 **专家盲区揭示** —— 改 dataset 反向工程 score 是作弊

最容易犯的隐性错误：**改 dataset 让 score 看起来涨**。比如发现 8 个
case 总是 fail，删掉它们 → score 从 0.75 涨到 0.91。**这是数据
作弊**。

对的姿势：fail case 不删 / 不改，**单独 mark 成"已知 hard case"**。
prompt 改完后专门看这些 case 是否从 fail 变 pass。永远不要静默删
fail case 提高 metric —— 那是 self-deception。

类似的隐性 cheating：
- 跑多次取 best score（cherry-pick）
- judge 跑多次取 majority（OK，但要 reproducible）
- 换 judge model 一直到 score 上去（**严重 anti-pattern**）
- prompt 里偷偷 inject 测试时才用的 hint（数据泄漏）

专家 ritual：**eval result 的可信度依赖于 dataset / scorer / judge
都 frozen**，三件套有一件变了就要 acknowledge "score 不可比"。

---

## 七、Meta-eval —— 你的 eval 怎么知道自己 work

这一章是分水岭。能想到 meta-eval 的人就是 eval 专家，想不到的就是
"会用 eval 工具的工程师"。

**Meta-eval 是 eval 的 eval —— 你怎么知道你的 eval 套件本身 work
得很好？**

### 7.1 Meta-eval 的三个问题

**Q1：你的 dataset 真的代表产品的真实 input 分布吗？**

测试方法：
- 你假设产品上线后 input 长 X 样
- 你的 dataset 里有多大比例的 case 长 X 样
- 你 dataset 里 0% 出现但生产可能 5% 出现的 input 类型有哪些

OpenHarness 上下文：你没有生产 input log，所以这一题要用 proxy ——
我自己作为 user 用 OpenHarness 时跑出来的 turn 长什么样。如果 6
个月后我发现一类我从来没用过但别人会用的场景（比如长 conversation
没有 tool use 全部 text-only），dataset 要补。

**Q2：你的 scorer 真的捕捉到了用户在乎的事吗？**

测试方法：
- 让 scorer 跑 dataset，得到 ranking
- 让 human（你自己）盲测同 dataset，给同样 ranking
- 两个 ranking 的 Spearman correlation > 0.7 → scorer 抓到了真相
  ；< 0.5 → scorer 偏掉了

这是判断 scorer 是否 representative 的硬指标。每季度做一次。

**Q3：你的 eval 改动反映在产品改动里了吗？**

**最锐利的 meta-eval 问题**：
- 过去 N 次 eval-driven prompt 改动里
- 有几次让 production 体验真的好了（你 dogfood 时感觉到的）
- 有几次 eval 涨了但 dogfood 没感觉
- 有几次 eval 不动但 dogfood 觉得变好了

如果 "eval 涨但 dogfood 没感觉" 的比例 > 30%，**你的 eval 是 proxy
错了** —— score 跑赢但抓的是错维度。回到 §四 重新设计 scorer。

### 7.2 Dataset 的 lifecycle

Dataset 不是"建一次永远用"。专家的 dataset 有 lifecycle：

```
新案例进入 ──► 候选池 ──► review ──► 加入 production set
                              │
                              ├──► 拒绝 (out of distribution / duplicate)
                              │
                              ▼
                         active 期 (3-12 月)
                              │
                              ├──► score 趋稳 (90%+ pass 6 月连续) → 候选 retire
                              ├──► score 趋零 (全部 fail) → 候选 retire (或拆解)
                              └──► score 仍有信息量 → 留任
                              │
                              ▼
                          retire 池 (归档但不参与日常 score)
```

**为什么要 retire**：
- 100% pass case 不再 discriminate，留着拖累信号
- 100% fail case 是产品根本不能做（应该改产品 spec，不是反复 eval）
- dataset 长期不动会让 prompt 朝 dataset overfit

**OpenHarness 上的 rule of thumb**：每季度 audit 一次 dataset，retire
连续 2 quarter 都是 100% pass 的 case，新加 5-10 个 case 替补。维持
**150 case 总量 + 季度 5-10% 周转**。

### 7.3 **专家盲区揭示** —— Eval 是协作工具，不是裁判

这个比较 subtle，但重要。

业余把 eval 当 **裁判**：eval 跑完出分，分高 = prompt 好。

专家把 eval 当 **协作工具**：eval 跑完看 multi-dim 输出 + failure
breakdown，**和自己（或团队）的产品判断对话**。如果 eval 出了一个分
但 dogfood 体感矛盾，**不是 dogfood 错了，是 eval 抓的维度需要补**。

具体表现：
- 业余："eval 说 prompt B 比 A 好，所以 ship B"
- 专家："eval 说 B 比 A 高 0.12，但我 dogfood 时 A 让我用着更顺。
  是不是 eval 没抓到流畅度这个维度？" → 加 scorer dim

这是 **eval thinking** 的成熟标志：你不再被 eval 数字绑架，你用 eval
数字 **augment** 你的产品判断，两者**互相校准**。

---

## 八、6 个月学习路径 —— 把这个项目当能力沙盒

我把 6 个月拆 4 个 milestone。每个 milestone 末尾有一个"你能不能给
别人讲清楚"的自测，过了那个自测才算到位。

### Milestone 1：Week 1-2 —— 把第一个 service 装上 eval

**目标**：focus_state.py 上从 0 到 70 个 case + 4-scorer 跑通 replay 模式

**任务清单**：
1. 读 `services/focus_state.py` 直到能 1 分钟讲清楚 input / output / parse 逻辑
2. 手挑 30 个 representative case，来源是我自己跑 OpenHarness 真实 session log
3. 让 Opus 帮我生成 30 个 edge case，**逐个人审**
4. 手写 10 个 adversarial case
5. 写 4 个 scorer：parse_success / schema_valid / goal_judge / next_step_judge
6. 录第一组 cassette
7. 跑 replay 模式 → 第一次 eval result

**自测**：你能不能用 30 秒说清楚 "我为什么需要 4 个 scorer，每个抓什么"？
讲不清楚 → 回去想 §四 scorer 矩阵。

### Milestone 2：Month 1 —— 三个 service 装齐

**目标**：focus_state / extract / compact L4 三个 service 都有自己的
dataset + scorer + cassette + 第一次 LIVE → REPLAY 切换

**任务清单**：
1. 抽象出 Phase 16 的 eval substrate（参考 [eval-first-principles.md](./eval-first-principles.md) 方案 3）
2. extract 装 50 case + 5 scorer（含 JSON schema + secret check + memory `__post_init__`）
3. compact L4 装 30 case + 4 scorer（含 9-slot tag 检查 + faithfulness judge）
4. `oh eval` CLI 子命令 ship —— record / replay / live 三态
5. CI 跑 replay 模式（每次 push）
6. 第一次 prompt 改动 → eval → calibration

**自测**：你能不能给一个不了解项目的工程师 5 分钟讲清楚 "我这个 eval
substrate 跟 DeepEval 的区别是什么、为什么这么做"？讲不清楚 → 回去
想 §一 first principles。

### Milestone 3：Month 3 —— Calibration loop + judge agreement 追踪

**目标**：eval 不只是出分，开始让产品 prompt 真的迭代起来

**任务清单**：
1. 每周改一次 prompt + 跑 record → 形成肌肉记忆
2. 月度跑 judge agreement check（30 case 人审 vs LLM-judge）
3. 失败 case 归类（§六）—— 你的 commit log 应该开始出现 "address
   failure mode F3"
4. 第一次 retire 一个 dataset case（连续 3 月 100% pass）
5. 写一篇 eval 复盘（learnings/eval-quarter-1.md），讲：
   - 这季度 prompt 改了几次
   - eval 数据驱动了几次决策
   - 哪几次 eval 涨了但 dogfood 没涨
   - 哪一类 failure mode 最难解

**自测**：你能不能讲清楚 "我这季度的 eval 帮我避免了哪些 bad
prompt change，又有几次它误导了我"？讲不清楚 → 回去想 §七 meta-eval。

### Milestone 4：Month 6 —— 跨越业余 / 专家分水岭

**目标**：能从 first principles 给陌生人解释你的整套 eval lab，并对
**所有重要决策** 给出 trade-off 而非 dogma

**任务清单**：
1. 给一个不懂的人（写一篇博客 / 录一个内部分享）讲清楚：
   - 你为什么没用 DeepEval（trade-off 而非品味）
   - 你的 scorer 矩阵和判断
   - 你的 calibration loop 怎么 work
   - 你这 6 个月里学到的 3 个 counter-intuitive 教训
2. 给另一个项目（比如 OpenHarness Phase 18 的 pipeline eval / 或别的
   personal project）设计 eval 大纲，体感 = 1 小时能出 spec
3. 能从面试者眼里看出 "他对 eval 的认识是 1 周还是 6 个月"
4. （可选）尝试 contribute 一个 Inspect AI 的 eval（拿你的 OpenHarness
   pattern 翻译过去）

**自测**：如果有人问 "什么时候应该不做 eval、直接 ship prompt 改
动？"，你能给出至少 3 个具体场景而不是空喊 "总应该 eval"。

这一题答得出 = 你已经 internalize 了 eval 的 cost / benefit framework，
不再被 "eval 是好东西所以应该全上" 的盲目崇拜支配。这是业余和专家
真正的分水岭。

---

## 九、最重要的几条记住

我浓缩 mentor 心得，这是 6 个月里我希望你反复回到的几句话：

1. **Eval 不是测代码，是测产品判断。** 它的 failure 含义不是 "代码
   错了"，是 "我以为对的产品判断可能不对"。
2. **Dataset 是 designed to fail。** Pass rate 100% 意味着 dataset 升级，
   不是产品 strong。
3. **小而 representative > 大而 redundant。** 50 个人审 case > 5000
   个 scraped case。frontier-LM 团队 prompt eval 也很少超 500。
4. **Scorer 用乘法不用加法。** Structural × Semantic；任一不及格
   case 整体不及格。
5. **VCR replay 是默认，LIVE 是仪式。** 改 prompt 才 LIVE 录新
   cassette，平时 CI 全 replay。
6. **Judge agreement 月度追踪。** 跌破 75% 立刻停止信任 eval，校准
   judge prompt。
7. **Multi-dim score，永远不 collapse 成 single number。** 总分会
   藏信息。
8. **失败 case 归类，不针对单点改 prompt。** 12 个 fail 案例可能只
   是 3 种 mode。
9. **改 dataset 拉 score 是作弊。** Fail case mark 但不删。
10. **Eval 不是裁判，是协作者。** 跟 dogfood 体感对话，互相校准。

---

## 十、一句话总结

如果让我用一句话浓缩这 1500 行：

> **产品有 eval ≠ 团队有 eval thinking。前者是工具，后者是 craft。
> OpenHarness 是你练 eval thinking 的 sandbox，不是练 "装 DeepEval"
> 的 sandbox。一年后你应该忘掉具体框架名字，记住的是 dataset 怎么
> 造、scorer 怎么分维度、judge 怎么校准、eval 跟产品判断怎么互相
> 校准。** 那时候你就是 eval 专家了。

---

## 配套读物（next 你可以做的事）

- 想动手做 Phase 16 eval substrate：先读 [docs/ideas/eval-first-principles.md](./eval-first-principles.md)
  里的方案 3 + §八 ratification gate，那是给当下 OpenHarness 写的最小
  spec
- 想看 LLM-as-judge 业内的 bias 数据：[FutureAGI 2026 综述](https://futureagi.com/blog/llm-as-judge-best-practices-2026)
- 想看 cassette mode 的细节工程：[Anay Nayak 的 LLM VCR 文章](https://anaynayak.medium.com/eliminating-flaky-tests-using-vcr-tests-for-llms-a3feabf90bc5)
- 想看专家组织怎么做 agent eval：[Inspect AI 的 inspect_evals 仓库](https://github.com/UKGovernmentBEIS/inspect_evals)（200+ pre-built eval，几乎全部 Task/Solver/Scorer 三件套结构）
- 想看 eval 跟产品迭代的 calibration loop 真实案例：[Hamel Husain 的 Inspect AI blog](https://hamel.dev/notes/llm/evals/inspect.html)

读完这篇 + 上面 5 个链接，你在 eval 领域已经超过 80% 自称 "我们用 LLM-judge 做 eval" 的工程师。剩下的 20% 差距，靠 OpenHarness 这 6 个月真的把 hand dirty 一遍补上。
