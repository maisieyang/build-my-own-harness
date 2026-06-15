# OpenHarness — 认知地图（逆向 HKUDS/OpenHarness v0.1.9）

> **Source attribution**：[HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness) **v0.1.9**（MIT）的逆向认知地图。本仓库与上游无隶属、未拷贝代码；接口为最小形状伪代码，实现独立重建。
>
> **定位**：认知脚手架，不是工程合同。回答"一个 harness 由哪些核心要素构成、每个解决什么问题"。不收实现精度（常量/算法——读源码 lazy 对齐）。**整份冻结：§1-§4 立认知；§5 把 §3 切成按依赖排的 build 模块（不追踪进度）。**
>
> **两个诚实声明**（按 reverse-spec 纪律）：
> 1. §3 的 trade-off 除标注 **(上游明说)** 外，均为**从代码形态推断**——记录的是"它这么设计"，动机是我的推断，不是上游的声明。
> 2. §3.④ 的模块指针锚定 **v0.1.9**；上游活跃演进，指针可能过期，用时重新定位。
>
> **形态**：按"harness 必须解决的硬问题"组织（概念地图），不按上游 33 个 src 目录（代码地图）。

## 目录
§1 这是什么 · §2 架构与数据流 · §3 九个核心要素 · §4 跨要素模式 + 与 Claude Code 对比 · §5 模块拆分（按依赖排，冻结）

---

## 1. 这是什么

OpenHarness = **"Claude Code 的开源 Python 复刻"**。把 Claude Code 的 agent runtime 用 Python 开源重写，交付 tool-use / skills / memory / 多 Agent 协同的轻量底座。含 `oh`（harness 本体）+ `ohmo`（建其上的个人 Agent）。

**技术栈**

| 层 | 选型 |
|---|---|
| 语言 / 数据模型 | Python ≥ 3.11 · Pydantic |
| CLI / API 客户端 | Typer · anthropic SDK · openai SDK |
| 主 UI | React + Ink |
| 协议 / 沙箱 | MCP · srt + Docker |

**入口**：`openharness.cli` / `ohmo.cli`。隐藏模式：`--backend-only`（React 后端宿主）、`--task-worker`（swarm 子进程）、`-p`（headless，eval 入口）、`--dry-run`（预览不调模型）。

## 2. 架构与数据流

### 2.0 目录树（实现层宏观架构 · 上游怎么码的，如实照搬 v0.1.9）

> 这棵树是镜头一：专业团队怎么把系统切成目录。§3 是镜头二：按问题重组的认知。§3.④ 连两者。

```
src/openharness/
├── engine/           Agent 循环 + 会话历史（心脏）
├── api/              Provider 抽象（统一流式协议，~22 家）
├── config/           Settings + provider profile + 路径解析
├── auth/             凭证存取 + 三大订阅桥（Claude/Codex/Copilot）
├── tools/            44 工具的 BaseTool + 注册表 + 内置工具
├── commands/         ~80 slash 命令 + 动态 skill/plugin 命令
├── prompts/          系统提示装配 + 环境快照 + CLAUDE.md 发现
├── permissions/      工具调用权限门（分层链）
├── hooks/            生命周期拦截（10 事件 × 4 类型）
├── services/         压缩 / 会话存储 / session-memory / cron / autodream / 抽取 / lsp
├── memory/           持久记忆 vault（Markdown + 启发式检索）
├── skills/           SKILL.md catalog + 懒加载
├── plugins/          可安装能力单元（manifest + 各类产物）
├── mcp/              MCP 客户端（stdio + HTTP）
├── sandbox/          srt + Docker 双沙箱
├── swarm/            多 Agent 执行后端 + 文件 mailbox + worktree
├── coordinator/      编排模式 + worker + <task-notification> XML
├── tasks/            后台子进程管理（单例 + 状态机）
├── bridge/           远程/云 session 宿主
├── channels/         pub-sub 总线 + 11 个聊天平台网关
├── ui/               React 后端宿主 + print 模式 + JSON-lines 协议
├── state/            AppStateStore（观察者）
├── cli.py            Typer CLI 入口
├── platforms.py      平台 / 能力矩阵检测
├── utils/            原子写 · 文件锁 · SSRF guard · shell 子进程
├── themes/ keybindings/ vim/ voice/ personalization/ output_styles/   TUI 周边
└── autopilot/        GitHub issue→PR 自动化状态机
顶层：ohmo/（个人 Agent）· frontend/（React+Ink TUI）· autopilot-dashboard/（Vite web）· tests/ · scripts/
```

### 2.1 分层（栈式框图，看得到层级）

```
┌──────────────────────────────────────────────┐
│ 产品面     CLI · TUI · headless · 聊天网关      │
├──────────────────────────────────────────────┤
│ 运行时装配  RuntimeBundle（headless/TUI 共享）  │
├──────────────────────────────────────────────┤
│ 核心循环    QueryEngine + run_query             │
├──────────────────────────────────────────────┤
│ Provider 抽象  一个流式协议屏蔽 ~22 家          │
├──────────────────────────────────────────────┤
│ 横切        权限 · hook · 记忆 · 压缩 · 扩展     │
└──────────────────────────────────────────────┘
```

### 2.2 数据流（一条用户输入的旅程，看得到流动）

```
用户输入
  │
  ▼
[submit_message]  sanitize 历史 · append · USER_PROMPT hook
  │
  ▼
╔══ run_query 循环（每轮）══════════════════════════════╗
║   压缩（上下文够满才触发）                              ║
║      │                                                ║
║      ▼                                                ║
║   api.stream_message ──► 文本增量 / 完整回复            ║
║      │                                                ║
║      ▼                                                ║
║   有 tool_use ?                                       ║
║      ├── 否 ──► STOP hook ──────────────────────► 退出 ╬═► [finally]
║      └── 是 ──► 权限检查 → 执行工具 → append 结果        ║       更新记忆
║                    │                                  ║       抽取 durable
║      ▲─────────────┘  回到循环顶                       ║
╚═══════════════════════════════════════════════════════╝
```

循环按"**模型这轮有没有发起 tool_use**"驱动（不是预设步骤）；换 provider 不动循环。**这张流程图就是 §3 九个要素的来源**——每个关口/分支背后，都是一个要素在解决一个硬问题。

---

## 3. 九个核心要素

**这 9 个要素怎么来的**（应用 reverse-spec 测试一）：删掉上游所有目录名，纯从 §2 数据流问"每条输入必经哪些关口 + 系统为了不死必须解决哪些硬问题"推出来的。**几乎每个要素都横跨多个上游目录**（不是 1:1 对目录），这是"提炼过、不是照搬"的信号。

| # | 核心要素 | 它是"必经关口"还是"硬问题" | 横跨的上游目录 |
|---|---|---|---|
| 1 | Agent 循环 | 必经：每条输入都进循环 | engine |
| 2 | 模型接入 | 必经：每轮都调模型 | api + config + auth |
| 3 | 工具 | 必经：循环靠它推进 | tools + commands |
| 4 | 上下文不爆 | 硬问题：长 session 必撑满 | services/compact |
| 5 | 安全边界 | 必经：每次工具调用要过门 | permissions + hooks + sandbox |
| 6 | 持久记忆 | 硬问题：跨 session 续认知 | memory + services |
| 7 | 能力扩展 | 硬问题：不改核心加能力 | skills + plugins + mcp |
| 8 | 多 Agent 协同 | 硬问题：单上下文不够用 | swarm + coordinator + tasks + bridge + channels |
| 9 | 产品接触面 | 硬问题：让人/系统用上 | ui + cli + state + ohmo + autopilot + frontend |

> 每个要素四段：解决什么问题（why）→ 关键 trade-off → 最小接口形状 → 上游在哪实现。

### 3.1 Agent 循环 —— 让模型持续工作
- **问题**：模型一次只回一段话。怎么让它持续"读环境 → 调工具 → 看结果 → 推进"直到完成？
- **trade-off (推断)**：循环靠"模型这轮有没有发起 tool_use"判断继续/停，不靠预设步数或状态机。代价是流程不可预测（LLM 自己当编排器），换来开放任务的自适应。引擎切两层（持久状态 / 纯循环）是为可重入、可测。
- **形状**：`run_query(ctx, messages) -> AsyncIterator[StreamEvent]`；退出 = `final_message.tool_uses` 为空。
- **上游**：`engine/`。

### 3.2 模型接入 —— 屏蔽 provider 差异
- **问题**：背后可能是 Anthropic / OpenAI / Codex / 18 家兼容端，怎么让上层一行不改就能换？
- **trade-off (推断)**：抽象缝定在"一个流式协议"，所有 client 都吃统一请求、吐统一事件流。代价是每家的格式转换/重试塞进各自 client（脏活下沉），换来循环层零感知。再延伸——读你已装的 Claude Code/Codex 凭证，"接入"变成"不要额外 key"。
- **形状**：`Protocol.stream_message(req) -> AsyncIterator[ApiStreamEvent]`；换 provider = 换 profile。
- **上游**：`api/` + `config/`(profile) + `auth/`(订阅桥)。

### 3.3 工具 —— 给模型手脚
- **问题**：模型只能输出文本，怎么让它真读写文件、跑命令、搜网、派子任务？
- **trade-off (推断)**：用 Pydantic 模型当 schema + 显式注册（非自动发现）。代价是加工具要手写注册，换来类型校验 + 清晰边界。工具不持有权限策略，靠 `is_read_only` + 审批回调与权限层协作（关注点分离）。
- **形状**：`BaseTool: name; description; input_model: Pydantic; async execute(args, ctx) -> ToolResult`；40+ 内置（文件/shell/搜索/web/agent/task/cron/mcp/skill）。
- **上游**：`tools/` + `commands/`。

### 3.4 上下文不爆 —— 压缩
- **问题**：上下文窗口有限，长 session 必撑满。怎么腾空间又不丢关键信息？
- **trade-off (推断)**：分级阶梯——先免费的（清旧工具输出）→ 确定性的（截长块/会话摘要）→ 最后才花钱让 LLM 摘要；每级压完重看够不够，能省则停。代价是实现复杂，换来"尽量不调 LLM、尽量保近期"。
- **认知陷阱（原则，过测试二：换实现也成立）**：压缩切分点**绝不能切断 tool_use/tool_result 配对**，否则下次请求被 provider 拒。这是重建这块最容易翻车的点。
- **形状**：`auto_compact_if_needed(messages) -> messages`；阶梯 microcompact → collapse → session-memory → LLM-summary。
- **上游**：`services/compact` + `services/session_memory`。

### 3.5 安全边界 —— 不让模型乱来
- **问题**：模型会调危险工具（删文件、读凭证、跑任意命令）。怎么挡又不挡死正常活？
- **trade-off (推断)**：分层链、首个命中即决，且**敏感路径（SSH/云凭证）硬编码、任何模式不可覆盖**。代价是规则优先级得记牢，换来"最危险的事不靠用户配置兜底"。hook 给可编程拦截（确定性），区别于 CLAUDE.md 的软指引；sandbox 给进程级隔离，opt-in。
- **形状**：`permission.evaluate(tool, *, is_read_only, file_path, command) -> Decision`；hook 10 事件 × 4 类型，可 block。
- **上游**：`permissions/` + `hooks/` + `sandbox/`。

### 3.6 持久记忆 —— 跨 session 记住
- **问题**：session 结束就忘。怎么跨会话记住偏好/事实，又不膨胀、不串错？
- **trade-off (推断)**：Markdown vault + 启发式打分检索（metadata 权重高于正文，叠加重要度/频率/新鲜度），后台固化 + 自动抽取。代价是检索是启发式不是语义向量（可能漏），换来零依赖、人可读可改、可版本化。
- **形状**：`find_relevant_memories(query, cwd) -> [MemoryHeader]`；vault = frontmatter + body 的 `.md`，`MEMORY.md` 当索引。
- **上游**：`memory/` + `services/memory_extract` + `services/autodream`。

### 3.7 能力扩展 —— 不改核心加能力
- **问题**：怎么让别人（甚至非工程师）给 agent 加能力，而不碰核心代码？
- **trade-off (推断)**：分三件——skill（Markdown 写的经验，业务专家能写，catalog + 懒加载省 context）、plugin（可安装单元 = 组织边界，带版本/权限）、mcp（连外部系统的开放协议）。代价是三套机制各自维护，换来"核心薄、能力由生态加"。
- **形状**：skill = `SKILL.md`(name/description + body)；plugin = manifest + 各类产物；mcp = stdio/http server。
- **上游**：`skills/` + `plugins/` + `mcp/`。

### 3.8 多 Agent 协同 —— 委派与并行
- **问题**：单 agent 上下文有限、串行慢。怎么拆给多个 agent 并行/委派，又不失控？
- **trade-off (推断)**：文件 mailbox（原子写）通信 + 多执行后端 + worktree 隔离。代价是文件 IPC 比内存慢，换来跨进程/跨机统一 + 崩溃可恢复。
- **认知陷阱（原则）**：子 agent 三重防护——工具禁用 + 权限默认拒 + **worker 工具集不含 agent 工具（结构性深度上限）**。
- **形状**：`TeammateExecutor.spawn/send_message/shutdown`；coordinator 派 worker，结果作 `<task-notification>` 异步回（靠标签识别，不当对话）。
- **上游**：`swarm/` + `coordinator/` + `tasks/` + `bridge/` + `channels/`。

### 3.9 产品接触面 —— 让人用上
- **问题**：再强的 runtime，用户得有入口。CLI？聊天？嵌进已有工作流？
- **trade-off (推断)**：JSON-lines 协议把 React/Ink 前端和 Python 后端分开，且 **headless 与 TUI 共享同一 runtime**（同 engine 不同 sink）。代价是协议层要维护，换来一套 runtime 喂多个接触面。
- **认知点（你做 eval 会用）**：`oh -p --output-format stream-json` 是 headless 驱动入口，强制 max_turns + auto-allow。
- **形状**：`RuntimeBundle`（共享核心）；UI 协议方向不对称（后端→前端加 `OHJSON:` 前缀，前端→后端裸 JSON）。
- **上游**：`ui/` + `cli.py` + `state/` + `ohmo/` + `autopilot/` + `frontend/`。

---

## 4. 跨要素的模式 + 与 Claude Code 对比

### 4.1 反复出现的模式（横跨多个要素）

| 模式 | 在哪些要素反复出现 | 一句话 |
|---|---|---|
| 原子写 + 文件锁 | 记忆 / 会话 / cron / 凭证 / mailbox | 持久状态的不变量：temp→rename + 锁，否则崩溃留半截文件 |
| 优雅降级 | 模型接入 / 扩展(mcp) / 安全(sandbox) | 连不上的依赖标失败、不中断启动 |
| 事件流解耦 | 循环→UI / 聊天网关 | AsyncIterator 流，生产者不知道消费者 |
| 薄底座 + 可插拔 | 扩展 / 接触面 | 核心保持薄，能力/接触面靠插件和协议加 |
| 默认安全 | 安全 / 聊天 allow_from / 项目 plugin | 危险的默认关、敏感的失败关 |

### 4.2 与 Claude Code 对比（锚到你的用户体感）

| Claude Code | OpenHarness 里的对应要素 |
|---|---|
| 自动 compact | §3.4 上下文不爆 |
| `/` 命令 + Skill | §3.3 工具 + §3.7 扩展 |
| Plan mode / 权限 | §3.5 安全边界 |
| Subagent / Task | §3.8 多 Agent 协同 |
| MCP | §3.7 扩展 |
| `claude -p` headless | §3.9 接触面（eval 入口） |
| CLAUDE.md / memory | §3.6 持久记忆 |

---

## 5. 模块拆分（build 顺序 · 冻结）

> 把 §3 的 9 个认知要素切成 build 模块。**模块 = 一次设计 + 一个 build arc + 一篇 §回顾**：原子要素 1:1；簇型要素按 §3 末列的子目录缝拆开（每个模块 → 它独占的几个上游目录，这就是 §3.④ 概念↔目录树的桥落到实处）。**列表顺序 = 依赖拓扑序 = 建造顺序**——先建能让一条输入端到端跑通的最小骨架，再按依赖往外加层。
>
> 这是**结构**（谁依赖谁、谁先谁后），不是**进度**。状态 / 谁 done / 选下一个不在这——去 git 和各模块的 `tasks/<module>-plan.md`。所以本章和 §1-§4 一样冻结。

| # | 模块 | 上游目录（拆自 §3.x） | 依赖 | 拆 / 排序理由 |
|---|---|---|---|---|
| | **骨架** —— 目标：一条输入 input→model→tool→result 端到端跑通 | | | |
| 1 | 模型接入·流式协议 | `api/` + `config/`(profile)（§3.2） | — | 循环要消费它的事件流，是底座 |
| 2 | Agent 循环 | `engine/`（§3.1） | 1 | 系统心脏 |
| 3 | 工具系统 | `tools/` + `commands/`(基础)（§3.3） | 2 | 循环要有东西可派 |
| 4 | 接触面·CLI | `cli.py`（§3.9 子集） | 2 | 跑/测循环的最小入口 →✅ 端到端跑通 |
| | **核心横切** —— 骨架之上"不死"的必需 | | | |
| 5 | 安全·权限+hooks | `permissions/` + `hooks/`（§3.5 子集） | 3 | 工具调用要过门 |
| 6 | 上下文压缩 | `services/compact` + `…/session_memory`（§3.4） | 2 | 长 session 必撑满；切分别断 tool_use/result 对 |
| 7 | 持久记忆 | `memory/` + `services/memory_extract` + `…/autodream`（§3.6） | 2 | 跨 session 续认知 |
| | **加能力** —— §3.7 三件各自独立机制 | | | |
| 8 | 扩展·skills | `skills/`（§3.7） | 3 | markdown 经验 + 懒加载 |
| 9 | 扩展·plugins | `plugins/`（§3.7） | 8 | 可安装单元 = 组织边界 |
| 10 | 扩展·mcp | `mcp/`（§3.7） | 3 | 外部工具进注册表 |
| | **进阶** —— 多 Agent（§3.8，认知增量最高，有内部依赖序） | | | |
| 11 | 多Agent·tasks | `tasks/`（§3.8） | 2 | 后台子进程管理（单例+状态机） |
| 12 | 多Agent·swarm | `swarm/`（§3.8） | 11,3 | 执行后端 + 文件 mailbox（原子写） |
| 13 | 多Agent·coordinator | `coordinator/`（§3.8） | 12 | 编排 + `<task-notification>` 异步回 |
| | **增强 / 可选** —— 后置 | | | |
| 14 | 模型接入·凭证+订阅桥 | `auth/`（§3.2） | 1 | 裸 key 能跑后再加（读已装凭证） |
| 15 | 接触面·TUI+state | `ui/` + `state/` + `frontend/`（§3.9） | 4 | 富 UI（JSON-lines 协议，共享同 runtime） |
| 16 | 安全·sandbox | `sandbox/`（§3.5） | 5 | 进程级隔离，opt-in |
| 17 | 多Agent·channels/bridge | `channels/` + `bridge/`（§3.8） | 12 | 聊天平台网关 / 远程 session 宿主 |

**覆盖核对**：§3 九要素的全部上游目录都落到了某个模块（原子 1:1，簇型按子目录拆）。例外——`ohmo/`（个人 Agent）、`autopilot/`（issue→PR 自动化）是**建在 harness 之上的应用**，不是 harness 模块，不进本拆分。

同 tier 内顺序可换（无硬依赖）；跨 tier 的依赖是真的。**9 认知要素 → 13 核心模块（+4 可选）**。

---

*reverse-spec · 逆向 OpenHarness v0.1.9 · §1-§5 全冻结：§1-§4 立认知（应用 要素vs目录 / 认知vs实现 两测试），§5 按依赖排定的 build 模块拆分（不追踪进度）。trade-off 标注推断/明说，模块指针锚定 v0.1.9。*
