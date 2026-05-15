# Phase 5c Preview — Skills (Lazy-Loaded Expertise via Tool Calling)

> **Status**: PREVIEW（不是正式 boundary doc）。Phase 5c 入口时基于此文档做正式
> Three-Axis 讨论，产出 `decisions/12-phase-5c-skills-boundary.md` 拍板。
>
> 写于 2026-05-15，在 Phase 5 MCP（5a）落地中段浮现。本文档的真正价值在
> §2 把"加载 skill"看穿成**LLM-as-RPC + tool dispatch** 的应用层 convention，
> 而非新机制。
>
> 关联：[`tasks/phase-5-preview.md`](./phase-5-preview.md) D15 slash command
> 兄弟 / [`decisions/11-phase-5-boundary.md`](../decisions/11-phase-5-boundary.md)
> cross-cutting invariant / [`learnings/phase-1-and-2.md`](../learnings/phase-1-and-2.md)
> §6 LLM-as-RPC-client framing

---

## Phase 5c Essence

> **Skills = curated expertise（markdown 文件）通过 tool calling 懒加载进
> context**。Harness 不增加任何新 dispatch path —— Skills 是现有 tool
> 基础设施的一个 tenant，不是新子系统。

**Deliverable**：

1. `LoadSkill(name)` BaseTool
2. Skill catalog（name + description）在 CLI 启动时自动拼进 system prompt
3. Frontmatter 解析的 markdown 在 `~/.openharness/skills/` + `<project>/.openharness/skills/`
4. **Cross-cutting invariant**：permission / hook / engine / observability **零改动**

**代码量估算**：~170 行

- `tools/load_skill.py` ≈ 50 行（一个 BaseTool 子类）
- `prompts.py` catalog 拼接 ≈ 20 行（system_prompt 函数加一节）
- `skills/` package（discovery + frontmatter parse + Skill dataclass）≈ 100 行

**Cross-cutting invariant 是 Phase 5 MCP / Phase 6 Sandbox invariant 的第三次兑现**——
证明 Phase 3 hook/permission 抽象的稳定性：MCP（外部 tool）、Sandbox（外部
执行环境）、Skill（外部知识）都不需要框架开新路。

---

## The First-Principles Unification（这份 doc 的灵魂）

### 加载 skill 的精确 trace

```
[ Bootstrap (CLI 启动) ]
  扫描 ~/.openharness/skills/ + <project>/.openharness/skills/
  解析 frontmatter → 生成 catalog 字符串：
    "## Available Skills (call LoadSkill to expand):
     - react-testing: <description>
     - sql-perf-tuning: <description>"
  catalog 拼进 system_prompt
  注册 LoadSkill BaseTool 进 ToolRegistry

[ Turn 1 ]
  user: "帮我写 React 组件测试"
  LLM 看 system prompt catalog → "我要 react-testing"
  emits tool_use: LoadSkill(name="react-testing")

[ Dispatch — engine/query.py 现有路径，零改动 ]
  permission_check → ALLOW (LoadSkill is_read_only=True)
  hook PreToolUse → pass
  LoadSkill.call(input, ctx):
    body = read_file("~/.openharness/skills/react-testing.md")
    body = strip_frontmatter(body)
    return ToolResult(output=body)
  hook PostToolUse → pass
  log tool_complete

[ Turn 2 ]
  messages[] 现在带 tool_result(<5K tokens skill body>)
  LLM 看 body 指引 → 写答复 / 或再 LoadSkill("react-router-testing") 递归
```

### 这是一个通用 pattern

把"skill"抽掉，本质是：

```
[ Index ]     小，sticky，进 system prompt
[ Lookup ]    LLM 决策，通过 tool 调用
[ Content ]   大，lazy load，进 tool_result（messages）
[ Recurse ]   loaded content 可能引向更多 lookup
```

**LLM 应用栈里到处都是这个 pattern**：

| 实例 | Index 在哪 | Lookup tool | Content 进哪 |
|---|---|---|---|
| **Skill** | catalog 在 system prompt | `LoadSkill(name)` | tool_result |
| **MCP** | tool 列表在 messages_request.tools | `tools/call` JSON-RPC | tool_result |
| **RAG** | chunk metadata / vector index | `RetrieveDocs(query)` | tool_result |
| **Memory** | `MEMORY.md` 索引在 system prompt | LLM `Read(memory/x.md)` | tool_result |
| **Code nav** | dir listing | `Read` / `Grep` | tool_result |
| **Docs lookup** | TOC / 章节名 | `FetchSection(id)` | tool_result |
| **Sub-agent** | sub-agent 名册 + 能力描述 | `Task(agent, input)` | tool_result |

**全是同一个 pattern**：小尺寸索引展示存在性 + LLM 用 tool 调用拉详情。

→ 框架要把**一件事**做对：**tool 调用 = LLM 递归 context 扩展原语**。

→ 此事做对后，skill / MCP / RAG / memory 都是**应用层 convention**，不是新机制。

### system prompt vs messages 的分工

| 装哪里 | 性质 | 装什么 | 为什么 |
|---|---|---|---|
| **system prompt** | sticky，每轮都看 | catalog（小，频繁参考） | LLM 每次决策都要"知道有什么 skill 可用" |
| **messages[].content (tool_result)** | dynamic，append-only，subject to compaction | skill body（大，用完可能过气） | 只在被引用的几轮起作用，过气后 Phase 4 Layer 1 可截 |

这个 split 不是为 skill 设计——是 LLM stateless 协议**天然就这么 split**：
system prompt 是契约层（agent 的"操作系统"），messages 是工作内存（agent
的"堆"）。Skills 复用同一个 split。

### Agent loop 的本质 = 递归 context 扩展

```
agent loop = [LLM 决策 → tool 调用 → tool_result 进 messages → 下一轮 LLM]
              ↑                                                    │
              └────────── 直到 stop_reason ≠ tool_use ─────────────┘
```

每次 tool 调用都是 LLM **主动扩展自己的 context**。Skill 加载是这个 loop
的一种 use case，**不是新的 loop**。

这就是为什么 `learnings/phase-1-and-2.md` §6 **LLM-as-RPC-client framing**
是 Phase 5+ 的灵魂——LLM 是个不断发 RPC 的 client，agent loop 是它的
event loop，tool 调用就是 RPC call。Skill / MCP / RAG / memory 是不同
endpoint 的 RPC。

---

## Tentative Decisions（Phase 5c 入口时正式锁定到 boundary doc）

| ID | 决策 | Tentative |
|---|---|---|
| **L1** Skill 文件格式 | markdown + YAML frontmatter（同 Claude Code 风格、test-gen-agent 风格） |
| **L2** Storage 层级 | `~/.openharness/skills/` 全局 + `<project>/.openharness/skills/` 项目；同名 project 覆盖 global（类比 git config） |
| **L3** Catalog 注入策略 | **always-on**（每次 CLI 启动扫一遍，name + description 拼进 system prompt）；不做 LLM-gated catalog filtering |
| **L4** Body 加载策略 | **lazy via `LoadSkill(name)` tool**——不进 system prompt，按需 tool_result 拉 |
| **L5** `LoadSkill.is_read_only` | `True`（读 markdown 是 read-only，permission Tier 3 走 lax 路径） |
| **L6** Frontmatter schema | `name`（必）+ `description`（必）+ `version`（可选）；其余字段忽略 |
| **L7** Skill body 大小限制 | 不在 Skill 层限制；Phase 4 compaction Layer 1 已经在 tool_result 级处理超大 output |

**L3 / L4 是核心决策**——决定了"什么进 system prompt / 什么进 messages"。
推论：

- L3 决定 catalog 是 **bootstrap-time discovered**（启动时定）——和 Phase 5 MCP catalog 同生命周期
- L4 决定 body 是 **on-demand**——LLM 决定何时 pull，不是框架硬塞

---

## Cross-cutting Invariant（Phase 5 / Phase 6 invariant 的第三次兑现）

**Phase 5c 必须不增加新的 dispatch path**：

- `permissions/checker.py` — 零改动（LoadSkill 是普通 BaseTool，Tier 3 read-only 自动 ALLOW）
- `hooks/executor.py` — 零改动（PreToolUse/PostToolUse 透明 fire）
- `engine/query.py` — 零改动（dispatch loop 调 `tool.call()`）
- `observability/logging.py` — 零改动（`tool_dispatch` 自动 cover LoadSkill 调用）

允许动的：

- `tools/load_skill.py`（新文件，BaseTool 子类）
- `skills/`（新包：discovery + parse + dataclass）
- `prompts.py`（catalog 拼接）
- `cli.py`（启动接线：扫 skill → 注册 LoadSkill → 拼 catalog → 进 system prompt）
- `config/settings.py`（可能加 `skills_dir` 字段；倾向不加，约定优于配置）

---

## 几个生效后的下游可能性（Phase 5c 外）

- **Skill ↔ ModeBundle 接口**（Phase 5b slash command）：slash command 可以 frontmatter 声明 `extra_skills: [list]`，切 mode 时把 skill 子集临时纳入 catalog
- **Skill ↔ MCP** 互补：MCP tool（`GitHub.CreateIssue`）+ 对应 skill（"什么时候 CreateIssue、issue body 怎么写"）= 完整 capability bundle
- **Skill 作为子 agent 的 prompt**（Phase 6+）：子 agent 启动时强制载入特定 skill，等价于"给这个 sub-agent 一个专家 persona"

---

## Out of Scope（明确不做，写清为什么）

- **LLM-gated catalog filtering**（preview 时 LLM 看 desc 选哪些进 catalog） — L3 选 always-on 因为 catalog 小（name+desc 每个 < 50 tokens，100 个 skill 也才 5K tokens），不值得拐弯
- **关键词/正则 matching 自动 inject skill body** — 脆弱，违背 LLM-as-RPC framing（让 LLM 决策，不要框架替它决策）
- **Skill versioning / dependency resolution** — 不是 npm，skills 是 markdown 文档，version 字段只供记录
- **Skill 编辑/创建 tool**（如 `CreateSkill(...)`） — 由用户手工 / 通过 `Write` tool 完成，不需要专门 tool
- **Vector embedding / semantic search over skills** — 当 skill > 50 个再考虑；那时是 RAG-over-skills，不是 skill 自己的事

---

## Capabilities Sketch

```
P5c-T1  skills/ 包基座 — Skill dataclass + frontmatter parse + 文件 discovery
P5c-T2  LoadSkill BaseTool — 读 markdown、strip frontmatter、返回 ToolResult
P5c-T3  prompts.py catalog 注入 + cli.py 启动接线
P5c-T4  端到端 smoke + invariant verification（permission/hook/engine zero diff）
P5c-T5  Coverage + retro（learnings/phase-5c-skills.md）
```

预算：**3-5 天**，比 Phase 5 MCP 小一个 order——因为机制全是复用的。

---

## 一句话

> **Skills 不是"加新能力"，是 LLM-as-RPC + tool dispatch pattern 应用到
> "外部知识懒加载"这个 use case 上的一次 convention**。
>
> 框架真正要做对的是 Phase 1-4 已经做过的事——tool dispatch + 横切配套。
> Skills、MCP、RAG、Memory、Sub-agent 都是这同一台图灵机的不同 input。
>
> 这条 invariant 第三次兑现后，未来加任何外部 lookup（文档检索 / API
> gateway / cross-session memory / RAG）都是同一形态：写 1 个 BaseTool，
> 把 index 拼进 system prompt，content 透过 tool_result 进 messages。
> 零摩擦。
