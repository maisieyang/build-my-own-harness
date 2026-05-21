# PLAYBOOK-PM — Harness 产品经理的 23 天实战手册

> **For PM**:这本 playbook 不教你怎么写代码,教你怎么**做产品决策**、**定义指标**、
> **跟研究员协作**、**避免常见陷阱**。基于 23 天独立交付的生产级 LLM harness
> ([OpenHarness from scratch](./README.md))实战经验,所有论断带可验证数据。
>
> 跟 [`PLAYBOOK.md`](./PLAYBOOK.md)(engineer 视角)互补,不重复。

---

## 目录

- **[Part 0](#part-0--为什么-pm-该读这个)** 为什么 PM 该读这个
- **[Part I](#part-i--harness-是什么pm-视角)** Harness 是什么(PM 视角)
- **[Part II](#part-ii--产品决策框架)** 产品决策框架
- **[Part III](#part-iii--pm-视角的-llmagent-机制速通)** PM 视角的 LLM/Agent 机制速通
- **[Part IV](#part-iv--指标--灰度--ab-方法论)** 指标 + 灰度 + A/B 方法论
- **[Part V](#part-v--跨角色协作)** 跨角色协作
- **[Part VI](#part-vi--demand-side-真实困难)** Demand-Side 真实困难
- **[Part VII](#part-vii--pm-启动-day-1)** PM 启动 Day 1
- **[Part VIII](#part-viii--反面案例pm-常犯的-5-个错)** 反面案例(PM 常犯的 5 个错)
- **[Appendix](#appendix)** JD 技术词对照 + 推荐资料

---

## Part 0 — 为什么 PM 该读这个

### 0.1 LLM 时代的 PM 困境

2024 年开始,LLM 产品 PM 面临三个全新挑战:

1. **不确定性**:LLM 行为不像传统 API 那样确定。同一个 prompt 调用 10 次有 8 种结果。怎么做产品规划?
2. **能力快速演进**:模型每 3-6 个月跳一代,昨天还做不到的事今天能做,昨天的最佳方案明天可能完全过时
3. **技术词爆炸**:LLM API / KV Cache / Agent Loop / Tool Use / Skills / MCP / Memory / Sub-agent / Multi-Agent / RAG / Workflow ... PM 到底要懂到什么程度?

这本 playbook 给出明确的回答:**PM 要懂到能跟工程师吵架的程度**——不需要自己写代码,但要能 spot 工程师方案的 trade-off,知道什么时候 push back,什么时候 trust 他们的判断。

### 0.2 这本 playbook 的承诺

每一条产品决策都用 **OpenHarness 17 phase 项目**实证:

- 23 天交付期(从 0 到产品级)
- 17 个 capability phase
- 18 个 subsystem
- 1274 个测试 + 95.33% 覆盖率(CI 验证)
- 24 个 decision document
- 31 个 phase retrospective

不是空泛主张,有数据有 commit hash 可查。

### 0.3 DeepSeek JD 技术词清单覆盖

JD 任职要求第 4 条列了这些技术:

> 理解 LLM 以及 Agent 基本机制及其技术原理,包括 LLM API、KV Cache、Agent Loop、
> Tool Use、Reasoning、Planning、Skills、MCP、Memory、Subagent、Multi-Agent 等
> 相关知识。对 Prompt Engineering、Context Engineering、Harness Engineering 等
> 课题有第一手实践。

这本 playbook **每一项都覆盖**,每项都给出 PM 视角的理解 + 产品决策建议。详见 [Part III](#part-iii--pm-视角的-llmagent-机制速通)。

---

## Part I — Harness 是什么(PM 视角)

### 1. 模型 + Harness = Agent —— DeepSeek 公式的产品翻译

DeepSeek Harness 团队的使命:**Model + Harness = Agent**。

这是个工程公式,翻译成产品语言:

| 工程概念 | 产品翻译 |
|---|---|
| **Model**(模型) | 智能 —— "懂得多" + "能推理" |
| **Harness**(框架) | 手 + 眼 + 记忆 + 安全边界 |
| **Agent**(代理) | **用户实际感知到的产品** |

用户不和模型直接打交道。用户和 **Agent** 打交道——而 Agent 90% 的用户感知都来自 Harness:

- **响应速度** = harness 的并发 + 流式渲染
- **能不能干活** = harness 的 tool dispatch + permission gate
- **会不会出错** = harness 的 error handling + retry
- **安全感** = harness 的 sandbox + audit log
- **可定制** = harness 的 hooks + plugin

模型只是 Agent 的**心脏**。Harness 是 **circulatory system + nervous system + immune system**。三个系统都缺一不可。

**PM 决策框架**:做 Agent 产品时,先问"这个用户痛点是模型问题还是 harness 问题"。

- 模型答错了 → 模型问题(找模型团队优化训练数据 + RLHF)
- 模型答对了但 Agent 没干完 → harness 问题(tool dispatch 失败、permission deny、内存不够 ...)
- 模型答对了但用户觉得慢 → harness 问题(流式输出没启用、tool 串行没并行)
- 模型答对了但用户没看到 → 渲染问题(harness 的 stream event 没 map 到 UI)

我的观察:**用户喊"模型不行"的时候,80% 是 harness 不行**。Harness 是 PM 真正能影响的层。

### 2. Harness 解决什么问题(三个客户的不同视角)

Harness 不是单一产品。**对三种客户解决不同的问题**:

#### 2.1 终端用户视角

终端用户(普通开发者用 Cursor / Claude Code / Cowork)关心:

| 用户感知问题 | Harness 提供的答案 |
|---|---|
| "我说的它真听懂了吗" | Tool use + intent classification |
| "它能干到底吗,还是说一半就罢" | Agent loop + 多轮 tool dispatch |
| "它会乱删我文件吗" | Permission system + sandbox |
| "它怎么这么慢" | 流式渲染 + 并发 + KV cache 复用 |
| "我能自定义吗" | Slash command + Skills + Plugin hooks |

**用户买的是 "Agent 真的能干活" 这个体验**。Harness 把模型能力翻译成可干活的产品形态。

#### 2.2 企业客户视角

企业客户(银行 / 电商 / 大公司内部团队)关心:

| 企业感知问题 | Harness 提供的答案 |
|---|---|
| "数据安全吗,会不会泄露" | Sandbox + permission tier + audit log |
| "怎么集成我的内部系统" | MCP + custom plugin |
| "怎么跟我的工作流对接" | Slash command + bundle 模式 |
| "我的合规怎么办" | Hook chain + 行为审计 |
| "崩了我怎么排查" | Structured observability + trace ID |
| "能不能定制 prompt" | System prompt 注入 + skill 库 |

**企业买的是 "可受控的 Agent 落地路径"**。Harness 是 Agent 在企业内站住脚的脚手架。

#### 2.3 模型研究员视角

模型研究员关心:

| 研究员感知问题 | Harness 提供的答案 |
|---|---|
| "我的模型在真实任务上效果怎样" | Harness 的 evaluation + trace 数据 |
| "用户的真实 prompt 长什么样" | Production query 采样 → 训练数据 |
| "模型 vs harness 谁是瓶颈" | 分层 evaluation(只换 model / 只换 harness) |
| "RLHF 该用哪种 reward signal" | Harness 的用户反馈 + tool 调用成功率 |

**研究员买的是 "model + harness 共演化的反馈闭环"**。Harness 是模型团队的 dogfood 实验场。

### 3. Harness 不解决什么(scope 纪律)

PM 最容易犯的错是**让 harness 越界**。Harness 不该解决:

1. **业务逻辑** —— 把 "银行风控逻辑" 写进 harness 是错的,业务逻辑该在客户的 application code 里
2. **模型本身的能力** —— 模型 reasoning 不行不是 harness 能修的(可以缓解,但不该 PM 把它当 KPI)
3. **特定领域的 UI** —— 不同行业有不同 UI 偏好,harness 应该提供 stream event 接口而不是渲染层
4. **数据持久化** —— harness 提供 session memory 是合理的,但企业级数据库不是 harness 的事
5. **多租户隔离** —— harness 提供单进程内的安全边界(sandbox),不提供 SaaS 级多租户隔离(这是 deployment 的事)

**PM 决策框架 ——"是 harness 该做的事吗"判断三问**:

1. **这个功能跨多个客户都需要吗** —— 否 → 客户 application code 里做
2. **去掉这个功能 harness 就不完整吗** —— 否 → 是 plugin/skill,不是 core
3. **做了之后 harness 跟特定垂直行业绑定吗** —— 是 → 别做,会污染通用性

三个 yes 才进 core。否则就是**别人的活**。

我的观察:**Harness PM 必须有强 scope discipline**,这件事比 "想做什么 feature" 重要得多。我在 OpenHarness 里反复用 SPEC.md 的 "Out of Scope" 表格守住:React TUI 不做、Slack/Telegram gateway 不做、Autopilot 不做。**说"不"是 harness PM 的核心技能**。

### 4. PM 的杠杆点在哪

LLM 产品三个层次,PM 能影响哪些:

```
┌──────────────────────────────────────────┐
│ Layer 3: 用户体验层(UI / 交互 / 营销)     │ ← PM 主导
├──────────────────────────────────────────┤
│ Layer 2: Harness 层(架构 / 工具 / 安全)   │ ← PM 主战场
├──────────────────────────────────────────┤
│ Layer 1: 模型层(训练 / RLHF / 量化)       │ ← 研究员主导,PM 协同
└──────────────────────────────────────────┘
```

PM 真正能定**产品方向**的是 Layer 2(Harness)。Layer 1 是研究员主战场(PM 提需求 + 评估效果),Layer 3 是 UI/UX 主战场(PM 协同设计师)。

**Harness PM 的核心杠杆**:
- 定义 **tool catalog**(用户能让 Agent 干什么)
- 定义 **safety boundary**(用户敢让 Agent 干到哪)
- 定义 **integration surface**(用户怎么把 Agent 接进自己的工作流)
- 定义 **observability**(用户怎么知道 Agent 干了啥)
- 定义 **extensibility**(用户怎么扩展 Agent)

**我的产品观**:Harness PM 的产出**不是 feature list**,是**抽象边界**。每个 feature 都是某个抽象边界的具体实现。守好抽象,feature 自然顺。守不好抽象,做一个 feature 引发一片重构。

---

## Part II — 产品决策框架

这部分是 OpenHarness 23 天里**真实做过的产品决策**的拆解。每个决策点附带"决策框架"和"我的推荐"。

### 5. 怎么做 Tier 划分(必做 / 选做 / 不做)

LLM harness 真实写出来要做 36 个模块(参考 OpenHarness upstream HKUDS/OpenHarness)。但 1 个 PM + 1 个工程师 + 3 个月,做不完 36 个。所以**Tier 划分是 PM 第一个产品决策**。

**OpenHarness 的 4 层 Tier 划分**:

| Tier | 性质 | 包含模块 | 决策 |
|---|---|---|---|
| **Tier 0** | Core,不做就不叫 harness | 项目脚手架 / 协议 / API 客户端 / 流式 / 引擎 / 工具系统 / 5 内建 tool / 基础权限 / 配置 / 认证 / CLI / Print 模式 | **必做** |
| **Tier 1** | Production hardening | 重试 / 异常 / Microcompact / 完整权限 / Hooks / 系统提示词 / 测试 / 可观测 / 打包 | **必做** |
| **Tier 2** | Extensibility | MCP / 斜杠命令 / Skills / Plugins / Memory / Sub-agent | **选 2-3 个深做** |
| **Tier 3** | Advanced | Docker sandbox / 后台任务 / LLM 摘要 / 多 Provider | **视时间 1-2 个** |

**out of scope**(明确写出来不做的):React TUI / 多平台聊天网关 / Autopilot / 语音模式 / Vim 模式 / Swarm 4 种后端 / 23 个 Provider。

**PM 决策框架** —— Tier 划分三问:

1. **不做这个会导致 harness 不能用吗** → 是 → Tier 0
2. **做这个能让 harness 从"能跑"变成"production 级"吗** → 是 → Tier 1
3. **这个功能是用户的"我能不能自己加东西"诉求吗** → 是 → Tier 2
4. 其余 → Tier 3 或 out of scope

**我的推荐**:

- 永远先 Tier 0 + Tier 1 全做,再考虑 Tier 2/3
- Tier 0 + Tier 1 加起来是 **MVP 的下限**,不能砍
- Tier 2 选 2-3 个**最能体现产品差异化**的(MCP 行业标准 + Skills 用户定制 + Sub-agent 高级用例)
- Tier 3 选**最能放大学习价值**的 1 个(Sandbox 简历加分,完整 auto-compaction 学 prompt 工程,LLM 摘要学 context 工程)

**陷阱**:Tier 划分一旦定,**6 个月内别改**。改 Tier 是 SPEC 级决策,不是 sprint 级决策。

### 6. Phase 顺序怎么排

Tier 解决"做不做",Phase 解决"先做哪个"。OpenHarness 的 17 phase 顺序:

| # | Phase | 顺序逻辑 |
|---|---|---|
| 1 | Foundation + Hello LLM | 工程基线(无脚手架,后面寸步难行)|
| 2 | Tool Loop(心脏) | Agent 的灵魂,所有后面 phase 的依赖 |
| 3 | Safety + Observability | 在功能多之前先把安全 + 可观测做好 |
| 4 | Compaction | Context 管理,长对话不爆 token |
| 5 | MCP | Tier 2 第一个,行业标准接入 |
| 5c | Skills | Tier 2 第二个,用户定制能力 |
| 5b | Slash 命令 | Tier 2 第三个,UX 抓手 |
| 6 | Sub-agent | Tier 2 第四个,高级用例 |
| 7a/7b/7c | Sandbox | Tier 3,容器化隔离 |
| 5d/5e/5f | Bundle + Plugin Hooks | 后期 emerge 的扩展性 |
| 6+ | REPL | UX 完善 |
| 7 | 收尾(打包 + meta-retro) | Release |

**PM 决策框架** —— Phase 顺序三问:

1. **依赖关系** —— 这个 phase 依赖哪些前置 phase?Tool Loop 依赖 protocols + API client,所以 Phase 2 不能在 Phase 1 之前
2. **风险前置** —— 风险高的 phase 早做(API client 抽象做错了 → 全废)
3. **价值前置** —— 每个 phase 完成都该交付**用户可见**的价值(不要做 6 周地基才出第一个 demo)

**我的推荐**:**用"端到端薄切片"原则排 phase 顺序**。

- Phase 1 = scaffold + hello LLM(用户能看到流式响应,虽然没 tool)
- Phase 2 = + tool loop(用户能让 Agent 干活)
- Phase 3 = + safety(用户敢让 Agent 干危险事)
- 每个 phase 都是一个可演示的产品里程碑

**陷阱**:别按"先做 model abstraction、再做 protocol、再做 retry、最后做 CLI"这种**水平切片**。水平切片会卡 6 周才出第一个 demo,士气 + stakeholder 信心都崩。

### 7. 什么时候选 RAG / Workflow / Agent

LLM 应用的三大模式,PM 必须能 spot 该选哪个:

```
                    不确定性高
                        │
                        │   Deep Agent 全自主代理
                        │   (Test Agent / Manus)
                        │   LLM 自主决策每一步
                        │
  复杂度低 ─────────────┼───────────── 复杂度高
                        │
     扣子/Dify           │   LangGraph 编排
     (简单场景快速方案)   │   (SOP 明确,代码控制流转)
                        │
                    不确定性低
```

**PM 决策框架** —— 业务复杂度 × 不确定性:

| 维度 | 复杂度低 | 复杂度高 |
|---|---|---|
| **不确定性低** | 扣子 / Dify(够用就行)| **LangGraph 流程编排**(企业落地主力,SOP 明确,分支可枚举)|
| **不确定性高** | (基本不存在的场景)| **全自主深度代理**(任务开放,LLM 自主决策)|

**混合场景**:**宏观编排 + 节点内 Agent**(如 Deep Research:LangGraph 编排 5 阶段 SOP,Research 节点内 Supervisor + Subagent 自主决策)。

**我的推荐**:

- **大部分企业场景选 LangGraph 编排** —— SOP 是明确的,复杂度高但不确定性可控,代码控制流转最稳
- **扣子/Dify 是简单场景的快速方案** —— 但进入深水区(企业级 evaluation / 多租户 / 复杂权限)力不从心
- **Deep Agent 是高不确定性的特殊场景** —— 测试生成 / 研究探索 / 复杂代码任务,值得做但不是 mainstream
- **RAG 是 "知识增强" 维度的正交事**,不在三大模式选型范围内(任何模式都可以叠加 RAG)

**陷阱**:**别被 "Agent" 这个词迷惑成"什么都用 Agent"**。Agent 的不确定性是 cost,只有问题的不确定性真高于 cost 时才该用。

### 8. 什么时候做 MCP / Skills / Sub-agent

Tier 2 三个最 popular 的扩展模式,PM 需要判断哪个先做。

#### 8.1 MCP(Model Context Protocol)

**做 MCP 的产品理由**:
- 接入第三方 tool ecosystem 不重写
- 行业事实标准(Anthropic 推动,各大厂都跟进)
- 简历加分,招聘 signal

**不做 MCP 的产品理由**:
- 你的 tool 全是自己写的,没第三方接入需求
- 用户群体小,生态不重要
- MCP server 的 init 延迟对 UX 重要场景不友好(stdio 启动 ~2s)

**我的推荐**:做。除非你的产品是封闭生态(企业内部 Agent),否则 MCP 是 default。

#### 8.2 Skills(懒加载专家知识)

**做 Skills 的产品理由**:
- 用户群体差异大(react 开发者 / Java 开发者需要不同 prompt 指导)
- Prompt 比 tool 多得多(不是每个 prompt 都要写成 tool)
- LLM 自己决定何时展开,token 友好

**不做 Skills 的产品理由**:
- 你的领域 prompt 全部已知 + 固化在 system prompt 里
- 用户群体单一(没必要做 per-user customization)

**我的推荐**:Tier 2 必做。这是用户**真正深度定制**的入口。OpenHarness 的 Skills 章节只用了 2-3 天 + ~170 行代码,ROI 极高。

#### 8.3 Sub-agent(递归 tool dispatch)

**做 Sub-agent 的产品理由**:
- 长任务需要 context isolation(避免父 agent context 爆炸)
- 想做 supervisor / planner / executor 三角架构
- 需要 parallel exploration(多 agent 并发探索不同方向)

**不做 Sub-agent 的产品理由**:
- 任务都是单 turn 或短 turn 链,不需要递归
- 模型 context window 足够大(Claude 200K / Gemini 2M),不需要 offload

**我的推荐**:Tier 2 第三优先级。除非你有明确的多 agent 用例(比如 deep research / 测试生成),否则可以延后。但**设计 BaseTool 时就该想到 sub-agent 是 tool 的递归调用**——架构上预留,实现可以晚。

### 9. 什么时候做 Sandbox / Memory / Multi-agent

Tier 3 高级特性,PM 决策时多数答案是"等真有 use case 再做"。

#### 9.1 Sandbox(Docker / gVisor)

**触发条件**:
- 用户群体跑 untrusted code(企业客户尤其)
- 合规审计要求 process isolation
- Open source product 怕被滥用

**不做的代价**:用户在 host 上跑 LLM 生成的代码,有删除 ~/ 或上传 ~/.ssh 的风险。

**我的推荐**:对 enterprise 客户**必做**(P0 需求),对个人开发者**可延后**。OpenHarness Phase 7 做了 Docker + gVisor 双 substrate,接口抽象一次设计(Phase 7a),Docker 实现一次(Phase 7b ~ 1-2 天),gVisor 实现一次(Phase 7c ~半天)。**抽象做对了,第二个 substrate 是第一个的 12% 代码量**。

#### 9.2 Memory(跨 session 状态)

**触发条件**:
- 用户希望 Agent "记得我" —— 偏好 / 历史项目 / 个性化
- 任务需要长期 context(多 session 跟进)

**陷阱**:Memory 容易被滥用,变成 "把所有信息都塞 memory" → context 爆炸 → 反而不准。**Memory 必须有 forget 机制**,不能只 append。

**我的推荐**:**先做 in-conversation memory**(单 session 内 working memory),**再考虑跨 session memory**(用户级别 long-term)。跨 session memory 的设计极其考验产品判断,容易翻车。OpenHarness 没做这块(Phase 7+ defer),因为单人项目没真用户群体证实必要。

#### 9.3 Multi-agent(真正多 agent 协同)

**触发条件**:
- 任务天然分解为多个角色(producer / consumer / supervisor)
- 不同 agent 需要不同 model / prompt / tool 集
- 并发能加速(parallel research / parallel testing)

**陷阱**:**Multi-agent 极容易被泛用**。10 个场景里 9 个用 sub-agent(单一类型递归)就够,真需要 multi-agent(多种角色协同)的场景很少。

**我的推荐**:**默认 sub-agent,multi-agent 留到真有数据证明 sub-agent 不够时再做**。Multi-agent 的 orchestration / message passing / context sharing 是巨大的工程坑,做错了所有 agent 一起崩。

### 10. 怎么判断 Out-of-Scope —— "不做"的纪律

PM 写"不做什么"比写"做什么"难。OpenHarness 的 SPEC.md "Out of Scope" 表格:

| 模块 | 不做的原因 |
|---|---|
| React TUI(Ink) | 跨语言架构超出"精通 Python"目标 |
| ohmo 独立 app | 应用层产品,不是 harness 抽象 |
| 多平台聊天网关(Slack/Telegram/Discord/Feishu) | 应用集成,非 harness 抽象 |
| Autopilot + Dashboard | 上层 workflow 产品 |
| 语音模式 / Vim 模式 | UX 边角 |
| Swarm 4 种后端 | 子 Agent 选 in_process 一种即可 |
| 23 Provider | 选 2-3 种即可 |
| Personalization / Themes | 应用层定制 |

**每个"不做"都给出**:
1. 为什么这个 feature 在 upstream 出现
2. 我不做的具体理由
3. 如果将来要做,触发条件是什么

**PM 决策框架** —— "不做" 三问:

1. **这是 application 还是 framework 的事** → application 的就推给用户
2. **这是 ergonomics 还是 capability 的事** → 纯 ergonomics 的延后(语音 / Vim / 主题)
3. **这是 vertical 还是 horizontal 的事** → vertical 行业相关推给用户,framework 守 horizontal

**我的产品观**:**Out of Scope 不是消极的"砍 feature",是积极的"守抽象"**。Out of Scope 列表是 SPEC 第一公民,跟"做什么"同等重要。

### 11. Buy vs Build —— SDK 还是自己造

每个子系统都面临 buy vs build 选择。OpenHarness 的真实选择:

| 子系统 | 决策 | 理由 |
|---|---|---|
| **Pydantic v2**(data model) | Buy | 自己写 typing + validation 是上万行代码,Pydantic 解决得比我好 |
| **Typer**(CLI) | Buy | 自己写 argparse 抽象不如 Typer 的 type hints + auto help |
| **structlog**(logging) | Buy | 自己写 structured logging 没必要 |
| **tiktoken**(tokenizer) | Buy | 跟 OpenAI 模型一致是契约,自己估算永远不准 |
| **mcp Python SDK**(MCP transport) | Buy | JSON-RPC 2.0 + stdio framing 是 800-1500 行重写,SDK 200 行集成够 |
| **aiodocker**(Docker client) | Buy | Docker API 不该自己 wrap |
| **httpx + openai SDK**(API client) | Buy | OpenAI 兼容协议自己写是 1000+ 行 |
| **Agent loop** | Build | Harness 的心脏,这部分必须自己造 |
| **Tool dispatch + permission + hook** | Build | 这是 harness 的差异化,自己造 |
| **Sandbox lifecycle** | Build(基于 aiodocker) | 协议自己,docker 操作借 SDK |

**PM 决策框架** —— Buy vs Build 三问:

1. **这部分是 harness 的差异化吗** —— 是 → Build
2. **行业有事实标准 SDK 吗** —— 有 → Buy(除非有特殊理由)
3. **自己造的成本是否远小于带来的价值** —— 否 → Buy

**我的产品观**:

- **Build 的部分要精**——只造你能造得比 SDK 更好的东西
- **Buy 的部分要厚**——能 buy 就别 build,你的时间用在差异化的地方
- **集成层永远自己造**——SDK 跟 SDK 之间的胶水,以及 SDK 跟你的抽象之间的胶水,是核心 IP

OpenHarness 是 ~10,800 行生产代码,其中 ~80% 是**集成 + 抽象**,~20% 是真正的算法/协议。如果不 Buy,代码量会是 5 万行,3 个月做不完。

---

## Part III — PM 视角的 LLM/Agent 机制速通

这部分覆盖 DeepSeek JD 明确列出的所有技术词,每节给:**技术原理(简版) + 产品含义 + PM 决策框架 + 我的推荐**。

PM 不需要懂到能实现这些技术,需要懂到**能跟工程师讨论 trade-off**。

### 12. LLM API / KV Cache —— 成本和延迟的来源

#### 技术原理(简版)

LLM 推理时,每个 token 的生成依赖前面所有 token 的 attention 计算。**KV Cache** 是把已计算的 key/value tensor 缓存起来,后续 token 只对新 token 做计算,前面的复用。结果:首 token 慢(prefill),后续 token 快(decode)。

#### 产品含义

KV Cache 决定 **3 个产品指标**:

1. **TTFT(Time To First Token)**:用户从发送到看到第一个字的时间。受 prompt 长度 + KV cache 是否命中影响
2. **TPS(Tokens Per Second)**:首 token 后的吞吐率。受模型本身 + 硬件影响
3. **Cost per query**:大部分 API 按 token 计费,prompt token 比 completion token 便宜(因为 prompt 可以 prefix cache)

**KV cache 命中**(同样的 prefix prompt 重复使用)能让成本和延迟同时下降。这就是为什么 system prompt 设计要**前面稳定后面变化**——稳定部分被 cache,变化部分才 recompute。

#### PM 决策框架

1. **TTFT > 2 秒** → 用户会感到 lag → 优化 prompt 长度 / 用更小模型 / 启用 KV cache
2. **TPS < 30** → 流式输出体验差 → 换模型 / 换 provider
3. **Cost per query 不合理** → 看 prompt 是不是太长 / system prompt 能不能 prefix cache

#### 我的推荐

- **永远把 system prompt 写成 prefix-stable 形态**(稳定部分在前,环境变量 / 用户输入在后)
- **不要在 system prompt 里嵌入随时间变化的内容**(比如 `current_time = ...`),会破坏 prefix cache
- **流式输出必须默认开**(`stream=True`),否则 TTFT 是整个 completion 时长

### 13. Agent Loop —— Harness 的心脏

#### 技术原理(简版)

```
while True:
    response = llm.stream(messages)
    if response.has_tool_use:
        for tool_use in response.tool_uses:
            check_permission(tool_use)
            result = dispatch_tool(tool_use)
            messages.append(result)
    if response.stop_reason == "end_turn":
        break
```

LLM 输出 `tool_use` block,harness 调度执行,把结果 append 回 messages,继续循环,直到 LLM 说"我做完了"(`stop_reason="end_turn"`)。

#### 产品含义

**Agent Loop 决定了用户能让 Agent 干多少事**。

- 没 agent loop = 单轮 Q&A(像 ChatGPT 早期)
- 有 agent loop = Agent 能连续干活直到完成

**关键产品决策**:

- **`max_turns` 上限**:Agent 最多循环多少轮?太低(5)Agent 干不完事;太高(100)成本爆炸或陷入死循环。OpenHarness 默认 20。
- **Stop reason 处理**:`end_turn` / `max_tokens` / `tool_use` / `stop_sequence` 各对应什么 UX?
- **错误处理**:tool 失败 → 给 LLM 看错误 + 让它重试,还是直接 abort?OpenHarness 选 "errors-as-payload"(给 LLM 看,让它适配)。

#### PM 决策框架

| 场景 | max_turns 推荐 |
|---|---|
| 简单 Q&A(stack overflow 风格) | 3-5 |
| 编辑 / 重构小文件 | 10 |
| 探索任务(grep 找东西) | 20 |
| Deep research / 测试生成 | 50-100 |

**永远要有上限**——无限循环是 bug,不是 feature。

#### 我的推荐

- **Default `max_turns = 20`** —— 覆盖 80% 编程任务
- **Per-tool `--max-turns` override** —— 给重型任务空间
- **Loop 限制触发时,给用户人话错误信息**(不是 stack trace)
- **`tool_use` stop reason 必须循环,其他都 break** —— 这是 OpenHarness Phase 2 boundary doc D6.1 的核心决策

### 14. Tool Use —— LLM 的 syscall interface

#### 技术原理(简版)

LLM 输出结构化的 `tool_use` block:`{name, input, id}`。Harness 解析,执行注册的工具,返回 `tool_result` block:`{tool_use_id, content, is_error}`。

#### 产品含义

**Tool 是 LLM 跨越"文本生成"进入"实际干活"的桥梁**。

不止于"调用函数"。Tool 是 LLM 的 **syscall interface**:
- Read/Write/Bash/Grep 是 OS-level 操作
- MCP 是 federated tool(远程服务)
- Skill 是 lazy-loaded knowledge(知识库)
- Sub-agent 是 task delegation(子任务)
- 都是同一个 Tool 抽象的不同实例

**关键产品决策**:

- **Tool catalog 大小**:5 个? 20 个? 100 个? 太少不够用,太多 LLM 选不准
- **Tool naming convention**:PascalCase 还是 snake_case?跟竞品(Claude Code / Cursor)对齐吗?
- **Tool 输入验证**:Pydantic schema 强制?还是 free-form JSON?
- **Tool 错误反馈**:errors-as-payload(给 LLM)还是 raise-as-exception(打断)?

#### PM 决策框架

1. **Tool 数量** —— 实测发现,LLM 在 < 30 个 tool 时选择准确率高,超过 50 个开始 confuse → **限制 catalog 到 30 个核心 + 通过 namespacing 扩展**
2. **Tool 描述** —— description 越具体 LLM 选越准。**"Use this when X" 比 "Does X" 描述好**
3. **Tool 调用错误** —— **永远走 errors-as-payload**,让 LLM 看到错误并适配(LLM 修错误的能力比硬中断 UX 好)

#### 我的推荐

- **Tool dispatch 必须是同步串行**(Phase 2 D6.3),除非你有强证据需要并发(并发 tool 的错误处理 / 资源争用 / observability 都是大坑)
- **Tool naming 直接抄 Claude Code 习惯**(Read / Write / Edit / Bash / Grep)——用户跨产品迁移友好
- **Tool input schema 必须 Pydantic strict**(`extra="forbid"`)——LLM hallucinate 多余字段时立刻 fail,而不是 silently corrupt

### 15. Reasoning / Planning —— Prompt Engineering 的产品落点

#### 技术原理(简版)

- **Reasoning**:LLM 在生成最终答案前显式 "think step by step"。GPT o1 / Claude 3.5 with extended thinking 都是这个范式。
- **Planning**:Agent 在 execute 前先 plan,把任务拆分成 sub-step。可以是 LLM 自己 plan(prompt-based),也可以是 framework 强制 plan(state-machine-based)。

#### 产品含义

Reasoning / Planning 是 **prompt engineering 的高级形态**:

- **Reasoning** 提高 LLM 在复杂任务的准确率,代价是 latency + cost(extended thinking 比普通 completion 慢 3-10x)
- **Planning** 提高任务完成度,但 over-planning 浪费 token(LLM 写了 5 步 plan 但实际只需要 2 步)

**关键产品决策**:

- **要不要默认开 Reasoning** —— 简单任务 reasoning 是浪费,复杂任务 reasoning 是救命
- **Plan-then-execute vs Execute-while-thinking** —— Codex 选了 plan mode(开发时显式 plan),Manus 选了 file-system-as-memory(plan 写到文件,边执行边改)
- **要不要让用户看到 reasoning** —— 看到能让用户信任 Agent,看不到能更简洁

#### PM 决策框架

1. **任务复杂度高 + latency 不敏感** → 开 reasoning(代码生成 / deep research)
2. **任务复杂度高 + latency 敏感** → 开 reasoning 但折叠显示(用户不感知 latency 来源)
3. **任务简单** → 不要 reasoning(浪费成本)
4. **Plan mode 适合**:任务步骤可枚举(SOP-driven)
5. **Edit-while-thinking 适合**:任务高度未知(deep research)

#### 我的推荐

- **Reasoning 应该是用户可选,不是默认**(产品提供 `/think` 命令显式开启)
- **Plan mode 应该是显式特性**,不要藏在 prompt 里偷偷做(用户不知道 plan 存在 → 看不懂 Agent 行为 → 失去信任)
- **Planning vs Reasoning 的产品边界**:Planning 在 framework 层(Agent 决定干啥),Reasoning 在 LLM 层(LLM 决定怎么干)。**别混淆。**

### 16. Skills —— Context 工程的懒加载范式

#### 技术原理(简版)

Skills 是 markdown 文件 + YAML frontmatter:

```markdown
---
name: react-testing-patterns
description: When to write React tests; what patterns to use
---

When writing tests for React components...
```

Harness 把所有 skill 的 `name` + `description` 注入 system prompt 让 LLM 看到 catalog。LLM 决定何时需要哪个 skill,通过 `LoadSkill(name="react-testing-patterns")` tool 拉取 body。**Body 是 lazy-loaded,不污染 context**。

#### 产品含义

**Skills 是 prompt engineering 的可扩展形态**:

- 不再把所有专家知识塞 system prompt(token 爆炸)
- 用户/团队可以自己写 skill(YAML 简单)
- LLM 自己决定何时展开(智能 routing)

**关键产品决策**:

- **Skill 粒度** —— 1 个 skill 写 1 个主题(small)还是包含多个相关主题(large)?
- **Skill 共享机制** —— `~/.openharness/skills/`(用户私人)还是 git repo 共享(团队)?
- **Skill 评估** —— 怎么知道 skill 有用?加载次数?加载后任务完成度提升?

#### PM 决策框架

1. **Skill < 500 字** → 直接放 system prompt(便宜)
2. **Skill 500 字 - 2000 字** → Skill 形态(catalog + LazyLoad)
3. **Skill > 2000 字** → 拆成多个 small skills

**Skill 命名 + description 是产品关键** —— LLM 用 description 决定是否 load,description 写不好 → skill 永远不被用。

#### 我的推荐

- **Skill description 应该是 "When to use"**,不是 "What it is"
- **Skill body 应该有结构**(headings / lists),不是一坨 prose
- **Skill 库应该是 git-versioned**,跟代码同源管理
- **测量 skill 价值的指标**:Load rate(用户多少 task 加载了这个 skill)+ task success rate after load(加载后任务完成度)

### 17. MCP —— 行业标准 vs 自研协议

#### 技术原理(简版)

MCP(Model Context Protocol)= JSON-RPC 2.0 over stdio。Anthropic 推动的工业标准,允许 LLM client(Claude Code / Cursor / Cowork)接入第三方 tool server。

**工作流程**:
1. Client(harness)spawn subprocess(MCP server,通常 npx 或 python -m)
2. JSON-RPC `initialize` handshake
3. Client 调 `tools/list` 拿 catalog
4. LLM 决定调用 → Client 调 `tools/call` → Server 执行 → 返回 result

#### 产品含义

**MCP 不是技术,是生态**:

- 接入 MCP = 接入了整个第三方 tool 生态(filesystem / github / sql / etc.)
- 用户写一个 MCP server,任何 MCP-compatible client 都能用
- **网络效应** —— 越多 client 支持 MCP,越多 server 写 MCP,反过来推动更多 client 支持

**关键产品决策**:

- **支持 MCP 还是自研协议** —— 自研有 control,MCP 有生态
- **支持哪些 MCP transport** —— stdio(最 common)/ HTTP / WebSocket
- **怎么处理不可信的 MCP server** —— `readOnlyHint` 自报?用户白名单?

#### PM 决策框架

1. **你的产品定位是封闭生态吗** —— 是(企业内部 Agent)→ 可以不做 MCP / 仅做 MCP client 不做 MCP server
2. **你的用户群体跨多个工具吗** —— 是 → MCP 是 default(用户跨工具迁移 tool 不重写)
3. **你需要快速接入第三方 tool 吗** —— 是 → MCP 而不是自研

#### 我的推荐

- **几乎所有 LLM agent 产品都该支持 MCP** —— 这是 2026 年的行业事实标准
- **MCP server 的 `readOnlyHint` 不能信** —— 必须有用户白名单机制(OpenHarness Phase 5 D15.6 的核心安全决策)
- **MCP 不是替代 native tool,是补充** —— 5 个 built-in tool(Read/Write/Edit/Bash/Grep)永远是 framework baseline,MCP 是 federated extension

### 18. Memory —— state 是产品的延伸

#### 技术原理(简版)

LLM 本身无状态。Memory 是 harness 提供的跨 turn / 跨 session 持久化:

- **Working memory**(单 session)= `messages[]` 累积 + 智能截断
- **Session memory**(跨 session)= 显式存储 + 在新 session 注入
- **Long-term memory**(无限期)= YAML / database + retrieval

**OpenHarness Phase 4 做了 working memory**(Microcompact 截断 + 重试);**session/long-term defer**。

#### 产品含义

**Memory 是 Agent 个性化的入口**:

- 没 memory = 每次对话从零开始
- 有 working memory = 单 session 内连贯
- 有 session memory = "记得我"
- 有 long-term memory = "了解我"

**关键产品决策**:

- **Memory granularity** —— 记每条对话?提取关键事实?用户主动 mark?
- **Forget 机制** —— Memory 必须能 forget,否则 context 永远累积
- **隐私边界** —— 用户的 memory 是不是隔离?跨用户能共享吗?

#### PM 决策框架

1. **你的用户 task 是 atomic 的吗**(一次性任务 → 不需 memory)
2. **你的用户多次回来吗**(回头率高 → session memory 有价值)
3. **你的用户个性化诉求强吗**(企业内部偏好 → long-term memory)

#### 我的推荐

- **先做 working memory** —— 单 session 内累积 + 智能截断,所有产品都该做
- **session memory 加 forget UI** —— 不是 "save everything",是 "save what user marks"
- **long-term memory 是产品高级形态,不是 default** —— 隐私 + 准确性都难,有强证据再做
- **OpenHarness 的 Microcompact 是 working memory 的最简实现** —— 截断旧 tool result,保留对话核心。够用 80% 场景

### 19. Sub-agent —— 递归 dispatch 的产品场景

#### 技术原理(简版)

Sub-agent = 一个 `BaseTool` 实例,`execute()` 内部启动新的 agent loop(`run_query()` 递归调用)。Sub-agent 有独立的:
- system prompt(可定制 role)
- max_turns(独立预算)
- messages(context isolation)

但继承父 agent 的:
- API client
- Tool registry
- Permission checker
- Hook chain

OpenHarness Phase 6 用 `SpawnAgent` 实现,带 `max_agent_depth` 防止 fork bomb。

#### 产品含义

**Sub-agent 是 "task delegation" 的产品形态**:

- 长任务可以 spawn sub-agent 干 sub-task,父 agent 等结果
- 不同 sub-agent 可以有不同 role(researcher / coder / reviewer)
- Context isolation —— sub-agent 探索的废弃 path 不污染父 context

#### PM 决策框架

| 场景 | 用 Sub-agent? |
|---|---|
| Deep research(多角度搜集信息) | ✅ 强用 |
| Code review(读 + 评论分开)| ✅ 用 |
| 简单 Q&A | ❌ 不用 |
| 多 turn 编程(同一上下文)| ❌ 不用 |
| 并发探索多个方向 | ✅ 用,但要做并发控制 |

#### 我的推荐

- **Sub-agent 是 advanced feature**,不是 default
- **`max_agent_depth = 3` 是 sensible default** —— 大部分场景不超过 3 层嵌套
- **Sub-agent 的 result 应该是 final assistant text**,不是完整 message history(否则父 context 爆炸)
- **永远要有 fork-bomb 防护**(深度上限 + 总数上限)

### 20. Multi-Agent —— 什么时候真需要

#### 技术原理(简版)

**Multi-agent** ≠ Sub-agent。

- **Sub-agent**:同一种 agent 递归调用(任务分解)
- **Multi-agent**:多种不同 agent 协同(角色协作)

Multi-agent 需要:
- Message passing(agent 之间怎么沟通)
- Shared state(共享哪些上下文)
- Orchestration(谁决定 agent 间顺序)

#### 产品含义

**Multi-agent 容易被过度推销**:

- "组队 AI 工程师"听起来酷
- 实际 90% 场景 sub-agent 就够
- Multi-agent 的 message passing 是工程巨坑(失败模式多 + 调试难)

#### PM 决策框架

只有**所有这些条件同时满足**才该做 multi-agent:

1. 任务天然有多个**不同性质**的角色(不是 just "更多 agent")
2. 不同 agent 需要**不同 model / prompt / tool**(否则就是 sub-agent)
3. Agent 间有**明确的协议**(谁问谁答 / 谁先谁后)
4. 你有**足够工程资源**做 orchestration 测试

#### 我的推荐

- **默认 sub-agent 而不是 multi-agent**
- **真要做 multi-agent**,先做 supervisor + workers 模式(最简单,1 个 supervisor 协调多个 worker)
- **别一上来做 swarm**(去中心化 multi-agent),工程上几乎不可控
- **LangGraph + sub-agent 组合**(LangGraph 编排 + 每个节点是 sub-agent)往往比真 multi-agent 简单

### 21. Prompt / Context / Harness Engineering 三层

JD 提了三个 "engineering":Prompt Engineering / Context Engineering / Harness Engineering。它们的分层:

| 层 | 关注 | 产品体现 |
|---|---|---|
| **Prompt Engineering** | LLM 单次输入怎么写让效果最好 | System prompt 设计 / few-shot 例子 |
| **Context Engineering** | 怎么管理整段对话的 token + state | Memory / compaction / RAG 注入 / skill loading |
| **Harness Engineering** | 怎么让 Agent 真能干活 | Tool dispatch / permission / observability / agent loop |

**三层关系**:

```
Harness Engineering(底层基础设施)
   ↓ 提供
Context Engineering(token + state 管理)
   ↓ 提供
Prompt Engineering(LLM 单次输入)
```

#### PM 决策框架

PM 在三层都该有 first-hand experience:

- **Prompt Engineering**:每周亲手写 / 改 / 测 ≥ 5 个 prompt(不是看 PRD 听工程师汇报)
- **Context Engineering**:能 read 一份完整 trace(messages + tool calls + responses),spot 哪条 context 是浪费 / 哪条是关键
- **Harness Engineering**:不要求亲自实现,但要能跟工程师讨论 trade-off

#### 我的推荐

- **PM 必须亲手 dogfood**(JD 加分项第 5 条)—— PM 自己不用产品,产品没救
- **Prompt 改一次就 evaluation 一次** —— "随便改改" prompt 是 production 灾难的 root cause
- **Context Engineering 是 LLM 时代的"性能调优"** —— 学一遍能让你跟工程师沟通效率翻倍

---

## Part IV — 指标 + 灰度 + A/B 方法论

这部分对应 JD 任职要求第 2 条:**系统性数据方法 + 统计学严谨**。

### 22. 衡量 Harness 的 5 个层次

Harness 是技术产品,有 5 个层次的指标。PM 必须 5 层都关心,但**优先级不同**。

#### Layer 1: 技术健康度(底线,必须达标)

| 指标 | 目标值 | 不达标后果 |
|---|---|---|
| Test coverage | ≥ 95% | 改动信心崩塌,产品迭代变慢 |
| Mypy strict | 0 error | 类型错误埋雷,production 出隐藏 bug |
| CI pass rate | 100%(去除 flake) | 团队对 main branch 失去信任 |
| P99 latency | 视产品定,但要稳定 | 用户体验差 |

OpenHarness 的现状:**1274 tests / 95.33% coverage / mypy strict / CI green**。

#### Layer 2: 功能正确率

| 指标 | 目标 |
|---|---|
| Tool dispatch success rate | > 99% |
| Permission check accuracy | 100%(0 误放 / 可控误拦) |
| Error recovery rate | LLM 在 tool 失败后能恢复继续的比例 |
| Multi-turn task completion | 完整任务跑到 `end_turn` 的比例 |

#### Layer 3: 用户体验

| 指标 | 目标 |
|---|---|
| TTFT(Time To First Token) | < 1.5s P50 |
| TPS(Tokens Per Second) | > 30 |
| Task success rate | 用户自评 "成功完成" 的比例 |
| Friction events | 用户中途放弃 / 切换工具的次数 |

#### Layer 4: 业务指标

| 指标 | 目标 |
|---|---|
| DAU / MAU | 留存指标 |
| Conversion(免费 → 付费) | 商业模式有效性 |
| NPS | 用户主动推荐意愿 |
| Churn | 流失率 |

#### Layer 5: 长期价值

| 指标 | 目标 |
|---|---|
| Model + harness 共演化数据量 | Production 数据回流训练的量 |
| Plugin ecosystem 增长 | 第三方 MCP server / Skill 数量 |
| 社区影响力 | GitHub stars / Discord 活跃度 / mentions |

**PM 决策框架**:

- **Layer 1 是 "及格线"**,不达标其他都不算
- **Layer 2 是 "及格 → 良好"**,这层是工程 + 产品共同负责
- **Layer 3 是 "良好 → 优秀"**,PM 主导
- **Layer 4 是 "商业有效性"**,跟管理层对齐
- **Layer 5 是 "战略影响力"**,长线投资

**我的推荐**:**每周 review 一次全 5 层**,不要只看你最关心的 1-2 层。Layer 1 出问题 Layer 4 一定崩,但反过来不成立。

### 23. 评测框架:LLM-as-Judge / 黄金集 / 用户反馈

LLM 应用的 evaluation 是新课题。**传统 unit test 不够**(因为不确定性),**需要新方法**。

#### 23.1 LLM-as-Judge

用更强的 LLM(GPT-4 / Claude Opus)给被测 LLM / Agent 打分:

```
Judge prompt: "评估 Agent 的回答质量,从 1-5 分。维度:
1. Correctness(对不对)
2. Completeness(完整不完整)
3. Helpfulness(有用没用)
4. Safety(安全不安全)
评分理由 + 分数"
```

**优点**:可扩展(自动跑 1000 个用例)、可比较(同一 judge 评不同版本)
**缺点**:judge 本身有 bias、贵(judge 比被测 LLM 贵)、绝对分数不可信(只能用相对差)

#### 23.2 黄金集(Golden Dataset)

人工标注的 "理想答案" 集合。

| 特性 | 描述 |
|---|---|
| 规模 | 通常 50-500 个 use case |
| 来源 | 真实用户 query 采样 + 人工筛选 + 标注 |
| 用途 | Regression test(每次发版跑一遍,看是不是退化) |
| 缺点 | 维护成本高,容易过时 |

#### 23.3 用户反馈集成

| 反馈类型 | 实施方式 |
|---|---|
| Implicit(行为日志) | 用户中断 / 重试 / 修改答案的频率 |
| Explicit(主动反馈) | 👍/👎 / 评分 / 评论 |
| Survey(定期调研) | NPS / 满意度 / Top pain points |
| Interview(深度访谈) | 每周 2-3 个用户 30 min 深聊 |

#### 我的推荐

**OpenHarness 这种纯框架项目** (没真用户):
- Layer 1 + Layer 2 完整覆盖(1274 tests / 95.33%)
- Layer 3 部分覆盖(integration tests 模拟流式延迟)
- Layer 4/5 不适用

**真有用户的 Agent 产品**:
- **LLM-as-Judge + Golden Dataset 必做**(自动化 evaluation)
- **每发版前必跑 golden dataset 看 regression**
- **每周看 user feedback 趋势**
- **每月深度访谈 5 个用户**(JD 任职要求第 2 条 "访谈" + "灰度测试")

**判断 evaluation 方法对不对的核心问题**:**evaluation 结果是不是和真实用户体验正相关?** 如果 evaluation 高分但用户骂,evaluation 方法错。

### 24. 灰度策略:谁先放 / 怎么 rollback / Stop criteria

#### 24.1 灰度顺序

**经典灰度漏斗**:

1. **Internal alpha**(内部团队,1-2 周)—— 自己 dogfood
2. **Closed beta**(< 100 内部 + 紧密用户)—— 真实 task 验证
3. **Open beta**(< 10% 用户)—— 大规模 noise 验证
4. **GA**(General Availability,100% 用户)—— 全量

#### 24.2 Rollback 机制

**LLM 产品 rollback 比传统应用复杂**:

- 模型 hot-swap(把 production model 从 v2 切回 v1)—— 需要 API 层支持
- Prompt rollback(回退 system prompt 到上一版)—— 需要 prompt versioning
- Feature flag(关掉特定 feature)—— 比 git revert 快

**关键设计**:**先有 rollback 能力,再发版**。没 rollback 的版本不是 production-ready。

#### 24.3 Stop Criteria

灰度过程中,什么数据让你**停止 rollout** ?事先约定:

| 信号 | Stop threshold |
|---|---|
| Error rate | > 2x baseline |
| Latency P99 | > 1.5x baseline |
| User explicit thumbs-down | > 10% increase |
| Critical bug(任何 P0)| 任何 1 个 |

**PM 决策框架**:

1. **每次 rollout 前先写 stop criteria**(纸面 / Notion / wiki)
2. **rollout 中实时看 dashboard**(Grafana / Datadog)
3. **触发任意 stop criteria → 立刻 rollback,不要赌**

#### 我的推荐

- **灰度漏斗不要跳级** —— 直接从 internal alpha 到 GA 是赌博
- **Internal alpha 至少 1 周** —— PM 自己用够时间发现 P0
- **每个 phase 都有 explicit "go / no-go" review** —— 不是看时间到了就发,看数据
- **Rollback 演练每季度做一次** —— 真实 rollback 时手忙脚乱说明平时不练

### 25. A/B Testing:统计显著性 / 多臂老虎机 / 业务变量

#### 25.1 经典 A/B test

```
H0: A 版和 B 版无差异
H1: A 版和 B 版有差异(且某方向)
样本量: 由 effect size + α + power 决定
持续时间: 至少 1 周(cover weekly cycle)
```

**关键陷阱**:

- **Peeking**:实验中途看数据,看到"显著"就停 → P-hacking → 假阳性
- **HARKing**(Hypothesizing After Results Known):看了结果再编故事 → 过拟合
- **季节性**:周末数据跟工作日不同,只跑 3 天会偏

#### 25.2 多臂老虎机(Multi-Armed Bandit)

A/B test 是 explore-only(50/50 分配),浪费用户在 worse arm 上。MAB 在 explore 同时 exploit:用得越好的 arm 流量越多。

**MAB 适合**:

- 多个变体同时测(5 个 prompt 哪个最好)
- 探索成本高(不想浪费太多用户)
- 反馈快(几分钟内能拿到 metric)

**MAB 不适合**:

- 需要统计严谨(论文 / 监管要求 A/B)
- 反馈慢(比如月度留存)
- 业务变量复杂(MAB 优化单一 metric,实际有多 metric trade-off)

#### 25.3 业务变量

**LLM 产品的 A/B 难点**:**单 metric 优化容易,多 metric 平衡难**。

| 单 metric 优化 | 业务真实 |
|---|---|
| Task success rate ↑ | 但用户 latency 也 ↑ |
| Latency ↓ | 但 model 换小了,accuracy ↓ |
| Cost ↓ | 但用户感知 "AI 变笨了" |

#### PM 决策框架

1. **关键决策用 A/B**(发版前必跑)
2. **多个 prompt 变体用 MAB**(快速迭代)
3. **三个 metric 同时跑 A/B 时,先约定优先级**(success > latency > cost)
4. **单变量 A/B**(只改一件事),否则归因混乱

#### 我的推荐

- **从 Day 1 就建实验框架** —— 不要 Phase 5 才想到 "我们没 A/B 系统"
- **绝不 peeking** —— 用统计工具(scipy.stats / Python evan)算样本量,跑够再看
- **保留至少 10% holdout** —— 全量 rollout 后 holdout 用户继续看 long-term metric
- **MAB 是 short-term 优化器,A/B 是 long-term 决策器** —— 两个都要

### 26. 用户反馈收集的 4 种方式

JD 任职要求第 2 条:**问卷、访谈、A/B 测试、灰度测试**。补全 4 种方式:

| 方式 | 优点 | 缺点 | 频率 |
|---|---|---|---|
| **问卷(Survey)** | 规模大,可量化 | 偏差大(填问卷的人不代表全部用户)| 每季度 1 次 |
| **访谈(Interview)** | 深度,能挖到 root cause | 慢,样本少 | 每周 2-3 个 |
| **A/B 测试** | 因果严谨 | 慢 + 工程成本 | 关键决策必做 |
| **灰度** | 实战环境 | 不可重现 | 每发版必做 |

**最被忽视的是访谈**:绝大多数 LLM 产品 PM 不真做访谈,只看 dashboard。结果是:**指标好看但产品没用**。

#### 访谈的最佳实践

1. **每周 2-3 个 30 分钟访谈**,持续做半年以上
2. **不引导用户**——"你最近用产品干了什么?"而不是"我们新功能怎么样?"
3. **看用户怎么干,不只听用户说什么**——screen-share 比 verbal 描述真实 10 倍
4. **每次访谈写 1 页总结**,3 个月后 review trends

#### 我的推荐

- **访谈是 PM 的护城河**——没多少 PM 真坚持做,坚持做的产出洞察远超问卷 + dashboard
- **JD 提到访谈说明 DeepSeek 看重这个能力**——简历或面试时务必给具体案例
- **dogfooding(JD 加分项)+ 访谈 = PM 的真实用户视角**

---

## Part V — 跨角色协作

LLM 产品 PM 是真正的"中心节点"——前面要懂客户,后面要懂模型,中间要懂工程。这部分讲怎么跟 5 个角色高效协作。

### 27. 跟模型研究员的协作

**核心矛盾**:研究员关心"模型能力上限",PM 关心"用户感知价值"。这两个不一定重合。

**翻译技巧**:把"产品需求"翻译成"训练信号"。

| 产品语言 | 训练信号语言 |
|---|---|
| "Agent 经常乱删文件" | "permission denied 的 prompt-response 对加进 RLHF 负样本" |
| "用户觉得 Agent 太啰嗦" | "回答长度的 reward shaping,过长 → 负 reward" |
| "Agent 经常选错 tool" | "tool selection accuracy 加进 eval,low-accuracy 的 prompt 加 SFT" |
| "Agent 不会用新 MCP server" | "MCP server 的工具描述加进 in-context learning examples" |

#### PM 决策框架

1. **不越位**——别跟研究员争 "怎么训"(那是他们的事)
2. **当好需求方**——清晰表达 "用户在哪里痛"
3. **当好评估方**——给出可量化的 metric,让研究员知道训完后怎么衡量
4. **当好数据方**——production 数据回流是 PM 的核心 leverage

#### 我的推荐

- **每周固定 30 分钟 1:1 跟 model lead** —— 不是项目同步,是建立长期 trust
- **每月 demo 一次用户真实 trace** —— 让研究员看到真实用户 prompt 长什么样
- **不要做 "翻译机"** —— PM 不只是 "把客户话翻译给工程师",PM 是产品决策者,有自己的 stance

### 28. 跟工程师协作 —— capability-level spec

**核心错误**:PM 写 spec 写到 sub-task 级,工程师反感(被微管理),进度反而慢。

**正确做法**(对应 PLAYBOOK.md §2):

✅ **Capability level spec**:
> "P1-T4: `oh ask` streaming 输出 + 人话错误提示 + 集成测试 gated"

❌ **Sub-task level spec**(微管理):
> "4a 实现 Settings → 4b 写 mock → 4c 真 client → 4d 集成测试"

**PM 的责任**(对应 JD "项目管理"):

1. **写好 capability spec + acceptance criteria**
2. **守 review 边界**(GREEN 不是 acceptance,walkthrough 才是)
3. **协调 stakeholder**(研究员 + 工程师 + 客户的时间表对齐)
4. **不下沉到代码细节**(那是工程师的 leverage)

#### 我的推荐

- **PM 读 code 但不写 code**(JD "vibe coding 能力" 是加分项,核心还是产品决策)
- **PM 守住 spec 颗粒度** —— 你写得越细,工程师能创造的空间越小
- **PM 守住 review 时点** —— 工程师做完一个 capability 立刻 review,不要堆 5 个一起 review

### 29. 跟开源社群

DeepSeek JD 第 5 条:维护用户社群,从海量用户获取反馈。

**社群三种形态**:

| 形态 | 特点 | 适合 |
|---|---|---|
| **GitHub Issues** | 异步,有上下文,可追溯 | bug report / feature request / 深度讨论 |
| **微信群 / Discord** | 同步,实时,氛围好 | 快速答疑 / 社群氛围 / 病毒传播 |
| **Discourse(社区论坛)** | 长帖,深度,搜索友好 | 教程 / 最佳实践 / 长期内容沉淀 |

**最佳组合**:**GitHub Issues(core)+ 微信群(reach)+ Discourse(长期内容)**。

#### 维护社群的 PM 时间分配建议

- **每周 5 小时 GitHub Issues**(回复 / 分类 / 引导)
- **每天 30 min 微信群浏览**(不一定回复,但要 listen)
- **每月 1 篇 Discourse 文章**(把 Issue 里反复出现的问题沉淀成 doc)

#### 我的推荐

- **社群运营是慢工夫,不是 sprint 任务** —— 6 个月起步才看到效果
- **PM 必须亲自做一部分** —— 不能全 delegate(社群直觉是产品直觉)
- **优先回复"提出深度问题的用户"** —— 这些用户是早期 advocate,值得投入时间
- **每月 review 社群 trends** —— 写一篇 internal memo,让团队知道用户在喊什么

### 30. 跟内部用户(dogfooding)

JD 第 4 条:**帮助 Harness 产品内部落地**。

**dogfooding 的常见误区**:

| 错误做法 | 后果 |
|---|---|
| 让团队"试用一下,有问题告诉我" | 团队不真用 / 不真反馈 |
| 强制要求每天用 | 用户阳奉阴违 |
| 收集 dashboard 数据但不看 | 数据浪费 |
| 把 dogfooding 当 launch 标准 | dogfood ≠ 产品有市场 |

**正确做法**:

1. **找到 internal champion**(几个真有兴趣的同事)
2. **给他们真实任务**(不是 "you should try this",是 "这个新任务能用我们的工具吗")
3. **每周收集 5 个具体故事**(用户怎么用,卡在哪)
4. **快速 iterate 修反馈**(48 小时内回复 + 修复,让 champion 感到被听见)

#### 我的推荐

- **dogfooding 是必要不充分**——内部能用 ≠ 客户买单,但内部不能用 ≠ 不能上线
- **PM 必须是 dogfooder #1**——你自己天天用,才能 spot bug 而不是等 user report
- **dogfooding feedback 不要 dilute**——5 个深度故事比 50 个浅 feedback 价值高

### 31. 跟管理层

**管理层关心的不是 feature list,是 strategic narrative**:

- **这个产品的 vision 是什么**(1 句话能讲清)
- **我们解决谁的痛点**(具体客户案例,不是"开发者")
- **我们的差异化是什么**(vs Claude Code / Cursor / etc.)
- **我们的时间表 + 资源需求**(quarterly OKR 级)
- **风险 + 缓解**(技术 / 市场 / 组织 3 个维度)

#### Demo 一个 Harness 给管理层

**最大的错** —— 给管理层 demo "Agent 写了一个 hello world"。

管理层看不出这个 demo 跟 ChatGPT 有什么区别。

**正确 demo**:

1. **先 demo Agent 干了一个真有商业价值的事**(完成一个开发任务 / 处理一个客服 case)
2. **然后展示"为什么 ChatGPT / 其他产品做不到这件事"**(差异化讲解)
3. **最后给数据**(用户满意度 / 任务完成率)

#### 我的推荐

- **PM 每月给管理层一次 1 页 update**(不是 deck,是 1-pager)
- **report 用 outcome 而不是 output 计量**——不是 "我们 shipped 10 features",是 "Conversion 从 8% 到 15%"
- **bad news 先于 good news report**——管理层最恨被惊喜

---

## Part VI — Demand-Side 真实困难

PM 容易陷入"做产品"思维,忘了"卖产品"的真实困难。

### 32. 客户买 harness 不是买 LLM —— 卖点不一样

**模型公司的销售点**:

- "我们的 model benchmark 高 X%"
- "我们 context window 长"
- "我们多语言支持好"

**Harness 公司的销售点**:

- "你的工程师不用从 0 写 agent loop"
- "你的安全团队的合规 checkbox 我们都已经做了"
- "你的客户用了之后留存提升 Y%"

#### PM 决策框架

每个 feature 都问:**这是 model 厂的卖点还是 harness 厂的卖点?**

- 是 model 厂的卖点 → 不该是你的核心定位(那是 model 厂的事)
- 是 harness 厂的卖点 → 加重投入

#### 我的推荐

- **永远不在销售 deck 写 "我们的 LLM 多强"** —— 那是 model 厂的话术,你抄不来
- **永远写 "我们如何让你的 LLM 真能解决业务问题"** —— harness 真正的价值
- **客户买你的产品时,model 厂的 logo 是必要不充分** —— harness 才是差异化

### 33. Latency / Cost / Quality 三角

LLM 产品 PM 必须能 quote trade-off:

```
   Latency(快)
        ▲
        │
        │
        │
Cost ◄──┼──► Quality
(便宜)   │   (准)
        │
        │
        ▼
```

**这三个是 真正的 trade-off**——优化任一两个,第三个一定下降。

| 优化方向 | 代价 |
|---|---|
| Latency ↓(快)+ Cost ↓(便宜)| Quality ↓(用小模型)|
| Latency ↓(快)+ Quality ↑(准)| Cost ↑(并发 + caching)|
| Cost ↓(便宜)+ Quality ↑(准)| Latency ↑(用大模型但慢)|

#### PM 决策框架

每个产品场景**显式选定优先级**:

| 场景 | 优先级排序 |
|---|---|
| Interactive coding | Latency > Quality > Cost |
| Batch processing | Cost > Quality > Latency |
| Critical decisions | Quality > Cost > Latency |
| Demo / 用户首体验 | Latency > Quality > Cost |

#### 我的推荐

- **每个产品 segment 都写一行 priority statement** —— 贴在工程师工位上
- **任何 PM 决策都看是否违背 priority** —— 违背了要 explicit conscious(不要 silent trade-off)
- **客户面对 trade-off 时,你必须能 quote 三个方向的具体数字**——不是 "差不多"

### 34. Safety / Compliance / Audit —— 企业客户的 P0

企业客户的 P0 不是 "Agent 多聪明",是:

| 维度 | 企业要求 |
|---|---|
| **Safety** | Agent 不会跑出可控范围(删数据 / 越权 / 调外部 API)|
| **Compliance** | 满足行业法规(金融 / 医疗 / 政府)|
| **Audit** | 所有 Agent 行为可追溯(谁 / 何时 / 做了什么 / 结果)|
| **Privacy** | 数据隔离(用户 A 的 prompt 不会泄露到用户 B)|
| **Reliability** | SLA 保证(uptime / latency P99)|

**关键产品决策**:

- 你的 harness **从 Day 1 就把这些做了**(audit log / sandbox / permission)还是 "后期补"?
- 后期补几乎不可能 —— 安全功能是横切的,后期改要动一切

#### PM 决策框架

1. **Audit log 永远是 Tier 1 必做**(不只是 nice-to-have)
2. **Sandbox 是企业客户的 P0**(个人开发者可延后)
3. **Permission system 是 framework baseline**(三层权限模式)
4. **Privacy 设计要早做**(单进程 isolation / process boundary / encryption at rest)

#### 我的推荐

- **OpenHarness 的 Phase 3(Safety + Observability)放在 Phase 4(Compaction)之前**是有意的——安全 + audit 必须早做
- **企业销售 demo 要包含 audit log 演示**——这是 P0 需求的具体体现
- **永远 say no 给 "为了 demo 关掉 sandbox"**——你今天关一次,明天 production 也关

### 35. 给客户 demo 时 LLM 行为不稳定 —— 怎么管理预期

**最尴尬的情况**:你给客户 demo,LLM 给出傻 X 答案。

**怎么管理预期**:

1. **永远先讲产品价值,再 demo**——不要让客户先看 demo 再听讲解(他们会被 demo 的 noise 干扰)
2. **demo 用确定性强的任务**——RAG over 客户 doc 比"问个 random 问题"稳得多
3. **解释不确定性是 LLM 本质**——"LLM 跟传统软件不同,我们用 evaluation 框架管理质量"
4. **show evaluation 数据**——用 LLM-as-Judge / golden dataset 数据证明长期质量

#### 我的推荐

- **demo 提前练 10 遍**——不要 live demo 没演练过的 flow
- **准备 backup demo**——LLM 答错时立刻切到 backup(predetermined 的好答案)
- **用 evaluation 数据建立信任**——demo 是 anecdote,数据是 evidence

---

## Part VII — PM 启动 Day 1

如果今天你被任命为 DeepSeek Harness PM,你 Day 1 该做什么?

### 36. Stakeholder Map

第一周画出 stakeholder map:

```
                    ┌──────────────────┐
                    │  CEO / VP        │
                    │  (战略对齐)        │
                    └─────────┬────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼────┐  ┌──────▼──────┐  ┌────▼─────┐
    │ Model 研究员  │  │  PM         │  │  设计师  │
    │ (能力供给)    │  │  (你)        │  │  (UI/UX) │
    └─────────┬────┘  └──────┬──────┘  └────┬─────┘
              │              │               │
              └──────┬───────┼───────┬───────┘
                     │       │       │
                  ┌──▼───────▼───────▼──┐
                  │  Harness 工程师团队   │
                  └──────────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼─────┐ ┌──────▼─────┐ ┌─────▼──────┐
       │ 内部用户   │ │ 外部客户   │ │ 开源社群    │
       │ (dogfood)  │ │ (B2B)      │ │ (community)│
       └────────────┘ └────────────┘ └────────────┘
```

**第一周做什么**:

- 跟每个角色 30 分钟 1:1(了解他们的 priority + pain points)
- 写一份 stakeholder map(每个角色的 name / role / what they care about)

### 37. 第一周 todo

```
□ Day 1: 跟 manager 对齐 OKR + 时间预算
□ Day 1-2: 读 SPEC.md / ARCHITECTURE.md / 任何已有 doc(读完整,不要 skim)
□ Day 2-3: 跟每个 stakeholder 1:1(model lead / eng lead / 设计师 / 老板)
□ Day 3-5: dogfood 现有产品(每天用 ≥ 2 小时,记录 friction)
□ Day 4-5: 跟 1-2 个真实客户访谈(30 min 深聊,他们用产品干啥)
□ Day 5: 写一份 "我看到的产品现状" 1-pager 给 manager
```

### 38. 第一个月的 OKR

**不要立**:"做完 X 个 feature"

**要立**:"验证 Y 个产品假设"

| 错误 OKR | 正确 OKR |
|---|---|
| 上线 sub-agent 功能 | 验证 "deep research 是 PM 用户的 top 痛点" 假设 |
| 提升 task success rate 5% | 找到 task failure 的 top-3 root cause |
| 增加 10 个 plugin | 验证 "用户真的会写 plugin" 假设 |

**为什么**:Feature 是手段,假设验证是目的。Feature 没人用是常态(因为你猜错了用户需要什么)。

### 39. 第一个季度的成功定义

3 个月后回头看,什么算成功?

| 指标 | 目标 |
|---|---|
| Stakeholder trust | model lead / eng lead 觉得 "PM 帮我们解决问题"(不是 "PM 给我们添麻烦")|
| Customer insight | 你能讲 ≥ 3 个具体客户 case study(不是 generic "developer")|
| Product clarity | 写出 SPEC v2(or v0.2,如果是新产品)—— 比当初的 SPEC 更准、更收敛 |
| Quick wins | 至少 1 个明显 user-visible improvement(不需要大,但要看见)|
| Data foundation | 建好 evaluation + A/B 框架(后面所有决策的基础设施)|

#### 我的推荐

- **第一季度不追求 "做完很多 feature"** —— 追求 "搞清楚产品方向"
- **第一季度不追求 "提升 X 个 metric"** —— 追求 "建好测 metric 的能力"
- **第一季度的真正交付是 SPEC v2 + roadmap** —— 后续 6-12 个月的执行底盘

---

## Part VIII — 反面案例(PM 常犯的 5 个错)

### 40. 错误 1:只听客户喊,不解码痛点

**症状**:客户说 "我们要 X feature",PM 直接说 "好,这季度做 X"。

**根因**:**亨利福特 "如果我问客户要什么,他们会说要更快的马"**——客户描述的是症状,不是 root cause。

**正确做法**:

1. 客户说要 X
2. PM 问 "为什么要 X?"
3. 客户答 "因为遇到 Y 问题"
4. PM 问 "Y 问题在什么场景出现?"
5. 客户讲场景
6. PM 推理 root cause Z
7. **PM 做的是解决 Z,不一定是 ship X**

#### 我的推荐

- **客户的"我要 feature X"接收后翻译成"我遇到 problem Y"再处理**
- **永远问 "why" 至少 3 次**(toyota 5 whys)
- **真实痛点往往跟 stated need 不同**——这是 PM 的 leverage

### 41. 错误 2:把模型能力当 PM 的事

**症状**:PM 要求工程师 "把这个 task accuracy 提升到 95%"。

**问题**:Task accuracy 主要由 model 决定,harness 能改善但不能 fundamentally fix。PM 越位到 model 团队的事。

**正确做法**:

1. PM 衡量 "整体 task success rate"
2. 分层 attribution: 多少是 model 不行,多少是 harness 不行
3. Model 部分 → 跟 model lead 沟通(他们的事)
4. Harness 部分 → 自己跟 eng lead 沟通(你的事)

#### 我的推荐

- **PM 不背 model accuracy KPI**(那是 model 团队的)
- **PM 背 harness-induced failure rate**(tool dispatch 失败 / permission 误拦 / observability 黑盒)
- **跨层 issue 找 model + harness 一起 review**

### 42. 错误 3:只盯 LLM evaluation 指标

**症状**:PM 每周开会看 "evaluation benchmark 涨了 2%",但 user-reported issues 没改善。

**问题**:Evaluation benchmark 是 proxy,user satisfaction 是真实 metric。Proxy 优化是 Goodhart's Law 经典案例。

**正确做法**:

| 看 | 不只看 |
|---|---|
| LLM-as-Judge 分数 | + User explicit feedback rate |
| Golden dataset accuracy | + Real production task completion rate |
| Latency P50 | + User-perceived speed survey |
| Cost per query | + Cost per successful task |

#### 我的推荐

- **永远把 evaluation metric 跟 user metric 联合看**
- **如果 evaluation 涨了但 user metric 没涨,evaluation 设计有问题**——重新审视 evaluation
- **不要 chase Goodhart's metric**——KPI 跟真实价值脱钩是 PM 失败的开始

### 43. 错误 4:不做 dogfooding

**症状**:PM 用 dashboard 数据做决策,自己从来不用产品。

**问题**:

- 数据不能告诉你 "为什么"
- 数据不能告诉你 "未来什么会突破"
- 数据不能让你 spot subtle UX 问题(loading state 一闪而过用户看不到 / 错误信息文案不清晰 / etc.)

#### 我的推荐

- **每天用产品 ≥ 1 小时**——不是 "demo 时用",是 "真实任务用"
- **记录 friction**——每次卡住的地方写下来,周末 review
- **JD 加分项 "对开发者体验有强感知"** = "你自己是 power user"——dogfood 是唯一路径

### 44. 错误 5:给所有人许诺(scope creep)

**症状**:Sales 喊要 feature A,客户喊要 feature B,model 团队要做 feature C,PM 全答应。

**结果**:Scope creep → 工程师赶不出来 → 质量下降 → trust 崩盘。

**正确做法**:

1. **每个季度 OKR 立完就锁死**——后续需求 backlog,下个季度 review
2. **紧急需求要 trade-off**——加 A 就要砍 B,不是 "都做"
3. **say no 是 PM 的核心技能**——比 say yes 难,但更有 leverage

#### 我的推荐

- **每周一次 "scope check"**——这周我答应了什么?加起来超 sprint 容量没?
- **永远有一份 "backlog" 文档**——客户喊的事进 backlog,不进 sprint
- **学会 "我会 prioritize,但不一定这季度做"**——给客户 hope 但不立 deadline

---

## Appendix

### A. DeepSeek JD 技术词清单对照

JD 任职要求第 4 条列的技术,playbook 中的覆盖:

| JD 技术词 | 本 playbook 章节 |
|---|---|
| LLM API | §12 |
| KV Cache | §12 |
| Agent Loop | §13 |
| Tool Use | §14 |
| Reasoning | §15 |
| Planning | §15 |
| Skills | §16 |
| MCP | §17 |
| Memory | §18 |
| Sub-agent | §19 |
| Multi-Agent | §20 |
| Prompt Engineering | §21 |
| Context Engineering | §21 |
| Harness Engineering | §21(整本 playbook) |

### B. OpenHarness 17 phase 的产品决策回顾

| Phase | 关键产品决策 | 决策框架对应 |
|---|---|---|
| 1 | Hello LLM,先做 streaming 不做 tool | "端到端薄切片" |
| 2 | Tool loop 是 harness 心脏 | Tier 0 必做 |
| 3 | Safety + Observability 早做 | 反面案例 §34 提前 |
| 4 | Microcompact 不做 LLM summary | Tier 1 / Tier 3 划分 |
| 5 | MCP 选 stdio transport | §17 推荐 |
| 5c | Skills 用 markdown + YAML | §16 推荐 |
| 5b | Slash command 不重写 | Tier 2 ROI 高 |
| 6 | Sub-agent 默认 max_depth=3 | §19 推荐 |
| 7a-c | Sandbox 分阶段(抽象 → Docker → gVisor) | §11 抽象先于实现 |
| 5d | Bundle 是 cross-layer composition | §5 抽象边界 |
| 5e/5f | Plugin 双 source 验证抽象 | §11 source-agnostic |
| 8 | Markdown_store rule-of-three refactor | §11 rule-of-three |
| 6+ | REPL 用 stream event 暴露 final state | §13 stream-event 优先 |
| 7 | 收尾包含 meta-retro | §39 第一季度交付 |

### C. 推荐资料

**LLM / Agent 技术原理**:

- Anthropic Engineering Blog —— Claude Code 设计原理
- OpenAI Cookbook —— Agent / Tool Use / Function Calling 实战
- LangChain blog —— 多 agent 架构演进
- METR research —— AI 能完成的任务长度趋势(每 7 个月翻倍)

**Production Agent 开源项目**:

- Claude Code(参考实现)
- Codex(OpenAI Coding Agent)
- Cursor / Cline(IDE-integrated)
- Manus(全自主 Agent 范例)
- OpenHarness from scratch(本项目)

**产品方法论**:

- "Inspired" by Marty Cagan —— PM 经典(LLM 时代仍适用)
- "Continuous Discovery Habits" by Teresa Torres —— 访谈方法论
- "Trustworthy Online Controlled Experiments" by Kohavi et al. —— A/B testing 严谨方法

**Vibe Coding / AI 协作**:

- [PLAYBOOK.md](./PLAYBOOK.md) (本项目 engineer 版 playbook)
- Karpathy on Vibe Coding —— 概念定义
- Anthropic "Effective context for Claude Code" —— Prompt engineering for AI协作

### D. 本 playbook 的源代码

所有论断都基于 [OpenHarness from scratch](./README.md) 实测,源代码 / commit history 全部公开:

- [`SPEC.md`](./SPEC.md) —— 项目契约
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) —— Tier 划分 + 依赖图
- [`learnings/phase-7.md`](./learnings/phase-7.md) —— Meta-retrospective
- [`decisions/`](./decisions) —— 24 个 boundary doc
- [`learnings/`](./learnings) —— 31 个 phase retro
- [`tasks/`](./tasks) —— 17 phase plan trail

---

## 收尾 —— 产品经理的真正护城河

LLM 产品技术词每年都在变。MCP 明年可能被新协议替代。Skills 范式可能演化成 别的形态。Multi-agent 可能终于跑通成主流。

但 **PM 的护城河不在技术词**,在**判断力**:

- 知道**什么时候**该投入哪个技术
- 知道**为什么**做 vs 不做某个 feature
- 知道**怎么**衡量产品是否真有价值
- 知道**跟谁**合作能放大杠杆

这本 playbook 教的不是 "MCP 怎么用",是 "**面对 MCP 这种行业标准时,PM 怎么思考做不做**"。前者 6 个月就过时,后者 10 年还有用。

23 天造一个 production-grade harness 是技术 + 方法论的双重证据。这本 playbook 是把这次经验提炼出来的产品判断框架。

**如果你今天要做 LLM Agent 产品 PM**,这本 playbook 是 day 1 工具箱。

**如果你今天要招 LLM Agent 产品 PM**,这本 playbook 是 评估候选人的 checklist。

无论你是哪一边,**带数据回来告诉我你怎么用它**。

---

## Pointers(更深入的话)

- [`README.md`](./README.md) —— 项目入口
- [`PLAYBOOK.md`](./PLAYBOOK.md) —— Engineer 视角 playbook(本 doc 的姐妹篇)
- [`CLAUDE.md`](./CLAUDE.md) —— 协作方法论(human-AI 工作流)
- [`learnings/phase-7.md`](./learnings/phase-7.md) —— Project meta-retrospective
- [`decisions/`](./decisions) + [`learnings/`](./learnings) —— 55 份完整 decision + retro trail
