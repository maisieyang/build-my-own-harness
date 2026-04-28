# OpenHarness 学习地图（Architecture Map）

> **本文档不是 `plan.md`，也不是 `SPEC.md`。** 它是 Phase 0 的产物——把
> [REFERENCE.md](./REFERENCE.md)（OpenHarness 完整逆向规格）拆成可学习、
> 可交付的层级，明确 scope，建立后续每个模块进入前的讨论框架。
>
> **文档分工**：
> - [SPEC.md](./SPEC.md) — 项目契约（做什么 / 不做什么 / 行为准则）
> - 本文档（ARCHITECTURE.md）— 战略地图（tier / phase / 依赖图）
> - [tasks/plan.md](./tasks/plan.md) + [tasks/todo.md](./tasks/todo.md) — 当前阶段任务
> - [REFERENCE.md](./REFERENCE.md) — OpenHarness 逆向规格（学习参考）
> - [decisions/](./decisions/) — 每个产品/工程决策的记录

---

## 1. 现实判断：SPEC ≠ 你要做的项目

REFERENCE.md 是 OpenHarness v0.1.7 的完整逆向规格——**36 模块 / 43+ 工具 / 23 Provider / 107+ 测试文件 / ~19,000 行测试代码**。这是有团队支撑、迭代多版本后的产物。

**你的真实约束**：
- 时间：2-3 个月
- 人力：1 人
- 标准：生产级（不是 demo）
- 主目标：精通 Python + 成为 harness 领域专家 + 训练产品判断力

**结论**：必须做"核心 + 选做"分层。一次性做 36 模块只会让所有模块都做不到生产级，反而违背学习目标。**REFERENCE.md 在这个项目里的角色是"参考实现"，不是"完成清单"。**

---

## 2. 四级 Tier 划分

### Tier 0 — Core（必做：构成"什么是 harness"的最小定义）

| 模块 | REFERENCE § | 核心价值 | Python 实战重点 |
|------|-----------|---------|---------------|
| 项目脚手架 | 38 | 生产级 Python 工程基线 | pyproject.toml / uv / ruff / mypy strict / pre-commit / CI |
| 协议与数据模型 | 36 + 5.4 | discriminated union、ContentBlock、Message | Pydantic v2 / typing / Annotated |
| API Provider 抽象 | 4 | 屏蔽不同 LLM API 差异 | Protocol / async / httpx |
| 流式事件 | 5.5 | StreamEvent → UI 解耦 | AsyncIterator / async generator |
| 引擎 + Agent Loop | 5 | `run_query()` 工具循环 = harness 的心脏 | async generator / 状态机 |
| 工具系统 | 6 | BaseTool / ToolRegistry / ToolResult | ABC / Pydantic schema / 动态注册 |
| 基础工具集（5 个） | 6 | Read / Write / Edit / Bash / Grep | 异步子进程 / 文件 I/O / 资源限制 |
| 权限系统（基础） | 8 | 工具调用前的安全门 | fnmatch / 状态机 |
| 配置系统 | 7 | 4 级优先级（CLI > ENV > File > Default） | XDG paths / 类型化配置 |
| 认证 | 22 | API Key 管理 | keyring / 文件权限 0600 |
| CLI 入口 | 35 | Typer + 多命令 | Typer / Rich |
| Print 模式 | 24 | text / json 输出 | Rich / Markdown 渲染 |

### Tier 1 — Production Hardening（让 Tier 0 真正"生产级"）

| 模块 | REFERENCE § | 核心价值 |
|------|-----------|---------|
| 重试 + Backoff | 4 | 指数退避 + jitter，区分可重试错误 |
| 异常层级 | 4.3 | `OpenHarnessApiError` 派生体系 |
| Auto-Compaction（Microcompact） | 5.3 + A.7 | 上下文窗口管理最简版（清旧工具输出） |
| 权限系统（完整） | 8 + A.2 | 9 步评估算法 + 敏感路径硬编码 |
| Hooks 生命周期 | 9 | 7 事件 × 4 类型，可在 Hook 层 block 工具 |
| 系统提示词组装 | 23 + A.1 | 5 section 提示词 + 运行时注入 |
| 测试体系 | 37 | pytest + pytest-asyncio + 覆盖率 + CI |
| 日志与可观测 | — | structlog / 事件追踪 / cost tracker |
| 打包发布 | 38 | hatchling + uv publish + install script |

### Tier 2 — Extensibility（harness 之所以叫 harness 的扩展点，选 2-3 个深做）

| 模块 | 推荐优先级 | 理由 |
|------|-----------|------|
| MCP 集成 | ⭐⭐⭐ | 行业事实标准，跨 harness 通用，简历加分 |
| 斜杠命令 | ⭐⭐⭐ | 用户体验关键，工程量小，体现 UX 判断 |
| Skills 或 Plugins（二选一） | ⭐⭐ | Skills 更轻、Plugins 更全；做一个就够展示扩展模式 |
| 记忆系统（基础） | ⭐⭐ | YAML frontmatter + 简单检索；体现 RAG 思想在 harness 里的落地 |
| 子 Agent / Worktree | ⭐ | "高级 harness"的标志，但复杂度大；推荐放 Phase 6 |

### Tier 3 — Advanced（视时间，1-2 个深做即可）

| 模块 | 学习价值 | 工程量 |
|------|---------|--------|
| Docker 沙箱 | 高（容器化 + 运行时隔离） | 中 |
| 后台任务 + Cron | 中（asyncio scheduler） | 中 |
| Full Auto-Compaction（LLM 摘要） | 高（prompt 工程 + 不变性约束） | 中 |
| 多 Provider（再加 2-3 个） | 中（接口稳定性验证） | 小 |

### Out of Scope（明确不做，并写清为什么）

| 模块 | 不做的原因 |
|------|-----------|
| React TUI（Ink） | 跨语言架构（Python 后端 + TS 前端）超出"精通 Python"目标；用 Rich/Textual 替代 |
| ohmo 独立 app | 应用层产品，不是 harness 核心抽象 |
| 多平台聊天网关（Slack/Telegram/Discord/Feishu） | 应用集成，非 harness 抽象 |
| Autopilot + Dashboard | 上层 workflow 产品 |
| 语音模式 / Vim 模式 | UX 边角，不影响 harness 骨架理解 |
| Swarm 4 种后端 | 子 Agent 选 in_process 一种即可 |
| 23 Provider | 选 2-3 种（Anthropic + 1 OpenAI 兼容 + 视情况一种本地） |
| Bridge / Coordinator / Channels Bus | Swarm 的高级特性，跟子 Agent 一起评估 |
| Personalization / Themes / Output Styles | 应用层定制，不是 harness 必需 |

---

## 3. 模块依赖图

```
                          ┌──[项目脚手架]──┐
                          │                │
                ┌─────────┴────────┐       │
                ▼                  ▼       │
           [配置系统]            [认证]    │
                │                  │       │
                └────────┬─────────┘       │
                         ▼                 │
                  [协议与数据模型]         │
                         │                 │
                         ▼                 │
              [API Provider 抽象]          │
                         │                 │
                         ▼                 │
                   [流式事件]              │
                         │                 │
                         ▼                 │
              ┌──[引擎 + Agent Loop]──┐    │
              │           │           │    │
              ▼           ▼           ▼    │
        [工具系统]  [权限系统]  [系统提示词]│
              │           │                │
              ▼           ▼                │
        [基础工具集]  [Hooks]              │
              │                            │
              ▼                            │
        [Auto-Compaction(Micro)]           │
              │                            │
              ▼                            │
         [CLI 入口] ◄────────────────[Print 模式]
              │
              ▼
       ★ Tier 0+1 完整端到端 ★    ← 这里是第一个"生产级里程碑"
              │
              ▼  （Tier 2/3 选做）
        ┌─────┼─────┬─────────────┐
        ▼     ▼     ▼             ▼
   [斜杠命令][MCP][Skills/插件][子Agent/沙箱/记忆]
```

---

## 4. 推荐 Phase 顺序（垂直切片，每 Phase 完成后项目都可运行）

### Phase 1（1-2 周）：Foundation + Hello LLM
**交付物**：`oh ask "hi"` → 接 OpenAI 兼容 LLM（Qwen via DashScope）→ 流式打印响应

**包含模块**：项目脚手架 / 协议数据模型 / OpenAI 兼容客户端（含 Anthropic↔OpenAI wire 翻译层）/ 流式事件 / 最小 CLI / Print 模式

**学到**：Python 工程基线、`async/AsyncIterator`、Pydantic v2、`Protocol`、CLI 模式、anti-corruption layer 实战

**关键产品决策点**：流式事件层级怎么设计？（参考 REFERENCE + LangChain + Vercel AI SDK，trade-off 表后你选）。**首个 Provider 的选型**：见 [decisions/03-api-client-strategy.md](./decisions/03-api-client-strategy.md)——选 Qwen via DashScope 作为 OpenAI-compatible 的首测目标，让 anti-corruption layer 第一天就被压力测试

---

### Phase 2（2-3 周）：Tool Loop（harness 的心脏）
**交付物**：`oh ask "list files in /tmp"` → LLM 调用 Read/Bash → 工具循环跑通；可处理多轮工具调用

**包含模块**：工具系统 / Read+Write+Edit+Bash+Grep / `run_query()` 循环 / 基础权限

**学到**：异步子进程、Pydantic 输入校验、工具循环设计、`stop_reason` 驱动

**关键产品决策点**：工具循环的退出策略？`stop_reason` 驱动 vs step counter vs 混合？

---

### Phase 3（1-2 周）：Safety + Production Hardening
**交付物**：完整权限算法 + Hooks + 异常体系 + 重试 + 第一版测试覆盖（>70%）

**包含模块**：完整权限（含敏感路径） / Hooks 生命周期 / 异常层级 / 重试 backoff / 测试体系 / 日志可观测

**学到**：状态机、可观测、`pytest-asyncio`、CI

**关键产品决策点**：权限粒度？硬编码敏感路径 vs 全用户配置 vs 混合？

---

### Phase 4（1-2 周）：Context Management
**交付物**：长对话不爆 token，Microcompact 自动触发，且能保持 `tool_use`/`tool_result` 配对完整性

**包含模块**：Microcompact / Boundary Detection / 系统提示词组装 / cost tracker

**学到**：上下文窗口管理、prompt 工程、不变性约束（tool 配对保护）

**关键产品决策点**：压缩策略？三级 vs 单级 vs 卸载到文件（Manus 风格）？

---

### Phase 5（1-2 周）：Extensibility 第一关
**交付物**：MCP 服务器接入（stdio）+ 斜杠命令 + Skills 或 Plugins（选一）

**包含模块**：MCP（stdio transport）/ 斜杠命令 / Skills 或 Plugins

**学到**：协议适配、动态注册、扩展点边界设计

**关键产品决策点**：扩展点的边界——什么交给用户写、什么内置？

---

### Phase 6（1-2 周）：选一个 Advanced 深做
**候选**：
- 子 Agent + Worktree 隔离（与 Claude Code "Task tool" 对齐，最像"现代 harness"）
- Docker 沙箱（云原生加分）
- 完整 Auto-Compaction（含 LLM 摘要）

**关键产品决策点**：哪个最匹配你想强化的方向？

---

### Phase 7（1 周）：打磨与发布
- README + 用户版 ARCHITECTURE + 教程
- 打包发布（TestPyPI → PyPI）
- 性能与覆盖率收尾
- 复盘文章："我从 0 构建生产级 harness 的 7 个 Phase"——这本身就是产品工程师的对外作品

---

## 5. 模块进入框架（Three-Axis Discussion Template）

每进入一个新模块前，我们按这个模板讨论：

```
### 模块名

#### 1. 领域问题（What & Why）
- harness 里这块解决什么？
- Claude Code / LangChain / Codex / Manus 等业界产品怎么做？
- 不做这块会怎样？

#### 2. 产品决策（Trade-off 矩阵）
- 关键决策点 1：A vs B vs C
  - A：xxx — 谁在用 — 适合 / 不适合
  - B：xxx — 谁在用 — 适合 / 不适合
  - C：xxx — 谁在用 — 适合 / 不适合
- 你的选择 + 理由（你写）
- 关键决策点 2、3 ...

#### 3. 工程实现要点
- 涉及的 Python 生产级实践（typing / async / 测试 / 性能 / 可观测）
- 关键算法 / 数据结构
- 验收标准（怎么知道做完了）

#### 4. Mini-Plan
- 任务拆解（每个任务 ≤ 0.5 天）
- 依赖顺序
- 测试检查点
```

---

## 6. 学习沉淀机制

每完成一个模块，写 `learnings/<module>.md`：

```markdown
## 这个模块解决了什么 harness 问题
（一句话）

## 我做了哪些产品决策
- 决策 1：选了 X 而不是 Y。理由：...。如果是 Z 场景我会改选 Y。
- 决策 2：...

## Python 上学到的
- 模式 1：（代码片段）
- 模式 2：...

## 如果重做我会改什么
（让未来的你看到这个模块时，知道当时的局限）
```

这些文件就是"成为 harness 领域专家 + 产品工程师"的对外证据。

---

## 7. 下一步

**当前位置**：Phase 0 完成（架构地图就绪）

**等你确认 4 件事**：
1. **Tier 划分**是否合理？有没有该上 / 该下的模块？
2. **Phase 顺序**是否符合你的节奏？特别是 Phase 6 想做哪个 Advanced？
3. **Out of Scope 列表**你认可吗？（最大的 cut 是 React TUI 和 ohmo——确认后不会再回来）
4. **时间预算** 2-3 个月按 7 个 Phase 算大致够，但每 Phase 上下浮动 1 周是常态——你接受吗？

**你确认后**：进入 Phase 1。届时按 Three-Axis 模板对 Phase 1 内的具体模块出 mini-plan，写入 `tasks/phase-1-plan.md` + `tasks/phase-1-todo.md`，然后开始动手。
·
