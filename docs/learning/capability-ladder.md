# Capability Ladder — 我们在哪儿，要去哪儿

> 这份文档不是 SPEC（合同），不是 ARCHITECTURE（模块战略），也不是 plan（任务清单）。
> 它是一张**能力坐标图**——把"我们要做的事"从**模块视角**换成**用户体验视角**：
> 每多走一级，harness 多了一项 Claude Code 才有的本事。
>
> 配套阅读：
> - [SPEC.md](../../SPEC.md) — 项目契约（做什么 / 不做什么 / 行为）
> - [ARCHITECTURE.md](../../ARCHITECTURE.md) — 模块战略（Tier / Phase / 依赖图）
> - [tasks/phase-1-todo.md](../../tasks/phase-1-todo.md) — 当前阶段任务

---

## 1. 这个项目的两个边界

**起点（L0）**：你向大模型 API 发一次 HTTP——prompt 进去，文本出来。**一次性**、**无状态**、**没有手脚**。
→ 这是 OpenAI Playground 给所有人的能力，没人需要 harness。

**终点（L9）**：你日常用的 Claude Code——能调工具读你电脑里的文件、能记住你跨 session 的偏好、能花十分钟自己跑测试改代码报告进度。
→ 它和 L0 之间隔着的全部东西，就是**这个项目要逐级补上的**。

---

## 2. 能力阶梯（一图全览）

```
L9  长任务 / 子 agent / 后台执行            ← 终点（Claude Code 那种全自主）
    ↑
L8  跨会话记忆（关掉再开还认识你）
    ↑
L7  扩展性（MCP / 斜杠命令 / Skills）
    ↑
L6  长上下文管理（自动压缩，对话不爆）
    ↑
L5  权限与生产级硬化（工具调用前可拦截 / 审计 / 重试）
    ↑
L4  Agent Loop（模型连续多次调工具完成任务）  ← harness 的"心脏"
    ↑
L3  单次工具调用（模型说"我要 read 文件"，harness 执行后回填）
    ↑
L2  多轮对话（harness 维护对话历史）
    ↑
L1  流式输出（边生成边显示，不卡）
    ↑
L0  单次问答（一次 HTTP，prompt → 文本）     ← 起点
```

每爬一级，**harness 不只是"代码多了一些"——是用户能做的事多了一类**。

---

## 3. 每一级在解决什么

### L0 — 单次问答

|  |  |
|--|--|
| **用户痛点** | 我想用 LLM，但只能去 Playground 复制粘贴 |
| **Claude Code 对应** | （Claude Code 内部不直接暴露这一级，但它是最小内核） |
| **项目 Module** | 协议（Pydantic Request/Response）+ API client + 最小 CLI |
| **Phase** | Phase 1 前半 |
| **示例** | `oh ask "hi"` 一次性返回完整文本 |

**学到的工程**：Pydantic v2 / discriminated union / async / httpx / Typer。

---

### L1 — 流式输出

|  |  |
|--|--|
| **用户痛点** | 等 3 秒才看到任何东西，体验像死机 |
| **Claude Code 对应** | 你看到 Claude 一行行把字往外吐 |
| **项目 Module** | StreamEvent 抽象 + AsyncIterator + Rich 渲染 |
| **Phase** | Phase 1 后半 |
| **示例** | `oh ask "写首诗"` 文字流式打印 |

**关键认知**：流式不只是 UX——它是后续 agent loop 的**信号通道**（`stop_reason` / `tool_use` 都是流式事件）。L1 设计错了，L4 会重写。

---

### L2 — 多轮对话

|  |  |
|--|--|
| **用户痛点** | 每次问问题模型都不记得上次说了什么 |
| **Claude Code 对应** | 一个 session 内连续追问，上下文累积 |
| **项目 Module** | Engine 第一版 + `list[ConversationMessage]` 状态管理 |
| **Phase** | Phase 2 前半 |
| **示例** | `oh chat`（REPL）连续问答 |

**关键认知**：harness 开始有自己的"状态"——history 是 harness 拿在手里的，不是 Anthropic 给的。**这是 harness ≠ SDK 的第一个分水岭。**

---

### L3 — 单次工具调用

|  |  |
|--|--|
| **用户痛点** | LLM 永远在"说话"，从来不"动手" |
| **Claude Code 对应** | 你说"读一下 README"，Claude 真的去读了 |
| **项目 Module** | `BaseTool` + `ToolRegistry` + Read/Write/Bash/Grep/Edit |
| **Phase** | Phase 2 中段 |
| **示例** | `oh ask "list files in /tmp"` → 模型决定 → harness 执行 → 一次回填 |

**关键认知**：工具的输入是 LLM 写的（不可信），所以**输入校验 = Pydantic schema** 是工程级安全门。这是"为什么 tool 必须用 Pydantic"的真正原因。

---

### L4 — Agent Loop（这个项目的"心脏"）

|  |  |
|--|--|
| **用户痛点** | 一个任务要 5 步工具调用，不能每步都让用户 confirm |
| **Claude Code 对应** | "帮我把 cli.py 里的 print 改成 logger" → Claude 自己 read / edit / run test 走完整个链条 |
| **项目 Module** | `run_query()` async generator / `stop_reason` 驱动 / 工具结果回填 |
| **Phase** | Phase 2 末段 |
| **示例** | `oh ask "把 README 里的 TODO 列出来并写入 todo.md"` |

**这一级是 harness 之所以叫 harness 的核心**——L0-L3 任何 SDK 都能给；L4 是 Claude Code / Cursor / Aider 们各自做出**风格差异**的地方。

**关键决策**（届时讨论）：循环退出策略——`stop_reason` 驱动 vs step counter vs 混合？

---

### L5 — 权限与生产级硬化

|  |  |
|--|--|
| **用户痛点** | LLM 可能 `rm -rf /`；可能在我没看到的时候改了我的代码 |
| **Claude Code 对应** | 你看到的 "Allow / Deny / Always allow" 弹窗，背后是 9 步评估算法 |
| **项目 Module** | 完整权限算法 + Hooks 生命周期 + 异常层级 + 重试 backoff |
| **Phase** | Phase 3 |
| **示例** | `oh ask "rm /tmp/foo"` → harness 拦下来问你 |

**关键认知**：权限不是"加一个 if"——它是**贯穿 agent loop 每一次工具调用的横切关注点**，必须设计在循环骨架里。

---

### L6 — 长上下文管理

|  |  |
|--|--|
| **用户痛点** | 聊半小时后报错 "context window exceeded" |
| **Claude Code 对应** | Claude Code 跑 100 次 tool 调用都不爆——背后是 Microcompact / Auto-Compaction |
| **项目 Module** | Microcompact + Boundary Detection + 系统提示词组装 + cost tracker |
| **Phase** | Phase 4 |
| **示例** | 50 轮对话后，旧的 tool_result 自动被压成摘要 |

**关键决策**（届时讨论）：压缩策略——三级窗口 vs 单级 vs 卸载到文件（Manus 风格）？

---

### L7 — 扩展性

|  |  |
|--|--|
| **用户痛点** | harness 内置工具不够，我想接自己的 / 团队的 / 业界标准的 |
| **Claude Code 对应** | MCP server / Skills / 斜杠命令——你今天用的整个生态 |
| **项目 Module** | MCP（stdio 优先）+ 斜杠命令 + Skills 或 Plugins（二选一） |
| **Phase** | Phase 5 |
| **示例** | `oh mcp add github` → LLM 能调 GitHub tools 了 |

**关键认知**：扩展性的关键不是"提供多少 hook"，而是**边界设计**——什么交给用户写，什么内置。

---

### L8 — 跨会话记忆

|  |  |
|--|--|
| **用户痛点** | 每次开新会话都要重新告诉模型我是谁、项目长什么样 |
| **Claude Code 对应** | `~/.claude/projects/.../memory/MEMORY.md`——你这次会话开头就被自动加载 |
| **项目 Module** | 记忆系统（YAML frontmatter + 简单检索） |
| **Phase** | Phase 6（候选） |
| **示例** | harness 记得"用户是 TS 背景，新学 Python" |

**关键认知**：记忆 ≠ 全局 RAG。它是有结构的、可被 LLM 精确读写的小型知识库。

---

### L9 — 长任务 / 子 Agent / 后台

|  |  |
|--|--|
| **用户痛点** | 任务跑 10 分钟，主对话就死锁了 |
| **Claude Code 对应** | `Task` tool 派一个子 agent / 后台跑 / Worktree 隔离 |
| **项目 Module** | 子 agent（in_process）+ Worktree + 后台执行 |
| **Phase** | Phase 6 选做 |
| **示例** | `oh ask "重构整个 protocols 模块"` → 派子 agent，主 loop 不阻塞 |

**这是 Claude Code 之所以是"现代 harness"的标志**。工程量大，我们做精简版（in_process subagent，不做 Bridge / Coordinator / Channels Bus）。

---

## 4. 当前位置（2026-04-28）

```
L9  ............................  ⬜
L8  ............................  ⬜
L7  ............................  ⬜
L6  ............................  ⬜
L5  ............................  ⬜
L4  ............................  ⬜
L3  ............................  ⬜
L2  ............................  ⬜
L1  ............................  ⬜
L0  协议层正在搭（M2 进行中）       🟡  ← 你在这里
```

**具体状态**：
- ✅ Module 1 项目脚手架（L0 的工程地基）
- 🟡 Module 2 协议数据模型（L0 的"语言"——Request / Response / ContentBlock）
- ⏸ Module 3 API client + 流式（落地 L0 → L1）
- ⏸ Module 4 CLI + Print 模式（让 L0 真的能用起来）

**Phase 1 验收 = 真的爬上 L1**：跑 `oh ask "hi"` 看到流式响应。

---

## 5. 这份文档怎么用

| 当你… | 看哪 |
|--|--|
| 想知道**总体往哪走** | 本文档 |
| 想知道**项目的合同/边界** | [SPEC.md](../../SPEC.md) |
| 想知道**模块依赖和顺序** | [ARCHITECTURE.md](../../ARCHITECTURE.md) |
| 想知道**这周做什么任务** | [tasks/phase-1-todo.md](../../tasks/phase-1-todo.md) |
| 想知道**为什么选 X 不选 Y** | [decisions/](../../decisions/) |

**视角差**：
- ARCHITECTURE 回答"代码怎么分层、谁依赖谁"——**做的人**的视角。
- 本文档回答"用户能做的事多了什么"——**用的人**的视角。
- 两者必须同时成立：每完成一个 ARCHITECTURE 里的 Module，就要能在本文档里把对应那一格涂成绿色。

---

## 6. 横跨多级的"暗线"

有些东西不是某一级专属，而是从 L0 一路演化到 L9：

| 暗线 | L0 是什么 | L9 是什么 |
|--|--|--|
| **State**（状态） | 无 | 多 agent 多 worktree 的全局状态树 |
| **System Prompt** | 一句话 / 没有 | 5-section 动态组装 + 运行时注入 |
| **可观测** | `print` | structlog + cost tracker + 事件追踪 |
| **测试** | 单元 | 单元 + 集成 + E2E 三层金字塔 |
| **错误处理** | `raise` | 类型化异常层级 + 重试 + 降级 |

每爬一级，回头看这些暗线**长出了哪一段**——这就是 `learnings/<NN>.md` 要记录的东西。

---

## 7. `protocols/` 文件 → 阶梯映射

L0-L9 是**用户视角**——能做什么；这一节是**文件视角**——`src/openharness/protocols/`
里每个文件**精确**地服务哪一级。

### 完整映射表

| 文件 | 服务的 L | 必须存在的理由 | 缺了它会怎样 |
|------|---------|--------------|------------|
| `_base.py` | **跨级**（地基） | 所有 Pydantic 模型的母版（`extra="forbid"` + `validate_assignment=True`） | 每个文件都要重复写 config，容易漏一个变成不一致 |
| `messages.py` | **L2** | typed message——多轮对话需要状态化的对话历史 | dict 操作到 L2 就 KeyError 满天飞 |
| `content.py` | **L3** | content 从 string 升级为 `list[ContentBlock]`——必须区分 4 种 block | 无法表示 tool_use / tool_result，L3 物理上不可能 |
| `usage.py` | **跨级**（observability） | token 计数；L0 就开始用，L6（压缩）依赖它做触发 | 看不到成本、不知道何时压缩 |
| `requests.py` | **L0 起步，L4 完整** | 横跨多级：L0 用 `model+max_tokens+messages`，L1 加 `stream`，L2 加 `system`，L4 加 `tools` | 没有它无法发出任何请求 |
| `tools.py` | **L4** | 描述工具给 LLM——L3→L4 的物理前提 | LLM 不知道有哪些工具，循环退化为 L0 |
| `stream_events.py`（待做 2f） | **L1 + L4** | L1 需要 text delta；L4 需要 `stop_reason` 和 tool_use 事件 | 既不能流式渲染，也不能让 agent loop 知道何时停 |

### 为什么"一次性建好 L0-L4 的所有形态"——而不是按级渐进？

新人会问："为什么 L0 时就要写 ContentBlock？我只要 `str` 不就够了？"

技术上的回答：

1. **Anthropic wire format 本身耦合**——`messages` 字段可能含 `tool_use` block。
   wire format 不会因为你"只做 L0"就给你简化的 schema。从 L0 第一次解析响应起，
   你就要能识别 ContentBlock。
2. **重构成本曲线陡峭**——L0 用 `messages: list[dict]`，L2 改成
   `list[ConversationMessage]`，L3 又改 message 的 content 字段类型……每次都要
   动所有 import。**一次性写好，所有下游模块只 import 一次稳定的类型。**
3. **测试金字塔需要稳定底座**——如果协议层每升一级都重写，所有依赖它的测试都
   破。L0 的协议测试要能在 L4 时仍然成立。

### 举一个具体例子：`requests.py` 一文件横跨 4 级

```python
class ApiMessageRequest(StrictModel):
    model: str                                  # L0 必须
    max_tokens: int = Field(gt=0)               # L0 必须
    system: str | None = None                   # L2 用上（system prompt）
    messages: list[ConversationMessage]         # L0 必须
    stream: bool = True                         # L1 默认开启
    tools: list[ToolSpec] | None = None         # L4 才会真正用
```

**6 个字段属于不同级**，但放在一个类里——因为 wire format 是统一的，下游
（API client / 引擎 / CLI）只想 import 一次。**这是"一次性构建"vs"渐进重构"的产品决策**。

---

### "_base.py 为什么需要？"——的真正回答

它不属于阶梯任何一格。它解决的是**横向**问题：

- 6 个协议文件**每个**都需要 `extra="forbid"` + `validate_assignment=True`
- 不抽出来：6 处重复代码，添加新文件时容易漏配置 → 不一致 → debug 噩梦
- 抽出来后：所有模型继承 `StrictModel`，配置自动一致

→ **这是 Pydantic 项目里 DRY 的标准模式**。看到 `_base.py` 的项目几乎都在做同样的事。

---

## 8. wire-level 契约：harness 与 LLM 之间的输入/输出规范

§7 解释了每个文件落在哪一级。这一节往上抬一级，回答：**这些 `protocols/`
文件作为一个整体在做什么？**

### 8.1 输入 / 输出的对称性

L0-L4 所有能力，归根到底都建立在两件事上：

- **输入规范（2e 完成）** —— `ApiMessageRequest` + `ToolSpec`：harness 能向
  LLM 发出的所有东西
- **输出规范（2f 完成）** —— `ApiStreamEvent` 三种事件
  （TextDelta / MessageComplete / Retry）：LLM 能向 harness 返回的所有东西

**把这两个加起来 = harness 和 LLM 之间的整套契约**。

L0-L4 的每一级能力，本质上都是"在这套契约的基础上加一种使用方式"——
L1 是"消费 TextDelta 流"，L2 是"把多个 ConversationMessage 串起来"，
L3 是"识别 ContentBlock 里的 ToolUseBlock"，L4 是"按 stop_reason 决定循环"。
**没有这个契约，L0-L4 任何一级都不存在**。

### 8.2 Anti-Corruption Layer 模式

```
       harness 这一侧                  LLM 这一侧

┌────────────────┐    2e (输入规范)   ┌─────────────────┐
│ 引擎 / CLI / UI │ ──── wire ───→     │                 │
│  说我们的语言    │                   │  说自己的语言    │
│                │                   │  (Anthropic /   │
│ 内部统一形态    │ ←─── wire ─────    │   OpenAI 等)    │
└────────────────┘    2f (输出规范)   └─────────────────┘
        ▲                                     ▲
        │                                     │
        └──── protocols/ 是边界翻译器 ────────┘
```

`protocols/` 是 **anti-corruption layer**：

- 上层（引擎 / CLI / UI）只跟 `protocols/` 说话——**不需要知道我们用的是
  Anthropic 还是 OpenAI**
- 下层（API client / SDK）负责把 SDK 的原生形态翻译成 `protocols/` 的形态
- **换 Provider 时，只动 API client 一层**，上面所有代码一行不改

这是软件工程经典模式（Eric Evans《Domain-Driven Design》），LLM 时代换了个皮。

### 8.3 选择的代价：LangChain 路线 vs 手写路线

> "在过去这里会用 LangChain。"

你的直觉对——LangChain 提供的恰恰就是这层抽象。我们选了不同的路线：

| 维度 | LangChain 路线 | 我们手写路线 |
|------|--------------|------------|
| **代码量** | 0 行（`from langchain_core.messages import HumanMessage` 拿来即用） | ~300 行 `protocols/`（手写 Pydantic 模型） |
| **抽象高度** | 在 SDK **之上**——`BaseMessage` / `AIMessage` 是 LangChain 的语言 | 在 SDK **同级**——`ConversationMessage` 紧贴 Anthropic wire format |
| **依赖** | LangChain 1 个包 → 间接拉 100+ 包 | 只有 `pydantic` |
| **类型安全** | 对 mypy strict 兼容历史性差（v0.x 漂移；v0.3 改善但仍非 strict-clean） | mypy --strict 100% 通过 |
| **debug 时看到什么** | LangChain 的抽象层（`Runnable.invoke()` / `BaseChatModel._generate()`）+ 真实 wire | 真实 wire（直接是 Anthropic API 的 JSON） |
| **学到的东西** | 学会"**怎么用** LangChain" | 学会"**怎么和 LLM 通信**"——可迁移到任何 framework |
| **跨 Provider 现成支持** | ✅ 几十个 Provider 开箱即用 | ❌ 我们要一个一个做（已锁：Anthropic + 后续 OpenAI 兼容） |
| **Long-term ownership** | 受 LangChain 路线图绑架（v0.3 → v0.4 不兼容真发生过） | 完全控制——LangChain 改 API 跟你无关 |

### 8.4 我们到底选了什么——精确表述

**我们在做"Anthropic 官方 SDK 同层级"的抽象，不是"LangChain 同层级"的抽象。**

具体说：

- Anthropic 官方 Python SDK 内部就是 Pydantic v2 模型（看 `anthropic._types.MessageParam` 就懂）
- 我们的 `protocols/` 几乎是 "**重新发明 Anthropic SDK 的 messages 部分**"
- 区别：我们的版本是**多 Provider 中性**——靠引擎的 `stop_reason`-driven loop
  在 `protocols/` 提供的事件上工作，而不是在 SDK 事件上

### 8.5 这个选择带来的三个后果

1. **学习深度** ↑↑ —— 写一遍 ContentBlock 比读 10 篇 LangChain 教程更懂。
   **这是做这个项目的核心目的之一**
2. **依赖复杂度** ↓↓ —— `uv.lock` 里只有 pydantic + httpx + anthropic SDK。
   `oh ask "hi"` 启动 < 200ms（vs LangChain 项目典型 800ms+）
3. **未来灵活度** —— 想换 GLM / Qwen / 本地 Ollama？只要它的 SDK 能映射到
   `ApiMessageRequest` / `ApiStreamEvent`，引擎一行不动

### 8.6 反过来：什么时候 LangChain 才对

不是说 LangChain 永远错。如果你做的是：

- **快速 prototype** 验证业务想法（不在乎学习深度 / 启动性能）
- **天然多 Provider** 场景（一个产品同时支持 5 家 LLM 切换）
- **复杂 workflow 编排**（需要 LangGraph 的图构造能力）

→ LangChain 仍然是合理选择。

但本项目的目标是"**精通 harness 实现 + 拥有可控的生产级 Python 代码**"——
这恰好是 LangChain **不能给你的东西**。所以 2e + 2f 这 300 行手写代码，
**是这个项目全部价值的一部分**——而不是 boilerplate。

### 8.7 一句话总结

> **2e + 2f 不是"两个普通模块"——它们一起定义了 harness 和 LLM 之间的整套
> wire 契约。一切上层能力都建立在这个契约之上；一切下层 Provider 都翻译成
> 这个契约。**
>
> LangChain 让你跳过这一步；我们选择不跳过——因为这一步本身就是 harness
> 工程师必须懂的东西。

---

## Modification log

- **2026-04-28 (c)** — 加 §8：wire-level 契约视角（2e+2f = 输入/输出对称
  规范 + ACL 模式 + LangChain 路线对比）。来自实践中的"啊哈"瞬间——意识到
  我们手写的 300 行 protocols/ 不是 boilerplate，是 harness 工程师的必修课。
- **2026-04-28 (b)** — 加 §7：`protocols/` 文件 → 阶梯映射；解释"为什么一次性建 L0-L4 的所有数据形态"。
- **2026-04-28 (a)** — 初版。从用户视角把项目目标拆成 L0→L9 十级能力阶梯，明确当前位置（M2 进行中，L0 接近完成）。
