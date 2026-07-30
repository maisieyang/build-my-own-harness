# 中国 Agent Harness 岗位调研

> 调研日期：2026-07-30
>
> 范围：中国大陆，优先社招与当前仍可访问的职位。
> 排名只依据 OpenHarness 项目，不包含候选人的学历、工作年限、当前薪资与城市偏好。

## 结论

`Harness Engineer` 在中国已经从非标准说法变成真实岗位名称，但市场仍处于早期：

- DeepSeek、腾讯、月之暗面、百度已经直接使用 `Agent Harness`；
- 阶跃星辰正在同时招聘 Agent 决策/评测、Coding Agent、AIOS 工具链、
  Agent 训推基建与 Agent 安全岗位；
- 盛大集团 AI 创新体系下的 EverMind 正在招聘长期记忆 OS、Agent 决策链、
  benchmark 与 Agent 后端相关岗位；
- 阿里开始把 `Agent Infra` 作为独立校招职类；
- 字节等公司更多使用 `Agent 评测工程师`、`AI Agent 开发工程师`；
- 更广泛的社招仍藏在 `Agent Runtime`、`Agent 平台`、`AI Developer Tools`、
  `Code Agent Eval`、`AgentOps` 等标题下。

岗位能力可以分为四类：

| 方向 | 主要工作 | OpenHarness 匹配 |
|---|---|---|
| Product Harness / Runtime | Tool loop、context、state、sandbox、long-running、human approval | **强** |
| Agent Eval / Observability | Trace、benchmark、failure taxonomy、regression、debugging | **强** |
| Agent Infra / Platform | K8s、调度、多租户、分布式 sandbox、gateway、SLO | 中等 |
| Model-Harness Co-design | Post-training、RL、data engine、model/harness joint optimization | 较弱 |

因此，最准确的求职定位是：

> **Agent Runtime / Harness + Evaluation Engineer**

不建议把自己泛化成 `AI Infra Engineer`。中国大多数纯 AI Infra 岗位实际招聘
CUDA、vLLM/SGLang、分布式训练、K8s/GPU scheduling，与当前项目不是同一方向。

## 第一优先级：直接匹配

### 1. 腾讯混元 — AI Agent Harness Engineer

- 地点：北京 / 深圳
- 类型：社招、全职
- 状态：2026-07 仍有近期收录和申请入口
- 职责：Agent 全链路 tracing/observability、自动化 eval、A/B testing、
  regression detection、debugging tools、评测与数据平台
- 要求：重度使用 Cursor/Claude Code/Codex；理解 agentic coding failure modes；
  全栈工程与系统设计；重视代码质量

**项目匹配：极高。**

OpenHarness 的 SWE-bench、failure taxonomy、judge meta-eval、structured logs、
dogfood findings 和 coding-agent 使用经历，几乎逐条命中 JD。主要缺口是公开材料
中还没有非常直观的 trace/debugging UI。

来源：

- https://haojianli.me/jobs/tencent_social-2052685072754196480-3fb195
- https://watchjobs.net/zh/explore/job/TENCENT_2052685072754196480/%E6%B7%B7%E5%85%83AI-Agent-Harness-Engineer-%E5%8C%97%E4%BA%AC%2F%E6%B7%B1%E5%9C%B3-%E8%85%BE%E8%AE%AF

### 2. DeepSeek — Agent Harness 团队 / Agent Infra 研发工程师

- 地点：北京 / 杭州
- 类型：全职与实习均有；官网当前在招
- 官网同时列出：`Agent Harness 团队`、`Agent Infra 研发工程师`、
  `Agent 后端`、`Code Agent 数据工程师`
- 公开信息显示 Harness 研发工程师通常要求 2 年以上软件开发经验，优秀者可放宽

**项目匹配：极高。**

OpenHarness 本身就是从 tool loop 到 sandbox/context/resume/eval 的完整实现；
SWE-bench campaign 证明不是只会拼 framework。DeepSeek 更可能追问 algorithmic
thinking、快速跨栈能力和 model-harness co-design，需要准备“同一模型只改 harness
如何影响结果”的实验论证。

来源：

- https://talent.deepseek.com/
- https://www.yicai.com/news/103192868.html
- https://www.nbd.com.cn/articles/2026-06-26/4438435.html

### 3. 月之暗面 — Harness 研究工程师

- 地点：北京
- 类型：社招、全职、个人贡献者、高级经验
- 职责：Kimi Agent 执行循环、工具调用、context compaction、state、retry；
  构建在浏览器/终端/桌面连续运行数小时的 long-running harness；探索
  meta-harness
- 要求：Agent Loop、Skills、MCP、Memory、Multi-Agent、sandbox、context
  engineering；能从 trace 和长任务日志反推结构性问题；Agent power user

**项目匹配：极高，但门槛高。**

这是当前公开 JD 中与 OpenHarness 代码面最接近的一份。SWE-bench 长跑、
provider drift、F6 evidence visibility、F17 snapshot seam、`/goal` external
judge 都可作为直接面试材料。缺口是 meta-harness 和大规模真实产品经验。

来源：

- https://watchjobs.net/zh/explore/job/00c1cb89-07b3-4002-a04c-9e12f291f2a7/Harness-%E7%A0%94%E7%A9%B6%E5%B7%A5%E7%A8%8B%E5%B8%88-Harness-Engineer%2FResearcher-%E6%9C%88%E4%B9%8B%E6%9A%97%E9%9D%A2
- https://app.mokahr.com/apply/moonshot/148506

### 4. 阶跃星辰 — AI Agent 工程师 / Coding Agent 开发工程师

- 地点：北京为主；部分 Agent 基建与安全岗位覆盖上海
- 类型：社招；当前公开职位仍有投递入口
- AI Agent 工程师：任务规划、工具选择、多步推理、human-in-the-loop；
  工具调用准确率、端到端完成率、成本与运行时评测；memory、context、
  sub-agent 与多 Agent 调度；从模型、prompt、工具链和系统架构分析失败根因
- Coding Agent / AIOS 方向：真实研发工作流、任务执行逻辑、benchmark、
  MCP/A2A 工具接口、任务调度、上下文管理和 API 调用
- 公开招聘聚合页显示 AI Agent 算法工程师为北京 40–70K × 16、3–5 年、本科

**项目匹配：极高。**

OpenHarness 的 plan/goal/auto-run、tool loop、context/state、sub-agent、权限边界、
SWE-bench 与 failure taxonomy 能直接对应。相比纯 Harness 岗，这些职位更可能追问
Agent 算法、模型评测与产品体验如何形成闭环，需要把“runtime 机制如何改变任务成功率”
讲成实验，而不只是工程功能列表。

同公司还有两个相邻方向：

- `Agent 基建算法工程师`：包含 Agent 训推全链路框架与 sandbox，项目有交集，
  但训练/推理基础设施是明显缺口；
- `Agent 安全专家`：身份、权限、运行时风控与 skills 和项目的 permission/sandbox
  高度相关，但明确要求安全研究、零信任或攻防背景，只适合有对应经历时投递。

来源：

- https://mqjob.cn/jobs/8815
- https://datahub.ac.cn/ai-jobs/topics/ai-application-engineer-jobs.html
- https://m.zhipin.com/zhaopin/75aaf86f7c63017f0nR73dS8GQ~~/
- https://www.chamd5.com/jobdetail.aspx?id=1732

### 5. 盛大集团 / EverMind — 大模型应用开发 / AI 算法 / Agent 后端

- 地点：上海 / 北京 / 硅谷；公开招聘信息将上海团队标为浦东张江
- 类型：官网社招；另有 EverMemOS AI 研发实习岗
- 大模型应用开发：Agent 长短期记忆、动态用户画像、任务规划、推理、工具调用，
  对任务成功率与响应时间负责
- AI 算法：EverOS 的 Memory / Retrieval / Decision / User 四层架构，
  参数化记忆、多步检索推理与 EverMemBench
- 后端开发：AI Agent 系统、智能搜索、生产集群故障定位与云原生业务编排

**项目匹配：很高。**

这是比通用 RAG 应用更接近 Harness 的长期状态与决策基础设施岗位。OpenHarness 的
context compaction、memory、snapshot/resume、`/goal` external judge、长任务恢复和
benchmark 能形成直接证据。主要缺口是向量检索/排序、参数化记忆、深度学习训练，
以及官网后端岗要求的 4 年生产经验和学历门槛。

需要区分同一创新体系中的项目：

- `EverMind / EverOS / EverMemOS`：当前最匹配，核心是 Agent 长期记忆基础设施；
- `Tanka`：陈天桥新推进的 AI-native company operating base，产品方向与 auto-run、
  role/intent 和企业执行系统相关，但暂未确认上海 Harness 工程岗；
- `MiroMind` 与 TCCI：更偏模型研究、Agent reasoning 或 AI + 脑科学，不应与
  EverMind 的工程岗位合并判断。

来源：

- https://evermind.ai/zh/careers
- https://evermind.ai/zh/careers/large-model-application-development-engineer
- https://evermind.ai/zh/careers/ai-algorithm-engineer
- https://evermind.ai/zh/careers/back-end-development-engineer
- https://www.zhipin.com/zhaopin/9e53cfd5b9cd42ad1nJ43N-8FA~~/

### 6. 月之暗面 — Agent 工程师 / AI 评估系统工程师

- 地点：北京
- 类型：当前职位聚合显示为 2026-07-20 更新
- Agent 工程师：LLM/Agent 评估平台、实验/回归/data loop、tools/skills、
  telemetry、CI/CD
- AI 评估系统工程师：Agent Eval Platform、Internal Benchmark、不同 Harness/Eval
  策略、线上线下评估闭环

**项目匹配：极高。**

与 Harness 研究岗相比，这两个岗位更偏工程和评测，OpenHarness 的现有证据更完整，
也更少依赖 model training 背景。

来源：

- https://datahub.ac.cn/ai-jobs/companies/company-819fc19826.html

### 7. 字节跳动飞书 Aily / 飞书妙搭 — 大模型/Agent 评测工程师

- 地点：上海（Aily）/ 杭州（妙搭）
- 类型：社招，页面显示可立即申请
- 经验：本科，3–5 年
- 页面薪资：28–45K × 12
- 技术标签：Golang
- 职责：通用 Agent 与 Code Agent 评测体系、真实业务用例抽象、分布式评测、
  benchmark/data governance、轨迹和工具调用问题诊断

**项目匹配：很高。**

SWE-bench adapter、records/predictions、failure taxonomy、programmatic scorer、
LLM judge、replay 和 capability-level eval 都可直接对应。明显缺口是 Go 与
大规模分布式评测平台经验。

来源：

- https://www.nowcoder.com/jobs/detail/429487
- https://www.nowcoder.com/jobs/detail/429488

## 第二优先级：相邻或有明显门槛

### 8. 上海人工智能实验室 — 大模型评测算法工程师

- 地点：上海
- 类型：全职、工程通道；页面发布时间 2026-07-16
- 要求：硕士及以上、Python
- 职责：Agent/LLM benchmark、任务环境、LLM-as-a-judge、
  execution-based evaluation、trajectory 与 failure-mode 分析

**项目匹配：很高。**

注意招聘页面归在校园招聘入口，需确认毕业时间/社招资格。

来源：

- https://www.shlab.org.cn/joinus/detail/7629174247638714650

### 9. 北京不等式科技 — 高级 AI Agent 工程师

- 地点：北京朝阳
- 类型：全职，3–8 年
- 要求：至少 1 年生产环境 LLM 系统；Python/TypeScript，团队栈 TS + Go
- 职责：tool calling、失败重试/并发/cache、多轮 context、任务/链路/成本 eval
- 申请：`hr@in-equal.com`

**项目匹配：高。**

主要风险是 JD 明确要求 production experience。项目能证明架构与工程能力，但不能
把学习项目包装成生产用户经验。

来源：

- https://in-equal.com/careers/senior-ai-agent-engineer

### 10. 深圳 Agent Harness 架构师（Randstad 客户）

- 地点：深圳
- 类型：Permanent
- 截止日期：2026-08-24
- 薪资：CNY 1,200,000–1,440,000 / 年
- 职责：企业级 Harness 平台、多 Agent scheduler、memory engine、sandbox、
  protocol、MCP、eval、trace/replay/observability

**项目内容匹配高，职级匹配未知。**

这是架构岗，除了 harness breadth，还会要求生产级多租户、高并发、团队技术领导和
量化业务成果。可作为 stretch application，不能作为唯一目标。

来源：

- https://www.randstad.cn/en/jobs/agent-harnessjia-gou-shi_shenzhen_90M0151014_18823_CN/

### 11. 北京出门问问 — AI Agent Tech Lead / CodeBanana

- 地点：北京
- 类型：社招、Tech Lead
- 公开薪资：40–60K/月
- 职责：Coding Agent、多 Agent、planning、tool chain、memory/context、
  MCP 与生产落地

**技术内容匹配高，管理/生产门槛较高。**

来源：

- https://cn.linkedin.com/jobs/view/ai-agent-tech-lead%EF%BC%88-agent%E5%BC%80%E5%8F%91%E6%8A%80%E6%9C%AF%E4%B8%93%E5%AE%B6%EF%BC%89-at-%E5%87%BA%E9%97%A8%E9%97%AE%E9%97%AE-4432146533

## 观察名单：公司成立或招聘信号已确认，具体 HC 待核验

### Tanka — AI-native company operating base

- 陈天桥/盛大体系的 AI-native company，公开产品方向包括企业长期记忆、
  vertical agents、role/intent 与自动化执行
- 盛大集团公开表示 Tanka、EverMind、MiroMind、Theta Health 等 AI portfolio
  正在全球招聘
- 但截至 2026-07-30，未找到仍有效且明确署名 `Tanka` 的具体工程 JD 与直接申请页
- LinkedIn 标注总部为美国 Redwood City，不能把盛大/EverMind 的浦东职位自动视为
  Tanka 上海 HC

**判断：方向匹配高，但当前应列为主动联系/持续监控，不计入已确认中国岗位排名。**

来源：

- https://www.tanka.ai/
- https://www.linkedin.com/company/tankaai
- https://www.linkedin.com/company/shanda-group

### LearnVector — 吴恩达新成立的 AI-native 教育公司

- 2026-07-28 刚正式公开，由吴恩达创立并领导
- Coursera 投资 1 亿美元，约占完全稀释后股权的三分之一
- 目标是用 Agent 构建一对一成人学习体验，让导师持续适配、练习并验证真正掌握
- 第一批产品计划于 2027 年初推出
- 截至 2026-07-30，未发现 LearnVector 自有 careers 页面或以公司名义发布的职位

**项目方向匹配很高，但目前不能称为“公开在招”。**

与它相邻、当前可以直接申请的吴恩达体系职位：

- `AI Fund — AI Engineer`：tool use、memory/state、planning/reflection、
  eval/observability、bounded autonomy 与 human review；Mountain View 现场
- `DeepLearning.AI Learning Experience Lab — AI Engineer`：教育产品中的
  agentic frameworks、evals、guardrails、RAG 与 full-stack；Mountain View 现场
- `AI Fund — Engineer in Residence: Research Coach`：教育 Agent、citation
  verification、sandbox、traceability 与 failure handling；12 周现场合同，
  明确要求美国工作许可且不提供签证

这些职位与 OpenHarness 的技术面高度匹配，但都是美国机会，不纳入中国市场排名。

来源：

- https://www.axios.com/2026/07/28/coursera-learnvector-andrew-ng
- https://www.businesswire.com/news/home/20260728999835/en/
- https://jobs.lever.co/AIFund/33bd1d6c-5091-42f8-99c0-6e97292782be
- https://jobs.lever.co/AIFund/273af06c-9114-4b9c-83c9-a3627f4b875f
- https://jobs.lever.co/AIFund/655b44a2-1e23-453f-b708-07e412701507

## 校招或市场信号

这些岗位证明了 job family 正在形成，但除非满足毕业时间，否则不应计入可投社招。

### 百度 — Agent Harness 研发工程师

- 地点：上海
- 官方校招，5 人，2026-07-21 发布
- 内容：state、sandbox、feedback loop、execution constraints、context handoff、
  compaction、long-running stability、human-agent collaboration
- 明确把 Harness 实践和开源贡献列为加分项

来源：

- https://talent.baidu.com/jobs/detail/GRADUATE/74d83772-1bd0-42b9-8cc5-69eb45696b62

### 字节 App Infra — AI Agent 开发工程师

- 地点：北京 / 上海 / 杭州
- 校招/实习方向
- JD 直接使用 `Agent Harness`，包括移动 runtime knowledge、Skills & CLI、
  trace 和大规模 validation，接入 Trae

来源：

- https://www.nowcoder.com/jobs/detail/446897

### 阿里巴巴 — Agent Infra / AI Agent 基础设施系统架构

- 地点：杭州、北京、上海等
- 当前结果主要是 2027 届实习/A-Star
- 能力面：sandbox、container orchestration、scheduler、state persistence、
  tool pipeline、memory、MCP、Agent Gateway、delegated identity、checkpoint、
  tracing、CLI/Skills

这是最完整的中国 Agent Infra 能力地图之一，但岗位本身不适合普通社招候选人。

来源：

- https://www.nowcoder.com/jobs/detail/439365
- https://www.nowcoder.com/jobs/detail/439379

### 阶跃星辰 StepStar — Agent 开发 / Agent 算法

- 地点：北京 / 上海
- 2026 年 StepStar 计划覆盖 Agent 与 Infra 等六个技术方向
- 全职面向 2026-09 至 2027-08 毕业生，实习面向 2026-09 及以后毕业生
- Agent 实习岗包括算法设计、Agent 决策能力与鲁棒性优化、模型评估和业务落地

来源：

- https://www.aipress.com.cn/news/details?id=79401
- https://www.deizao.net/m/index/shixixq/jobid/114792

## 城市分布

| 城市 | 当前主要机会 |
|---|---|
| 北京 | DeepSeek、腾讯混元、月之暗面、阶跃星辰、不等式、出门问问；岗位密度最高 |
| 上海 | 盛大/EverMind、阶跃星辰、字节飞书 Aily、上海 AI Lab、百度校招；偏 memory、eval 与产品 harness |
| 杭州 | DeepSeek Agent Infra、字节飞书妙搭、阿里 Agent Infra |
| 深圳 | 腾讯混元、企业级 Agent Harness 架构岗 |

只看 harness/runtime/eval，北京最集中；上海与杭州次之；深圳职位少但可能更偏
企业平台和高职级。

## 搜索关键词

在 BOSS、猎聘、脉脉、牛客和公司官网同时使用：

```text
Agent Harness
Harness Engineer
Agent Infra
Agent Runtime
Agent 平台 / 智能体基础设施
Coding Agent / Code Agent
Agent 评测 / Code Agent 评测
Agent Eval / LLM Eval
Agent Observability / Agent Debugging
Context Engineering
Agent Sandbox
AgentOps
AI Developer Tools / AI IDE
```

不要只搜 `AI Infra`，否则结果会被训练/推理/GPU 岗位淹没。

## OpenHarness 对岗位的映射

### 投腾讯混元

主叙事：**coding-agent failure modes + eval/observability**。

重点材料：

- SWE-bench `RUNLOG.md` 与 `TAXONOMY.md`
- F6：错误 output truncation 如何制造 hallucination
- 5 个 benchmark 反向发现的 harness defects
- capability-level eval、judge meta-eval、replay gate

### 投 DeepSeek / 月之暗面 Harness

主叙事：**从零实现 runtime，并用真实长任务验证 control boundaries**。

重点材料：

- typed tool loop 与 permission/sandbox
- compaction、snapshot/resume、memory
- `/goal` external checker 与 headless repair loop
- F9 authorization vs containment
- F17 state transition vs persistence seam
- SWE-bench 的 provider/config drift 与 self-hosted evaluator

### 投阶跃星辰 Agent / Coding Agent

主叙事：**runtime control plane + eval-driven Agent iteration**。

重点材料：

- plan → approve → goal/auto-run 的完整任务生命周期
- tool selection、permissions、sandbox 与 human-in-the-loop
- context compaction、snapshot/resume、sub-agent delegation
- SWE-bench 指标、failure taxonomy 与 harness defect 归因
- 用同一模型做 runtime/权限/上下文变量实验，而非只展示功能数量

### 投盛大 / EverMind

主叙事：**long-horizon state + memory as Agent infrastructure**。

重点材料：

- context compaction、memory、snapshot/resume 的边界与状态模型
- 长任务中 provider/config drift、恢复与可审计记录
- `/goal` 独立判定器和 benchmark-driven iteration
- 为什么记忆不是无限追加 context，以及 retrieval、persistence、decision 的边界
- 主动说明尚未实现向量检索、参数化记忆和模型训练

### 投字节 / 上海 AI Lab Agent Eval

主叙事：**可复现 benchmark + failure attribution**。

重点材料：

- shipped CLI 驱动 300 题，不用 benchmark-only private path
- conditions stamped into records
- programmatic oracle vs probabilistic judge
- resolved/unresolved turn-distribution finding
- raw records/predictions/official verdicts 可审计

### 投平台/架构岗

主叙事：**control-plane breadth**。

同时主动说明缺口：

- 当前没有生产多租户和高并发规模；
- Docker/gVisor 是功能实现，不是大规模 sandbox fleet；
- 没有 K8s scheduler/gateway/identity service；
- `cli.py` 仍有 composition-root monolith 风险。

诚实说明这些边界，比把个人项目包装成生产平台更可信。

## 当前竞争力与缺口

### 已形成差异化

- 不是 LangChain workflow demo，而是完整 runtime/control plane；
- 有真实 benchmark campaign，不只 unit tests；
- 能区分 model failure、harness failure 与 environment failure；
- 有 permissions、sandbox、state、external verification 的系统判断；
- 有 2,783 tests、95.29% coverage、strict typing 和 append-only design record；
- 是 Claude Code/Codex 的重度用户，能讲具体 failure mode。

### 投递前需要补足表达，不一定补代码

1. **Go/Java/TypeScript：** 字节、阿里和部分平台岗会卡主语言。
2. **分布式生产经验：** K8s、多租户、SLO、online observability 是主要缺口。
3. **模型训练背景：** RL/post-training/inference infra 不是当前优势，不要硬投为主线。
4. **业务结果：** 项目是学习与研究 artifact，第二阶段必须用既有工作经历补充
   真实用户、规模、成本、稳定性或业务收益。
5. **公开影响力：** 将 SWE-bench 战役写成一篇中英文技术文章，会明显增强
   DeepSeek/月之暗面这类岗位的可信度。

## 建议投递顺序

只按当前项目匹配度：

1. 腾讯混元 AI Agent Harness Engineer
2. DeepSeek Agent Harness / Agent Infra
3. 月之暗面 Harness 研究工程师
4. 阶跃星辰 AI Agent / Coding Agent
5. 盛大集团 / EverMind 大模型应用开发
6. 月之暗面 Agent 工程师 / AI 评估系统工程师
7. 字节飞书 Aily / 妙搭 Agent 评测
8. 上海 AI Lab 大模型评测
9. 不等式高级 AI Agent 工程师
10. 深圳 Agent Harness 架构师（stretch）

实际顺序应在第二阶段加入工作年限、学历、城市和薪资后重新计算。

## 信息可信度

- **高：** DeepSeek 官方招聘、EverMind 官方招聘、百度官方招聘、
  上海 AI Lab 官方招聘、不等式公司官网。
- **中高：** 牛客社招职位页、Randstad 原始职位页、Moka 申请入口聚合；
  阶跃星辰 AI Agent 职位聚合页提供 Moka 投递入口。
- **中：** WatchJobs、DataHub、好简历等职位聚合；投递前应回到公司官网或联系
  招聘方确认 HC。
- AI 估算薪资不作为事实使用。本文只保留招聘页面直接披露的薪资。
