# OpenHarness 第一性原理拆解

> 这不是模块复盘，是**指南针**。当 REFERENCE.md 36 个模块/43+ 工具/23 Provider 的体量
> 让人迷路时，回到这里——harness 到底在做一件什么事。
>
> 来源：2026-05-07 与 Claude 的讨论（trigger："从第一性原理读 REFERENCE.md"）

---

## 0. 起点

LLM 只能"说话"——给它一段 prompt，它吐一段文本。要让它**做事**、**记得事**、**不出事**，
必须在它外面包一层。这层东西就叫 harness。

OpenHarness 是这层 harness 的完整 Python 实现。整个项目所有的复杂度，都是为了支撑或装饰
**一个核心循环**。

---

## 1. 核心循环：Agent Loop（一切的源头）

LLM 的输出里含 `tool_use` 块时还没"说完"，含 `end_turn` 才是说完。所以最底层的循环
只有一个判断：

```
while True:
    stream = llm.stream(messages)              # API 流式调用
    parse tool_use blocks                      # 解析动作
    for each tool_use:
        check permission                       # 拦
        execute tool                           # 做
        append tool_result to messages         # 喂回
    if stop_reason == "end_turn": break        # 终止条件
```

**这就是 `engine/query.py:run_query()` 的本质**。整个项目 36 个模块、43 个工具、23 个
Provider，全部都是给这个循环喂参数 / 装饰行为 / 提供基础设施。

记住这一句：**LLM 自己就是编排器，循环只负责把它的输出变成动作再喂回去。**

---

## 2. 围绕循环的四个抽象层

| 层 | 解决的问题 | OpenHarness 的实现 |
|---|---|---|
| **API 抽象** | LLM 是可替换的，但调用协议不一样 | 23 个 ProviderSpec + 4 种 Client（Anthropic / OpenAI-compat / Copilot / Codex），全部实现 `SupportsStreamingMessages` 协议，输出统一的 `ApiStreamEvent` |
| **工具抽象** | LLM 的"动作空间"必须可扩展 | `BaseTool` 基类 + `ToolRegistry`；MCP / 插件 / 技能 都通过 adapter 接入这个 registry |
| **拦截抽象** | 不能让 LLM 想干啥干啥 | `PermissionChecker`（敏感路径硬编码 → 黑/白名单 → 模式默认值）+ `HookExecutor`（7 个生命周期钩子） |
| **事件抽象** | 引擎 ≠ UI，要能插任意前端 | 引擎只产生 `StreamEvent` 异步流，UI（React TUI / Textual / print mode / ohmo 频道）只是 consumer |

**这四层是 harness 的"骨架"**——任何要做生产级 harness 的项目都绕不过去。少一层就少一个
能扩展的方向。

---

## 3. 加在循环之上的三套"长程能力"

LLM 的根本约束是 **context window 有限 + 跨会话失忆**。所以要在循环之外打三套补丁：

1. **Compaction（短期记忆挤压）**——3 级触发：
   - micro：清旧 tool 输出
   - session：确定性摘要
   - full：LLM 调用生成摘要
2. **Memory（长期记忆持久化）**——YAML frontmatter + 多语言语义检索，project / user 两级，
   每轮注入 system prompt
3. **Personalization（环境上下文自动提取）**——OS / Shell / Git / venv 自动塞进 prompt

这三套**不是**循环的一部分，是循环外面的"装饰"。它们让 harness 从"能跑一轮"变成
"能跑很久而不忘事"。

---

## 4. 当一个 Agent 不够时：水平扩展

单循环跑不动复杂任务，于是有 **Swarm**：

- **隔离**：Git worktree（文件系统）+ 子进程 / in-process / tmux / iTerm2（执行环境）
- **通信**：Mailbox（`.tmp` → rename 的原子文件队列，避免读到一半的消息）
- **协调**：Coordinator 模式 + TaskRegistry

`Agent` / `SendMessage` / `TeamCreate` 这些工具，本质是把 Agent Loop 自己变成一个工具——
**递归**。这就是为什么"子 Agent"在 harness 里是天然抽象，不是外挂功能。

---

## 5. 把循环包装成产品的外层

最外圈是把内核工程化的事，跟"什么是 harness"已经无关，跟"能不能交付给人用"有关：

- **CLI**（Typer，2400 行的 `cli.py`）
- **Auth**（多 Profile + ApiKey / DeviceCode / Browser 三种 flow）
- **Sandbox**（srt + Docker 双后端，opt-in）
- **Cron / BackgroundTask**（守护进程 + JSONL 历史）
- **Autopilot**（GitHub Issue → 排期 → 跑 Agent → 验证 → PR → CI 的完整流水线）
- **ohmo**（把 Agent 接到 Feishu / Slack / Telegram / Discord 频道）

**这些层 OpenHarness 做了，不代表我们必做**。我们的项目 ARCHITECTURE.md 已经把
React TUI / ohmo / Autopilot 划到 Out of Scope——这层东西每加一块都是"产品决策"，
不是"harness 本质"。

---

## 6. 一句话总结

> OpenHarness = **一个 `while stop_reason == "tool_use"` 循环** +
> **4 个抽象层（API / 工具 / 拦截 / 事件）** +
> **3 套长程记忆补丁（Compaction / Memory / Personalization）** +
> **多 Agent 水平扩展（Swarm）** +
> **产品化外壳（CLI / Auth / Sandbox / 服务）**。

读 REFERENCE.md 的某个具体模块时，先回来对一下：**它是骨架（§1-2）、长程能力（§3）、
水平扩展（§4），还是产品壳（§5）？** 这个判断决定它在我们项目里是必做、选做还是不做。

---

## 7. 与本项目 Phase 节奏的对应

详见 [tasks/plan.md](../tasks/plan.md) + [ARCHITECTURE.md](../ARCHITECTURE.md)。简表：

| 第一性原理层 | 本项目 Phase 落点 |
|---|---|
| §1 核心循环（Agent Loop） | Phase 2 — Tool Loop（harness 的心脏） |
| §2.1 API 抽象 | Phase 1 — T3 + T4（已完成 OpenAI-compat 通过 Qwen） |
| §2.2 工具抽象 | Phase 2 — Tool Loop |
| §2.3 拦截抽象（权限 + Hook） | Phase 3 — Safety + Production Hardening |
| §2.4 事件抽象（StreamEvent） | Phase 1 — T2 协议层 + T4 渲染（进行中） |
| §3.1 Compaction | Phase 4 — Context Management |
| §3.2 Memory | Tier 2，Phase 5+ 选做 |
| §3.3 Personalization | Out of scope |
| §4 多 Agent | Phase 6 候选 |
| §5 产品化外壳 | CLI 在 Phase 1；slash commands / MCP 在 Phase 5；sandbox 在 Phase 6 |
