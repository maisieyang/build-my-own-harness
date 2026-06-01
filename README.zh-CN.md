# OpenHarness — 生产级 Python LLM Agent Harness

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

**OpenHarness 是一个生产级的 Python LLM Agent Harness：你给它一个 prompt，它驱动 LLM 选择工具、安全地执行、循环直到任务完成。**

它由单人（一名开发者 + Claude Code 作为协作者）在 23 天内、跨 17 个 phase 从零构建。除了能跑的运行时之外，它同时是一份**框架设计的案例研究**——仓库完整保留了每个边界文档（`decisions/`）、每个阶段的复盘（`learnings/`）和完整的计划/执行轨迹（`tasks/`），让你不仅能看到“做了什么”，还能看到“每个权衡为什么这么做”。

---

## 我做了什么（核心能力）

- **流式工具循环** —— 引擎核心。`run_query()` 是一个 `AsyncIterator`，由 LLM 通过 `tool_use` 块驱动，harness 调度工具、回填结果，循环直到 `end_turn`。
- **5 个内置工具** —— Read / Write / Edit / Bash / Grep，输入经 Pydantic 校验，输出为结构化 `ToolResult`。
- **三级权限系统** —— 硬编码敏感路径拒绝 + 用户可配置的 glob 拒绝 + 权限模式覆盖（`--auto` / `--dry-run`）。
- **Hook 中间件** —— 5 个生命周期事件（PreToolUse / PostToolUse / PreApiCall / PostApiCall / OnError），支持拒绝 / 修改 / 放行语义。
- **结构化可观测性** —— JSON 日志绑定 `run_id` / `turn_id` / `agent_depth`，可用 `jq` 重建调用链。
- **MCP 集成** —— 通过 Model Context Protocol（stdio）注册第三方工具服务器。
- **Slash 命令 / Skills** —— Markdown 定义的自定义命令；Skills 按需懒加载，LLM 看到目录后再展开。
- **子 Agent 递归调度** —— 带深度上限的 `SpawnAgent` 工具，子 Agent 以不可变方式继承上下文。
- **沙箱执行** —— 通过 `--sandbox` 在 Docker 中运行，可选 runc / gVisor(runsc) 运行时。
- **多轮 REPL + 上下文压缩** —— `oh chat` 跨轮累积对话；两层压缩策略控制上下文长度。

---

## 成果一览

| 指标 | 数据 |
|------|------|
| 构建周期 | 23 天（单人 + Claude Code 协作） |
| 交付阶段 | 17 个 phase（边界文档 → 计划 → 执行 → 复盘） |
| 子系统 | `src/openharness/` 下 18 个 |
| 决策记录 | `decisions/` 中 24 份（逐权衡说明） |
| 复盘与随笔 | `learnings/` 中 31 份（18 份逐阶段 + 框架随笔） |
| 测试 | CI 上 1274 个通过，覆盖率 95.33%（Python 3.11），mypy strict、ruff clean |
| 代码量 | ~10,800 行生产代码 / ~21,600 行测试 |
| 提交数 | 195 |

---

## 快速开始

需要 Python ≥ 3.10 和 [uv](https://github.com/astral-sh/uv)。

```bash
# 1. 安装 uv（一次性）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 克隆并同步依赖
git clone https://github.com/maisieyang/build-my-own-harness.git
cd build-my-own-harness
uv sync

# 3. 配置 Provider（任何 OpenAI 兼容端点均可：
#    默认测试目标是 DashScope 上的 Qwen；
#    换 base_url 即可切到 OpenAI / DeepSeek / Moonshot 等）
cp .env.example .env
\$EDITOR .env   # 填入 OPENHARNESS_API_KEY

# 4. 开始提问
uv run oh ask "list 5 git commands"

# 或开启多轮 REPL：
uv run oh chat
```

**错误是分类返回的**——默认模式下不会抛 Python traceback：

| 情况 | 你会看到 |
|------|----------|
| 未设置 `OPENHARNESS_API_KEY` | 配置错误 + 提示 |
| Key 错误 | 认证失败（HTTP 401） |
| Provider 限流 | 重试后仍被限流（HTTP 429） |
| 循环触达 `max_turns` | 循环错误：触达轮次上限（N）；调高 `--max-turns` 或简化 prompt |

---

## 核心能力详解

每一项都链接到 `docs/development-log.md` 中对应阶段的开发叙事，以及 `learnings/phase-*.md` 的构建者复盘。

- **流式工具循环** —— 引擎的心脏。`run_query()` 由 LLM 发出的 `tool_use` 块驱动，harness 调度工具、回填结果，循环直到 `end_turn`。
- **三级权限系统** —— 硬编码敏感路径拒绝、用户可配 glob 拒绝（`OPENHARNESS_DENY_PATHS`）、权限模式覆盖。
- **Hook 中间件** —— 内部用于自动截断，对外暴露给用户插件（entry points + 文件系统 `*.py`）。
- **ModeBundle** —— 把系统提示词 + 工具白名单 + 额外拒绝路径 + 命名 hook 组合成一个具名 “mode”，由 slash 命令的 `mode:` frontmatter 引用。
- **插件 Hook** —— 第三方 Python 包可通过 `openharness.hooks` entry-point 提供 hook；放在 `~/.openharness/hooks/` 的 `.py` 文件也会被发现。通过 `--enable-plugin-hooks` 开启。
- **沙箱执行** —— Docker 沙箱（Phase 7b），可选运行时 `--sandbox-runtime runc|runsc`（Phase 7c gVisor 支持）。
- **上下文压缩** —— Layer 1 通过 hook 对单个工具结果截断；Layer 2 在 `PromptTooLong` 时丢弃最旧的工具调用/结果对并重试。

---

## 架构总览

三层关注点，按 phase 纵向切分：

- **Engine（`engine/`）** —— `run_query` 是流式产出 `ApiStreamEvent` 的异步生成器。每轮：发送消息 → 处理 `tool_use` 停止原因 → 调度工具 → 追加结果 → 循环至 `end_turn`。防御性不可变：调用方的 `initial_messages` 永不被改动。
- **Tools（`tools/`）** —— `BaseTool` 抽象基类 + Pydantic 输入 schema。`ToolRegistry` 是引擎可自省的目录。权限检查在调度**之前**通过 `permissions/checker.py` 完成。
- **Hooks（`hooks/`）** —— 5 个生命周期事件的中间件链，可拒绝 / 修改 / 观察。Hook 链是核心扩展点：Phase 4 的压缩、Phase 5d 的 bundle、Phase 5e/5f 的插件都挂在它上面。

完整的分层划分、依赖图和设计理由见 `ARCHITECTURE.md`；逐决策的权衡分析见 `decisions/`；构建者复盘见 `learnings/`。

### 项目结构

```
.
├── SPEC.md              # 项目契约（目标 / 命令 / 边界）
├── ARCHITECTURE.md      # 多阶段策略（分层、依赖图）
├── decisions/           # 24 份决策记录
├── learnings/           # 18 份逐阶段复盘 + 框架随笔（共 31 份）
├── tasks/               # 逐阶段边界文档 + 实现计划
├── src/openharness/     # 18 个子系统
│   ├── engine/          # run_query + 工具调度循环
│   ├── tools/           # 工具注册表 + 5 个内置工具
│   ├── hooks/           # 中间件（5 个事件）
│   ├── permissions/     # 三级授权检查器
│   ├── execution/       # 沙箱抽象 + Docker/gVisor
│   ├── mcp/             # Model Context Protocol 适配器
│   └── ...              # api / bundles / commands / skills 等
├── tests/               # ~1277 个测试，镜像 src/ 布局
└── .github/workflows/   # CI：lint + 类型检查 + 测试（Python 3.10/3.11）
```

---

## CLI 参考

```bash
# 单次查询
oh ask "<prompt>"
oh ask "<prompt>" --model qwen-max      # 覆盖模型
oh ask "<prompt>" --dry-run             # 只列出工具调用，不执行
oh ask "<prompt>" --auto                # 跳过权限确认
oh ask "<prompt>" --sandbox             # 在 Docker 容器内运行 Bash
oh ask "<prompt>" --sandbox --sandbox-runtime runsc   # gVisor 隔离

# 多轮交互
oh chat                                 # 内置 /exit /clear /help

# 自省框架
oh tools list                           # 内置工具
oh tools show Read                      # 名称、schema、是否只读、信任来源
oh config show                          # 生效配置（api_key 已脱敏）
oh hooks list                           # 内置 hook
```

## 配置

所有设置通过 `OPENHARNESS_` 前缀的环境变量读取（基于 pydantic-settings），模板见 `.env.example`。

| 环境变量 | 默认值 | 用途 |
|----------|--------|------|
| `OPENHARNESS_API_KEY` | （必填） | Provider API Key |
| `OPENHARNESS_BASE_URL` | （必填） | OpenAI 兼容端点 |
| `OPENHARNESS_MODEL` | `qwen-plus` | 默认模型 |
| `OPENHARNESS_PERMISSION_MODE` | `default` | default / auto / dry_run |
| `OPENHARNESS_MAX_AGENT_DEPTH` | `3` | 子 Agent 递归上限 |
| `OPENHARNESS_SANDBOX_RUNTIME` | `runc` | OCI 运行时（runc / runsc / ...） |

CLI 参数总是覆盖环境变量；环境变量总是覆盖默认值。完整清单见 `.env.example`。

---

## 这个项目是怎么构建的（给好奇的读者）

OpenHarness 不只是能跑的代码，更是一份**在自设的生产约束下做框架设计**的案例研究。如果你想要可直接套用的方法论提炼，先读 `PLAYBOOK.md`（约 6500 字中文）。

**方法论**：17 个 phase 都跑同一个四步循环——

1. **边界文档**（`decisions/`）：界定范围内/外，以及变更中必须守住的不变量。写代码前先锁定。
2. **计划**（`tasks/`）：带验收标准的能力清单，是“框架构建者（人）”与“实现者（Claude Code）”之间的契约。
3. **执行**：Claude Code 驱动子任务，人只在契约层评审，不看实现细节。
4. **复盘**（`learnings/`）：哪些抽象经受住了考验、哪些崩了、下一阶段该预测什么——在每个 phase 结束时写，而非项目结束时。

**5 条框架级经验（精简版）**：

1. 抽象优先会复利——正确形状的 Protocol 让 Phase 7c 仅用 7b 12% 的代码量就加上了 gVisor。
2. 分层模型能承载横切负载——ModeBundle 同时触达 4 层，11 个受保护目录零 diff。
3. 加法式 kwarg 是扩展稳定 API 的正确形状——默认值=旧行为，opt-in=新功能，旧测试逐字节通过。
4. 来源无关的目录是可扩展性的解锁点——第二个生产者仅花第一个 60% 的成本。
5. API 级零 diff 是重构的正确不变量——抽取共享模块后 233 个调用方测试不变；rule-of-three 是甜点而非更早。

从 `learnings/phase-7.md`（覆盖全部 17 个 phase 的元复盘）读起最佳。

---

## 开发

```bash
uv run ruff check && uv run ruff format   # Lint + 格式化
uv run mypy --strict src/                 # 类型检查（strict 模式）
uv run pytest                             # 测试 + 覆盖率
uv run pre-commit install                 # 首次克隆后安装 hook
```

CI 在 Python 3.10 / 3.11 上跑 lint + 类型检查 + 完整测试。集成测试在缺少环境变量 / Docker / gVisor 时自动跳过，`tests/` 在无外部依赖时始终干净通过。

---

## 致谢

本项目的名称与模块词汇承袭自 [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness)（MIT 许可）——最初的 Python LLM harness。本仓库（build-my-own-harness）是一份**独立、从零重写的学习产物**：不共享任何代码，实现细节频繁分歧，范围有意更窄（见 `SPEC.md §1` 与 `ARCHITECTURE.md`）。`REFERENCE.md` 记录了上游 v0.1.7 规范作为研究对象，而非复制来源。

## License

MIT，见 [LICENSE](LICENSE)。
