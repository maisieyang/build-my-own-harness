# Phase 5 Preview — Extensibility (MCP + Slash Command)

> **Status**: PREVIEW（不是正式 boundary doc）。Phase 5 入口时基于此文档做正式
> Three-Axis 讨论，产出 `decisions/<NN>-phase-5-boundary.md` 拍板。
>
> 写于 2026-05-13。Phase 3 hook (P3-T4) 进行中时浮现的下游设计预演。沉淀这份
> 文档的目的：**避免 Phase 5 入口再想一遍**——boundary doc 入口时直接基于此
> 推进，能节省 2-3 小时设计时间。
>
> 关联：[`learnings/phase-3-framing.md`](../learnings/phase-3-framing.md) §9
> 框架 vs 业务 / [`decisions/08-phase-3-boundary.md`](../decisions/08-phase-3-boundary.md)
> D13.1 hook / D13.2 permission / [`ARCHITECTURE.md`](../ARCHITECTURE.md) §4 Phase 5

---

## Phase 5 Essence

> **把 Phase 3 装好的扩展点真正激活 —— harness 从单点应用升级为可扩展平台**。

**Deliverable**：
1. `oh ask` 能调用本地 + MCP server 的 tool（federated tool registry）
2. 用户能通过 markdown 文件 + Python API 自定义 slash command
3. Slash command 能切换 mode bundle（system prompt + catalog filter + permission + hooks）

**关键 invariant**：MCP / Slash command 完全复用 Phase 3 的 hook + permission 基础
设施 —— 不增加新的 dispatch path。这是验证 Phase 3 抽象是否做对的时刻。

---

## D14 MCP — Federated Tool Registry

### First-principles 锚

回到 [`phase-1-and-2.md §6 LLM-as-RPC-client`](../learnings/phase-1-and-2.md)：
**MCP = federated service registry**——把 service discovery 从 "本进程注册" 扩展
到 "跨进程 / 跨网络 / 跨组织"。

> 类比：MCP 之于 LLM tool 生态 ≈ USB-C 之于电子设备。

### D14.1 Transport 支持哪几种

MCP 协议定义 3 种 transport：

| 选项 | 含义 | 复杂度 | 用例覆盖 |
|---|---|---|---|
| **A. stdio only** | 本地 spawn 子进程 stdin/stdout JSON-RPC | 低 | ~80% |
| **B. stdio + SSE** | 加远程 HTTP 长连接 | 中 | ~95% |
| **C. 三种全支持** | 加 WebSocket | 高 | ~99% |

**Tentative: A (stdio only)** —— Claude Desktop / Cursor 的 MCP 几乎全用 stdio。
SSE 在 enterprise 远程场景才需要，留 Phase 6 hardening。

### D14.2 MCP server 怎么注册

| 选项 | 形态 | trade-off |
|---|---|---|
| **A. Settings 文件声明** | `OPENHARNESS_MCP_SERVERS=[...]` | 简单但 friction 大（改 env） |
| **B. CLI subcommand** | `oh mcp add github` | UX 好但要写 CLI |
| **C. Python API** | `registry.register_mcp(...)` | 灵活但 user 不易用 |
| **D. 三者都** | 完整 | 工程量大 |

**Tentative: A + C** —— Settings 路径已存在（沿用 D13.2 `Settings.deny_paths`
同形态），API 给 testing。CLI subcommand 留 Phase 5 后期 polish。

### D14.3 MCP tool 的 catalog 怎么 merge

MCP server 给 N 个 tool 后，怎么跟本地 tool 共存？

| 选项 | LLM 看到的 name | trade-off |
|---|---|---|
| **A. Flat namespace** | `CreateIssue` | 简洁但 name 冲突 |
| **B. snake_case prefix** | `mcp_github_create_issue` | 防冲突但难看长 |
| **C. Server-as-namespace** | `GitHub.CreateIssue` | 优雅但 wire 层处理 dot |

**Tentative: C (server-as-namespace)** + 内部 normalize 到 PascalCase（跟 D6.4 一致）。
LLM 看到 `GitHub.CreateIssue` —— 跟 OOP 方法调用直觉对齐。

### D14.4 MCP tool 失败怎么处理

MCP server crash / 网络断 / timeout。

| 选项 | 处理 | 取舍 |
|---|---|---|
| **A. 当 ToolError 喂回 LLM** | 跟本地 tool 失败一致 | 简单一致，但 cascade failure |
| **B. 重连 + retry** | transport 层 retry | robust 但复杂 |
| **C. Deregister + 喂回** | server 死了就摘掉 | 中庸 |

**Tentative: A 默认 + auto-respawn 限次**——保持 [framing §4.2 错误是 payload](../learnings/phase-3-framing.md)
的一致性。完整 retry 留 Phase 5 后期 hardening。

### D14.5 MCP 的 4 个 primitive 做哪些

MCP 不只 Tool：

| Primitive | 做？ | 理由 |
|---|---|---|
| **Tool** | ✅ 必做 | core use case |
| **Resource** | ⏸ Phase 6 | "喂数据给 LLM" 可以晚加；目前 RAG 自己做 |
| **Prompt** | ⏸ Phase 7 | niche，slash command 已经覆盖大部分 |
| **Sampling** | ⏸ Phase 6+ | 反向 LLM call，复杂且少见 |

**Tentative: Phase 5 only Tool**——保持 MVP scope。

---

## D15 Slash Command — Mode-as-Trigger 的 UX 形态

### First-principles 锚

[Mode = fan-out 配置源](../learnings/training-stack-framing.md) §14.1 ——一个概念
扇出 5 个 surface（prompt / catalog / permission / hooks / error）。Slash command
是 mode 切换的**用户显式触发器**。

### D15.1 复杂度光谱（核心决策）⭐

Slash command 可以从简单 prompt template 到完整 ModeBundle：

| 选项 | 形态 | user persona | 门槛 |
|---|---|---|---|
| **A. Prompt template only** | markdown 配置，`/cmd args` → template(args) 喂 LLM | 普通 user / Claude Code 风格 | 低 |
| **B. Template + system prompt** | A + 可换 system prompt | 中级 user | 中 |
| **C. 完整 ModeBundle** | catalog_filter + permission + hooks + prompt | framework 开发者 | 高 |
| **D. 任意 Python callback** | 完全自由 | extension dev | 高 + 安全风险 |

**Tentative: A + C 二选一支持，不要 B/D**。

理由：
- A 给**普通 user**（markdown 门槛低，扩展生态繁荣——Claude Code 路线）
- C 给**框架开发者**（Python ModeBundle，可深度定制）
- B 是"between"——能 A 就够，能 C 就完整
- D 太开放（任意 callback 跟 hook 重叠 + 安全面失控）

→ 两种 path 对应两种 persona，**避免 between** 是核心工程判断。

### D15.2 命令存放位置

类比 git config 多层 override：

| 层 | 路径 | 谁写 |
|---|---|---|
| **Global** | `~/.openharness/commands/<name>.md` | 用户跨项目用 |
| **Project** | `<project>/.openharness/commands/<name>.md` | 项目内复用 |

**Tentative: 两者都支持，local 优先**（类比 git config）。

### D15.3 命令的 args 怎么处理

| 选项 | 形态 | 取舍 |
|---|---|---|
| **A. 整 args 当 user message** | `/cmd hello` → user_message="hello" | 简单 |
| **B. 解析成 named fields** | frontmatter 声明 schema | 复杂但 type-safe |
| **C. freeform 让 LLM 解析** | 不处理直接喂 LLM | LLM-friendly 但不可控 |

**Tentative: A 起步**——B 留待真有 schema 需求。

### D15.4 Mode 切换是 stateful 还是 one-shot

| 选项 | 行为 | 例 |
|---|---|---|
| **A. One-shot 默认** | `/plan X` 只这一次 plan mode | 跟 tool 调用相似 |
| **B. Stateful 默认** | `/plan` 后整 session plan，直到 `/execute` 切 | 更接近 "mode" 概念 |
| **C. Per-command 声明** | frontmatter 里 `mode: stateful` or `one-shot` | 最灵活 |

**Tentative: C** —— 每个 command 在 frontmatter 自己声明。

例：
```markdown
<!-- /plan -->
---
mode: stateful   # 切完 stay in plan mode
---

<!-- /review -->
---
mode: one-shot   # 一次性 review,下轮回 default
---
```

→ 这条决策**直接影响 UX 体感**——选错了用户会撞墙。C 是 reversibility 最好的选择。

### D15.5 Custom command 能不能挂 hook（关键连接点）⭐

这是 D15 跟 D13.1（Phase 3 hook）的**关键连接点**。

| 选项 | 含义 |
|---|---|
| **A. 不能** | slash command 跟 hook 完全独立 |
| **B. 能** | frontmatter 声明 extra hooks |

**Tentative: B** —— 跟 D13.1 hook 系统**直接结合**。

例：
```markdown
<!-- /security-review -->
---
mode: one-shot
hooks:
  - audit_log         # 加 PostToolUse hook 记录
  - block_writes      # 加 PreToolUse hook 拒所有 write
---
Please review pending changes for...
```

→ 这是 **slash command + hook + permission 三者组合**的具体形态。是 Phase 5
跟 Phase 3 基础设施的桥梁。

---

## Cross-cutting Invariant — Phase 5 复用 Phase 3 基础设施

⭐ **Phase 5 的扩展点（MCP tool / Slash command）必须复用 Phase 3 已经装好的
横切基础设施（hook + permission），不增加新的 dispatch path**。

具体 dispatch 链路：

```
Slash command (/plan)
   ↓ 切 mode
ModeBundle (system prompt + catalog filter + permission + hooks)
   ↓
LLM 决策 → tool_use
   ↓
PermissionChecker (D13.2)          ← 一视同仁,本地 vs MCP
   ↓
PreToolUse Hook chain (D13.1)      ← 一视同仁
   ↓
Tool dispatch:
   ├── 本地 tool: tool.execute()
   └── MCP tool: mcp_client.call_tool()  ← Phase 5 新增
   ↓
ToolResult 喂回 LLM
```

**关键 invariant 三条**：

1. **MCP tool 跟本地 tool 在 PermissionChecker 看来一致**
   —— 同一个 `evaluate(tool_name, args, ctx)` 接口
   —— **不要**在 Permission 里加 `if isinstance MCPTool` 分支
2. **MCP tool execution 走同样的 PreToolUse / PostToolUse hook**
   —— 透明
3. **Slash command 切 mode 时可以 register/unregister hook**
   —— 这是 ModeBundle 的核心 power

→ 这是 [framing §7.4「现在 / 留位 / 不做」](../learnings/phase-3-framing.md) 在
Phase 5 入口的具体兑现：Phase 3 的扩展点是**留位**，Phase 5 是**激活那些留位**。

**如果 Phase 5 必须在 dispatch loop 加新分支处理 MCP**（"if 是 MCP tool 走另一条
路"）——**说明 Phase 3 hook/permission 的抽象没做对，要回头改**。这条 invariant
是 P3-T4 hook 实施的**隐藏 acceptance**。

---

## Tentative Recommendations Summary

| 决策 | Tentative |
|---|---|
| **D14.1 Transport** | stdio only;SSE/WS 留 Phase 6 |
| **D14.2 注册方式** | Settings + Python API;CLI subcommand 留 polish |
| **D14.3 Catalog merge** | server-as-namespace 形态（`GitHub.CreateIssue`） |
| **D14.4 Failure handling** | 当 ToolError 喂回 LLM;auto-respawn 限次 |
| **D14.5 其他 primitive** | 只做 Tool;Resource/Prompt/Sampling 留 Phase 6+ |
| **D15.1 复杂度光谱** | A markdown + C ModeBundle 二选一,**不要 B/D** |
| **D15.2 存放位置** | global + local,local 优先（类比 git config） |
| **D15.3 args 处理** | 整 args 当 user message |
| **D15.4 mode 切换** | per-command frontmatter 声明 stateful/one-shot |
| **D15.5 跟 hook 关系** | frontmatter 声明 extra hooks,跟 D13.1 直接结合 |

---

## Phase 5 Entry Three-Axis 时要重新走的题

到 Phase 5 入口时，**不是抄此文档拍板**——要重新走 Three-Axis 流程，因为：

1. **Phase 3 真实落地后** D13.1 hook / D13.2 permission 的接口形态可能跟现在略有
   不同，影响 cross-cutting invariant 的具体表达
2. **MCP 生态在变** —— 2026 年中可能有新 primitive / 新 transport，要重新评估 D14.5
3. **客户场景可能浮现新需求** —— 比如 ModeBundle 跟 catalog filter 的具体粒度

但**基础 framework 应该稳定**：5 件配套同构（framing §2）+ Phase 5 复用 Phase 3
基础设施 invariant。这条不变。

**Three-Axis 入口要问的 5 个问题**（不是答案，是问题）：

1. Phase 3 hook 接口真的稳定吗？Phase 5 要不要先改 D13.1 再做 D14/D15？
2. MCP 生态状态如何？stdio 仍是主流吗？
3. Custom slash command 应该用 markdown 还是 Python？还是两者支持？
4. ModeBundle 的具体字段（catalog_filter / permission_override / hooks）是不是
   够？需不需要加新字段？
5. cost cap / observability 跟 slash command 的关系？slash command 里的成本
   单独 track 吗？

---

## Pointers

- ARCHITECTURE.md §4 Phase 5 位置（短描述）
- `decisions/08-phase-3-boundary.md` D13.1 / D13.2 / D13.6（hook / permission / observability）
- `learnings/phase-3-framing.md` §9 框架 vs 业务 / §7 五条统一原则
- `learnings/training-stack-framing.md` §14 inference-time amplification
  （MCP 是 agentic strategy 的扩展，slash command 是 mode 切换）
- `learnings/phase-1-and-2.md` §6 LLM-as-RPC-client（federated registry 的位置）

---

## 一句话

> **Phase 5 = Phase 3 装好的扩展点真正被激活的时刻**——也是验证 Phase 3 抽象是否
> 做对的时刻。
>
> D14 MCP = federated tool registry；D15 Slash command = mode-as-trigger UX。两者
> orthogonal 但通过 ModeBundle 可组合（slash command 触发 mode bundle，mode bundle
> 包含 catalog filter 也作用于 MCP tool）。
>
> 唯一隐藏 invariant：**Phase 5 不增加 dispatch path**——MCP tool 跟本地 tool 在
> permission / hook 看来一视同仁。这条 invariant 反过来是 P3-T4 hook 实施的**隐藏
> acceptance**。
