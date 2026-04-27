# OpenHarness — 完整逆向工程规格文档

> 基于 OpenHarness v0.1.7 源码逆向分析生成。覆盖全部 36 个子模块、43+ 工具、23 个 Provider、ohmo 个人 Agent 及前端 TUI。
> 最后更新：2026-04-26

---

## 目录

1. [项目概述](#1-项目概述)
2. [项目结构](#2-项目结构)
3. [核心架构](#3-核心架构)
4. [API 模块](#4-api-模块)
5. [引擎模块](#5-引擎模块)
6. [工具模块](#6-工具模块)
7. [配置模块](#7-配置模块)
8. [权限模块](#8-权限模块)
9. [Hook 模块](#9-hook-模块)
10. [MCP 模块](#10-mcp-模块)
11. [记忆模块](#11-记忆模块)
12. [Swarm 多 Agent 模块](#12-swarm-多-agent-模块)
13. [Coordinator 模块](#13-coordinator-模块)
14. [Bridge 模块](#14-bridge-模块)
15. [任务模块](#15-任务模块)
16. [服务模块](#16-服务模块)
17. [插件模块](#17-插件模块)
18. [技能模块](#18-技能模块)
19. [命令模块](#19-命令模块)
20. [沙箱模块](#20-沙箱模块)
21. [频道模块](#21-频道模块)
22. [认证模块](#22-认证模块)
23. [提示词模块](#23-提示词模块)
24. [UI 模块](#24-ui-模块)
25. [React TUI 前端](#25-react-tui-前端)
26. [状态管理模块](#26-状态管理模块)
27. [主题与样式模块](#27-主题与样式模块)
28. [键绑定模块](#28-键绑定模块)
29. [个性化模块](#29-个性化模块)
30. [语音模块](#30-语音模块)
31. [Vim 模块](#31-vim-模块)
32. [Autopilot 模块](#32-autopilot-模块)
33. [ohmo 个人 Agent](#33-ohmo-个人-agent)
34. [Autopilot Dashboard](#34-autopilot-dashboard)
35. [CLI 接口](#35-cli-接口)
36. [协议与数据模型](#36-协议与数据模型)
37. [测试策略](#37-测试策略)
38. [构建与发布](#38-构建与发布)
39. [设计决策总览](#39-设计决策总览)
40. [与 Claude Code 的对比](#40-与-claude-code-的对比)

---

## 1. 项目概述

### 1.1 定位

OpenHarness 是 **Claude Code 的开源 Python 复刻版**，实现完整的 Agent Harness 模式——LLM 提供智能，Harness 提供手（工具）、眼（搜索/观察）、记忆（持久化）和安全边界（权限/沙箱）。

核心能力：

- 23 个 Provider 的 LLM 抽象层（Anthropic / OpenAI / Copilot / Codex / Moonshot / Gemini 等）
- 流式工具调用 Agent 循环引擎（streaming tool-call cycle）
- 43+ 内置工具（文件操作、Shell、搜索、Web、MCP、Agent 生成、定时任务等）
- 持久化记忆系统（YAML frontmatter + 多语言语义检索）
- 多 Agent 协作（Swarm 4 种后端 + Coordinator 模式 + Mailbox 通信）
- 插件 / 技能 / Hook 三层扩展体系（兼容 anthropics/skills 和 claude-code plugins）
- Docker / srt 双沙箱隔离
- React TUI 终端界面（React 18 + Ink 5，21 个 TSX 组件）
- ohmo 个人 Agent 应用（Feishu / Slack / Telegram / Discord 网关）
- Autopilot 自动化任务系统（GitHub Issue 扫描 → 排期 → 执行 → 验证 → PR → CI → 合并）

**目标用户**：AI Agent 研究者、开源开发者、需要可定制 CLI Agent 的工程团队。

### 1.2 技术栈

| 层级 | 技术选型 |
|------|----------|
| 语言 | Python ≥ 3.10（目标 3.11） |
| 构建 | hatchling + uv |
| 数据模型 | Pydantic ≥ 2.0 |
| CLI 框架 | Typer ≥ 0.12 |
| API 客户端 | anthropic ≥ 0.40, openai ≥ 1.0, httpx ≥ 0.27 |
| 终端 UI | React 18 + Ink 5（主 TUI）/ Textual ≥ 0.80（后备） |
| 协议 | MCP ≥ 1.0（Model Context Protocol） |
| 聊天集成 | slack-sdk, python-telegram-bot, discord.py, lark-oapi |
| 测试 | pytest ≥ 8.0 + pytest-asyncio + pytest-cov |
| 代码质量 | ruff ≥ 0.5（lint）, mypy ≥ 1.10（strict 模式） |

### 1.3 入口点

```
openharness = oh = openh → openharness.cli:app   # 主 CLI（三个别名）
ohmo                     → ohmo.cli:app           # 个人 Agent 应用
```

Windows PowerShell 中 `oh` 与 `Out-Host` 冲突，需使用 `openh`。

---

## 2. 项目结构

```
OpenHarness-main/
├── src/openharness/           # ← 核心引擎（36 个子模块）
│   ├── __init__.py
│   ├── __main__.py            # python -m openharness 入口
│   ├── cli.py                 # CLI 入口（Typer app，~2400 行）
│   ├── platforms.py           # 平台检测（OS / Shell / Terminal）
│   ├── api/                   # 多 Provider LLM 抽象层
│   │   ├── client.py          #   AnthropicApiClient
│   │   ├── openai_client.py   #   OpenAICompatibleClient
│   │   ├── copilot_client.py  #   CopilotClient（GitHub OAuth）
│   │   ├── codex_client.py    #   CodexApiClient（Codex 订阅）
│   │   ├── registry.py        #   23 个 ProviderSpec 注册表
│   │   ├── provider.py        #   Provider 检测与能力元数据
│   │   ├── usage.py           #   UsageSnapshot Token 追踪
│   │   └── errors.py          #   异常层级
│   ├── engine/                # 对话引擎（Agent Loop）
│   │   ├── query_engine.py    #   QueryEngine 高层管理
│   │   ├── query.py           #   run_query() 核心工具循环
│   │   ├── messages.py        #   ConversationMessage / ContentBlock
│   │   └── stream_events.py   #   StreamEvent 事件层级
│   ├── config/                # 配置管理
│   │   ├── settings.py        #   Settings / PermissionSettings / MemorySettings
│   │   ├── paths.py           #   XDG-like 路径解析
│   │   └── output_styles/     #   输出样式加载
│   ├── tools/                 # 43+ 内置工具
│   │   ├── base.py            #   BaseTool / ToolResult / ToolRegistry
│   │   ├── __init__.py        #   create_default_tool_registry()
│   │   ├── bash_tool.py       #   Shell 命令执行
│   │   ├── file_read_tool.py  #   文件读取
│   │   ├── file_write_tool.py #   文件写入
│   │   ├── file_edit_tool.py  #   文件编辑（精确替换）
│   │   ├── glob_tool.py       #   文件模式搜索
│   │   ├── grep_tool.py       #   内容搜索（ripgrep 封装）
│   │   ├── web_search_tool.py #   Web 搜索
│   │   ├── web_fetch_tool.py  #   URL 抓取
│   │   ├── agent_tool.py      #   子 Agent 生成
│   │   ├── send_message_tool.py #  Agent 间消息
│   │   ├── task_*.py          #   任务管理（Create/Get/List/Update/Stop/Output）
│   │   ├── cron_*.py          #   定时任务（Create/List/Delete/Toggle）
│   │   ├── mcp_tool.py        #   MCP 工具适配器
│   │   ├── skill_tool.py      #   技能调用
│   │   ├── notebook_edit_tool.py # Jupyter 编辑
│   │   ├── enter_plan_mode_tool.py / exit_plan_mode_tool.py
│   │   ├── enter_worktree_tool.py / exit_worktree_tool.py
│   │   ├── remote_trigger_tool.py
│   │   └── ...（共 42 个工具文件）
│   ├── auth/                  # 多流程认证
│   │   ├── manager.py         #   AuthManager（Profile 管理）
│   │   ├── storage.py         #   credentials.json（mode 600）
│   │   └── flows.py           #   ApiKeyFlow / DeviceCodeFlow / BrowserFlow
│   ├── permissions/           # 工具访问控制
│   │   └── checker.py         #   PermissionChecker
│   ├── hooks/                 # 生命周期事件
│   │   ├── schemas.py         #   HookDefinition（4 种类型）
│   │   ├── loader.py          #   HookRegistry
│   │   └── executor.py        #   HookExecutor
│   ├── mcp/                   # Model Context Protocol
│   │   ├── client.py          #   McpClientManager
│   │   └── types.py           #   McpServerConfig（stdio/HTTP/WS）
│   ├── memory/                # 持久化记忆
│   │   ├── manager.py         #   MemoryManager
│   │   ├── scan.py            #   MemoryScanner（frontmatter 解析）
│   │   ├── search.py          #   MemorySearch（多语言检索）
│   │   └── types.py           #   MemoryHeader
│   ├── swarm/                 # 多 Agent 团队
│   │   ├── registry.py        #   BackendRegistry（4 种后端）
│   │   ├── in_process.py      #   InProcessBackend
│   │   ├── subprocess_backend.py # SubprocessBackend
│   │   ├── mailbox.py         #   TeammateMailbox（文件级消息队列）
│   │   ├── permission_sync.py #   权限同步协议
│   │   ├── spawn_utils.py     #   环境变量继承
│   │   ├── worktree.py        #   Git Worktree 隔离
│   │   ├── lockfile.py        #   文件锁
│   │   └── types.py           #   TeammateSpawnConfig / SpawnResult
│   ├── coordinator/           # Coordinator 模式
│   │   ├── registry.py        #   TeamRegistry / TaskNotification
│   │   └── agent_definitions.py # WorkerConfig / 工具清单
│   ├── bridge/                # 隔离会话管理
│   │   └── manager.py         #   BridgeSessionManager
│   ├── tasks/                 # 后台任务管理
│   │   └── manager.py         #   BackgroundTaskManager（单例）
│   ├── services/              # 高层服务
│   │   ├── cron.py            #   Cron 作业注册表
│   │   ├── cron_scheduler.py  #   Cron 调度器守护进程
│   │   ├── session_storage.py #   会话快照存储
│   │   ├── session_backend.py #   SessionBackend 协议
│   │   └── compact/           #   上下文压缩
│   │       └── __init__.py    #   Microcompact / Session Memory / Full Compact
│   ├── plugins/               # 插件系统
│   │   ├── schemas.py         #   PluginManifest
│   │   ├── types.py           #   LoadedPlugin
│   │   └── loader.py          #   PluginLoader
│   ├── skills/                # 技能系统
│   │   ├── types.py           #   SkillDefinition
│   │   ├── registry.py        #   SkillRegistry
│   │   └── loader.py          #   SkillLoader
│   ├── commands/              # 斜杠命令
│   │   └── registry.py        #   CommandRegistry / SlashCommand
│   ├── prompts/               # 系统提示词
│   │   ├── system_prompt.py   #   build_system_prompt()
│   │   ├── context.py         #   build_runtime_system_prompt()
│   │   └── environment.py     #   环境检测注入
│   ├── sandbox/               # 沙箱隔离
│   │   ├── adapter.py         #   SandboxAdapter（srt CLI）
│   │   ├── docker_backend.py  #   DockerSandboxSession
│   │   └── path_validator.py  #   PathValidator
│   ├── channels/              # 多平台聊天集成
│   │   └── bus/               #   MessageBus / ChannelBridge
│   ├── ui/                    # UI 后端
│   │   ├── app.py             #   run_repl() / run_print_mode()
│   │   ├── protocol.py        #   BackendEvent / FrontendRequest
│   │   ├── backend_host.py    #   ReactBackendHost
│   │   ├── runtime.py         #   RuntimeBundle
│   │   ├── react_launcher.py  #   React 进程启动器
│   │   └── textual_app.py     #   Textual 后备 UI
│   ├── state/                 # 应用状态
│   │   ├── app_state.py       #   AppState dataclass
│   │   └── store.py           #   AppStateStore（观察者模式）
│   ├── themes/                # 主题系统
│   │   ├── schema.py          #   ThemeConfig
│   │   ├── loader.py          #   load_theme()
│   │   └── builtin.py         #   内置主题
│   ├── output_styles/         # 输出样式
│   │   └── loader.py          #   OutputStyle（default/minimal/codex）
│   ├── keybindings/           # 键绑定
│   │   ├── default_bindings.py
│   │   ├── loader.py
│   │   ├── parser.py
│   │   └── resolver.py
│   ├── personalization/       # 个性化
│   │   ├── extractor.py       #   环境事实自动提取
│   │   ├── rules.py           #   规则加载/合并
│   │   └── session_hook.py    #   会话后注入
│   ├── voice/                 # 语音
│   │   ├── voice_mode.py      #   VoiceDiagnostics
│   │   ├── keyterms.py        #   关键词检测
│   │   └── stream_stt.py      #   流式 STT
│   ├── vim/                   # Vim 模式
│   │   └── transitions.py     #   toggle_vim_mode()
│   └── autopilot/             # 自动化任务
│       ├── types.py           #   RepoTaskCard / RepoRunResult
│       └── service.py         #   Autopilot 编排
├── ohmo/                      # 个人 Agent 应用
│   ├── cli.py                 # ohmo CLI
│   ├── runtime.py             # 后端运行时
│   ├── workspace.py           # ~/.ohmo/ 工作空间
│   ├── memory.py              # 个人记忆
│   ├── prompts.py             # 系统提示词组装
│   ├── session_storage.py     # 会话持久化
│   └── gateway/               # 消息网关
│       ├── service.py         #   OhmoGatewayService
│       ├── runtime.py         #   OhmoSessionRuntimePool
│       ├── router.py          #   SessionRouter
│       ├── bridge.py          #   ChannelBridge
│       ├── config.py          #   网关配置加载
│       └── models.py          #   GatewayConfig / GatewayState
├── frontend/terminal/         # React TUI 前端
│   └── src/components/        #   21 个 TSX 组件
├── autopilot-dashboard/       # Autopilot 看板（React + Vite）
├── tests/                     # 107+ 测试文件
├── scripts/                   # 安装 & E2E 测试脚本
└── pyproject.toml             # 项目元数据
```

---

## 3. 核心架构

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                          UI 层                                   │
│  React TUI (Ink 5)  │  Textual TUI  │  Print Mode (text/json)  │
├─────────────────────────────────────────────────────────────────┤
│                         CLI 层                                   │
│  Typer App: oh [OPTIONS] COMMAND [ARGS]                         │
│  入口：openharness.cli:app / ohmo.cli:app                       │
├─────────────────────────────────────────────────────────────────┤
│                       Runtime 层                                 │
│  RuntimeBundle: API Client + MCP Manager + Tools + State +      │
│                 Hooks + Commands + Session Backend               │
├─────────────────────────────────────────────────────────────────┤
│                        引擎层                                    │
│  QueryEngine → run_query() 工具循环                              │
│  ├─ ConversationMessage 管理（messages 历史）                    │
│  ├─ StreamEvent 异步事件流                                      │
│  ├─ CostTracker 成本追踪                                        │
│  └─ Auto-Compaction 自动压缩                                    │
├─────────────────────────────────────────────────────────────────┤
│                     中间件 / 服务层                               │
│  Permissions │ Hooks │ Memory │ Services │ Tasks │ Personalization│
├─────────────────────────────────────────────────────────────────┤
│                        工具层                                    │
│  ToolRegistry → 43+ BaseTool 实现                               │
│  ├─ 文件: Read / Write / Edit / Glob / Grep                    │
│  ├─ 执行: Bash / LSP / NotebookEdit                            │
│  ├─ 搜索: WebSearch / WebFetch / ToolSearch                    │
│  ├─ Agent: AgentTool / SendMessage / TeamCreate / TeamDelete    │
│  ├─ 任务: TaskCreate / Update / List / Get / Stop / Output     │
│  ├─ 模式: EnterPlanMode / ExitPlanMode / Worktree              │
│  ├─ MCP: McpTool / ListMcpResources / ReadMcpResource          │
│  └─ 扩展: Skill / Config / Brief / Sleep / CronCreate / ...    │
├─────────────────────────────────────────────────────────────────┤
│                        API 层                                    │
│  AnthropicApiClient │ OpenAICompatClient │ CopilotClient │ Codex│
│  ProviderRegistry（23 个 ProviderSpec）                          │
├─────────────────────────────────────────────────────────────────┤
│                      基础设施层                                   │
│  Auth │ Sandbox │ MCP │ Channels │ Plugins │ State │ Platforms  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 核心数据流

```
用户输入（交互 / -p 管道 / ohmo 频道消息）
  │
  ▼
CLI 层解析参数 → 构建 RuntimeBundle
  │
  ▼
QueryEngine.submit_message(user_message)
  │
  ▼
run_query()  ← 核心工具循环（async generator）
  │
  ├──▶ [1] 构建 QueryContext（API client, tools, permissions, hooks, system_prompt）
  │
  ├──▶ [2] api_client.stream_message(request) → AsyncIterator[ApiStreamEvent]
  │         ├── ApiTextDeltaEvent     → 文本增量 → UI 实时渲染
  │         ├── ApiMessageCompleteEvent → 完成信号（含 usage + stop_reason）
  │         └── ApiRetryEvent         → 重试（429/500/502/503/529）
  │
  ├──▶ [3] 解析 response 中的 tool_use ContentBlock
  │
  ├──▶ [4] HookExecutor.fire(TOOL_EXECUTION_START, payload)
  │         └── Hook 可 block 工具执行
  │
  ├──▶ [5] PermissionChecker.evaluate(tool_name, is_read_only, file_path, command)
  │         ├── allowed → 继续
  │         ├── requires_confirmation → UI 弹出确认对话框
  │         └── denied → 跳过，返回 ToolResult(is_error=True)
  │
  ├──▶ [6] tool_registry.get(name).execute(args, ToolExecutionContext)
  │         └── 返回 ToolResult(output, is_error, metadata)
  │
  ├──▶ [7] HookExecutor.fire(TOOL_EXECUTION_END, payload)
  │
  ├──▶ [8] 将 ToolResultBlock 追加到 messages
  │
  ├──▶ [9] 检查 token 用量
  │         ├── 接近阈值 → 触发 Auto-Compaction
  │         │    ├── Microcompact（清除旧工具输出）
  │         │    ├── Session Memory（确定性摘要）
  │         │    └── Full Compact（LLM 调用生成摘要）
  │         └── 正常 → 继续
  │
  ├──▶ [10] 检查 stop_reason
  │          ├── "tool_use" → 回到步骤 [2]，继续循环
  │          └── "end_turn" → 退出循环
  │
  └──▶ [11] 发射 StreamEvent → UI 渲染
            ├── AssistantTextDelta(text)
            ├── ToolExecutionStarted(tool_name, tool_input)
            ├── ToolExecutionCompleted(tool_name, output, is_error)
            ├── CompactProgressEvent(phase, trigger)
            ├── ErrorEvent(message, recoverable)
            ├── StatusEvent(message)
            └── AssistantTurnComplete(message, usage)
```

---

## 4. API 模块

**路径**：`src/openharness/api/`
**职责**：多 Provider LLM 抽象层，统一不同 API 的调用接口。

### 4.1 Provider 注册表

```python
@dataclass(frozen=True)
class ProviderSpec:
    name: str                       # "openrouter"
    keywords: tuple[str, ...]       # ("openrouter",) — 用于模型名检测
    env_key: str                    # "OPENROUTER_API_KEY"
    display_name: str
    backend_type: str               # "anthropic" | "openai_compat" | "copilot" | "codex"
    default_base_url: str
    detect_by_key_prefix: str       # "sk-or-" — API Key 前缀检测
    is_gateway: bool                # 可路由任意模型
    is_local: bool                  # 本地部署
    is_oauth: bool                  # OAuth 认证
```

**23 个已注册 Provider**（按优先级排序）：

| 分类 | Provider |
|------|----------|
| OAuth | GitHub Copilot |
| 网关 | OpenRouter, AiHubMix, SiliconFlow, VolcEngine |
| 云服务 | Anthropic, OpenAI, DeepSeek, Gemini, DashScope (Qwen), Moonshot (Kimi), MiniMax, Zhipu AI (GLM), Groq, Mistral |
| 其他 | StepFun, Baidu (ERNIE), AWS Bedrock, Google Vertex AI |
| 本地 | Ollama, vLLM |

**Provider 检测链**：`detect_provider_from_registry()` → API Key 前缀 → base_url 关键词 → 模型名匹配。

### 4.2 API 客户端

所有客户端实现统一协议：

```python
class SupportsStreamingMessages(Protocol):
    async def stream_message(
        self, request: ApiMessageRequest
    ) -> AsyncIterator[ApiStreamEvent]
```

| 客户端 | 文件 | 说明 |
|--------|------|------|
| `AnthropicApiClient` | `client.py` | 原生 Anthropic SDK 封装，指数退避重试（3次，base 1s，max 30s，jitter 0.25） |
| `OpenAICompatibleClient` | `openai_client.py` | OpenAI 兼容 REST 客户端，消息/工具格式转换 |
| `CopilotClient` | `copilot_client.py` | GitHub Copilot OAuth 封装，委托给 OpenAICompatibleClient |
| `CodexApiClient` | `codex_client.py` | Codex 订阅客户端，SSE 流式解析，工具调用提取 |

**流式事件类型**：

```python
ApiStreamEvent:
  ├── ApiTextDeltaEvent(text: str)
  ├── ApiMessageCompleteEvent(message, usage: UsageSnapshot, stop_reason: str)
  └── ApiRetryEvent(attempt: int, delay: float, error: str)
```

**Token 追踪**：

```python
@dataclass
class UsageSnapshot:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int: ...
```

### 4.3 异常层级

```python
OpenHarnessApiError (base)
  ├── AuthenticationFailure    # 401/403
  ├── RateLimitFailure         # 429
  └── RequestFailure           # 其他 4xx/5xx
```

### 4.4 关键设计

- Claude OAuth 支持 `anthropic-beta` header + metadata（user_id, device_id, session_id）
- Token resolver 支持懒加载刷新（每次重试时重新解析）
- Anthropic → OpenAI 工具格式自动转换（`input_schema` → `parameters`）
- 所有 API 调用完全异步（`async/await`）

---

## 5. 引擎模块

**路径**：`src/openharness/engine/`
**职责**：核心对话引擎，管理消息历史和工具感知的模型循环。

### 5.1 QueryEngine

```python
class QueryEngine:
    """高层对话管理器"""
    # 拥有：
    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    permission_checker: PermissionChecker
    cost_tracker: CostTracker
    messages: list[ConversationMessage]

    # 关键方法：
    async def submit_message(user_text) -> AsyncIterator[StreamEvent]
    def clear()
    def set_system_prompt(prompt)
    # 属性：
    max_turns: int
    total_usage: UsageSnapshot
```

### 5.2 QueryContext

```python
@dataclass(frozen=True)
class QueryContext:
    """跨查询共享的不可变上下文"""
    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    permission_checker: PermissionChecker
    cwd: str
    model: str
    system_prompt: str
    max_tokens: int
    auto_compact_threshold: int
    # + 回调：permission_callback, user_prompt_callback
```

### 5.3 run_query() — 核心循环

`run_query()` 是整个系统的心脏——一个异步生成器，驱动"查询→流式→工具调用→结果→继续"的循环：

- **工具循环**：以 `stop_reason` 驱动（`"tool_use"` → 继续，`"end_turn"` → 停止）
- **Auto-Compaction**：当 token 接近阈值时自动触发（分三级：micro → session → full）
- **Reactive Compaction**：API 返回 "prompt too long" 错误时触发头部截断
- **Turn Limit**：防止无限循环
- **工具元数据**（`tool_metadata`）：跨 turn 持久化，追踪已读文件、技能、工作日志、任务焦点

### 5.4 消息模型

```python
# 内容块（discriminated union by type field）
TextBlock(type="text", text: str)
ImageBlock(type="image", media_type: str, data: str, source_path: str)
ToolUseBlock(type="tool_use", id: str, name: str, input: dict)
ToolResultBlock(type="tool_result", tool_use_id: str, content: str, is_error: bool)

# 对话消息
ConversationMessage(role: "user" | "assistant", content: list[ContentBlock])
```

### 5.5 流式事件

```python
StreamEvent:
  ├── AssistantTextDelta(text: str)              # 文本增量
  ├── AssistantTurnComplete(message, usage)       # Turn 完成
  ├── ToolExecutionStarted(tool_name, tool_input) # 工具开始
  ├── ToolExecutionCompleted(tool_name, output, is_error) # 工具完成
  ├── CompactProgressEvent(phase, trigger)        # 压缩进度
  ├── ErrorEvent(message, recoverable)            # 错误
  └── StatusEvent(message)                        # 状态更新
```

---

## 6. 工具模块

**路径**：`src/openharness/tools/`
**职责**：工具注册表与 43+ 内置工具实现。

### 6.1 基类设计

```python
class BaseTool(ABC):
    name: str                          # 工具唯一标识
    description: str                   # 模型可见的描述
    input_model: type[BaseModel]       # Pydantic 输入 schema

    async def execute(
        self,
        arguments: BaseModel,          # 已验证的输入
        context: ToolExecutionContext   # 运行时上下文
    ) -> ToolResult

    def is_read_only(self) -> bool     # 是否只读（影响权限检查）

@dataclass
class ToolResult:
    output: str                        # 字符串输出
    is_error: bool = False             # 是否错误
    metadata: dict[str, Any] = {}      # 附加元数据

@dataclass
class ToolExecutionContext:
    cwd: str                           # 当前工作目录
    # + 其他运行时元数据

class ToolRegistry:
    def register(tool: BaseTool)       # 注册工具
    def get(name: str) -> BaseTool     # 获取工具
    def list_tools() -> list[BaseTool] # 列出所有工具
    def to_api_schema() -> list[dict]  # 生成 Messages API JSON Schema
```

### 6.2 完整工具清单（43+）

| 类别 | 工具文件 | 工具名 | 说明 |
|------|----------|--------|------|
| **文件 I/O** | `file_read_tool.py` | `file_read` / `Read` | 读取文件内容（支持 offset/limit） |
| | `file_write_tool.py` | `file_write` / `Write` | 创建或覆盖文件 |
| | `file_edit_tool.py` | `file_edit` / `Edit` | 精确字符串替换编辑 |
| | `glob_tool.py` | `glob` / `Glob` | 文件名模式搜索 |
| | `grep_tool.py` | `grep` / `Grep` | 内容搜索（ripgrep 封装，8MB 流限制） |
| **执行** | `bash_tool.py` | `bash` / `Bash` | Shell 命令执行（超时、安全校验） |
| | `lsp_tool.py` | `lsp` / `LSP` | Language Server Protocol 交互 |
| | `notebook_edit_tool.py` | `notebook_edit` / `NotebookEdit` | Jupyter Notebook 单元格编辑 |
| **搜索** | `web_search_tool.py` | `web_search` / `WebSearch` | Web 搜索 |
| | `web_fetch_tool.py` | `web_fetch` / `WebFetch` | URL 内容抓取（URL 验证防 SSRF） |
| | `tool_search_tool.py` | `tool_search` / `ToolSearch` | 延迟工具 Schema 查找 |
| **交互** | `ask_user_question_tool.py` | `ask_user` / `AskUserQuestion` | 向用户提问 |
| | `brief_tool.py` | `brief` / `Brief` | 简报输出 |
| | `sleep_tool.py` | `sleep` / `Sleep` | 等待（用于轮询） |
| **Agent** | `agent_tool.py` | `agent` / `Agent` | 生成子 Agent（支持 worktree 隔离） |
| | `send_message_tool.py` | `send_message` / `SendMessage` | Agent 间发送消息 |
| | `team_create_tool.py` | `team_create` / `TeamCreate` | 创建 Agent 团队 |
| | `team_delete_tool.py` | `team_delete` / `TeamDelete` | 删除 Agent 团队 |
| **任务** | `task_create_tool.py` | `task_create` / `TaskCreate` | 创建后台任务 |
| | `task_get_tool.py` | `task_get` / `TaskGet` | 获取任务详情 |
| | `task_list_tool.py` | `task_list` / `TaskList` | 列出任务 |
| | `task_update_tool.py` | `task_update` / `TaskUpdate` | 更新任务状态 |
| | `task_stop_tool.py` | `task_stop` / `TaskStop` | 终止任务 |
| | `task_output_tool.py` | `task_output` / `TaskOutput` | 读取任务输出 |
| **计划** | `enter_plan_mode_tool.py` | `enter_plan_mode` / `EnterPlanMode` | 进入计划模式（阻止写操作） |
| | `exit_plan_mode_tool.py` | `exit_plan_mode` / `ExitPlanMode` | 退出计划模式 |
| **工作树** | `enter_worktree_tool.py` | `enter_worktree` / `EnterWorktree` | 进入 Git Worktree |
| | `exit_worktree_tool.py` | `exit_worktree` / `ExitWorktree` | 退出 Git Worktree |
| **定时** | `cron_create_tool.py` | `cron_create` / `CronCreate` | 创建定时作业 |
| | `cron_list_tool.py` | `cron_list` / `CronList` | 列出定时作业 |
| | `cron_delete_tool.py` | `cron_delete` / `CronDelete` | 删除定时作业 |
| | `cron_toggle_tool.py` | `cron_toggle` / `CronToggle` | 启用/禁用定时作业 |
| **MCP** | `mcp_tool.py` | `mcp_tool` / `McpTool` | MCP 工具适配器 |
| | `list_mcp_resources_tool.py` | `list_mcp_resources` | 列出 MCP 资源 |
| | `read_mcp_resource_tool.py` | `read_mcp_resource` | 读取 MCP 资源 |
| | `mcp_auth_tool.py` | `mcp_auth` | MCP 认证 |
| **扩展** | `skill_tool.py` | `skill` / `Skill` | 调用技能 |
| | `config_tool.py` | `config` / `Config` | 修改运行时配置 |
| | `todo_write_tool.py` | `todo_write` / `TodoWrite` | 写入 TODO 列表 |
| | `remote_trigger_tool.py` | `remote_trigger` / `RemoteTrigger` | 远程触发 |

### 6.3 工具注册

`create_default_tool_registry()` 在 `__init__.py` 中实例化全部 43+ 工具并注册。MCP 工具和插件工具通过 `McpToolAdapter` 动态添加。

---

## 7. 配置模块

**路径**：`src/openharness/config/`
**职责**：设置管理与路径解析。

### 7.1 配置优先级（高 → 低）

1. CLI 参数（`--model`, `--max-turns`, `--permission-mode` 等）
2. 环境变量（`ANTHROPIC_API_KEY`, `OPENHARNESS_MODEL`, `OPENHARNESS_API_FORMAT` 等）
3. 配置文件（`~/.openharness/settings.json` + `settings.local.json`）
4. 代码默认值

### 7.2 核心设置类

```python
Settings
  ├── provider: str                              # 当前 provider 名
  ├── api_key: str                               # API Key
  ├── model: str                                 # 模型 ID
  ├── provider_profiles: dict[str, ProviderProfile]  # 命名 Profile
  │     └── ProviderProfile(label, provider, auth_source, default_model, allowed_models, base_url)
  ├── permissions: PermissionSettings
  │     ├── mode: PermissionMode                 # DEFAULT | PERMISSIVE | RESTRICTIVE
  │     ├── allowed_tools: list[str]             # 工具白名单
  │     ├── denied_tools: list[str]              # 工具黑名单
  │     ├── path_rules: list[PathRule]           # glob 路径 ACL
  │     └── denied_commands: list[str]           # 危险命令黑名单
  ├── memory: MemorySettings
  │     ├── enabled: bool
  │     ├── max_files: int
  │     └── auto_compact_threshold_tokens: int
  ├── sandbox: SandboxSettings
  │     ├── enabled: bool
  │     ├── backend: str                         # "srt" | "docker"
  │     └── network / filesystem ACLs
  ├── hooks: dict[str, HookDefinition]
  ├── mcp_servers: dict[str, McpServerConfig]
  └── plugins: list[str]
```

### 7.3 配置目录结构

```
~/.openharness/
  ├── settings.json           # 主配置
  ├── settings.local.json     # 本地覆盖（gitignored）
  ├── credentials.json        # API 密钥（mode 600）
  ├── copilot_auth.json       # GitHub Copilot token
  ├── hooks/                  # Hook 定义
  ├── memory/                 # 全局记忆
  │   ├── MEMORY.md           # 记忆索引
  │   └── *.md                # 各条记忆
  ├── skills/                 # 用户技能
  ├── plugins/                # 已安装插件
  ├── themes/                 # 自定义主题 (*.json)
  ├── output_styles/          # 自定义输出样式 (*.md)
  ├── keybindings.json        # 键绑定覆盖
  ├── teams/                  # 团队配置
  ├── data/
  │   ├── sessions/           # 会话快照
  │   ├── cron_history.jsonl  # 定时任务执行历史
  │   └── tasks/              # 后台任务日志
  └── local_rules/            # 个性化规则
      ├── rules.md
      └── facts.json
```

### 7.4 路径解析

`paths.py` 采用 XDG-like 约定，支持环境变量覆盖：

- `OPENHARNESS_CONFIG_DIR` → 配置目录
- `OPENHARNESS_DATA_DIR` → 数据目录

---

## 8. 权限模块

**路径**：`src/openharness/permissions/`
**职责**：细粒度工具访问控制。

### 8.1 PermissionChecker

```python
class PermissionChecker:
    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool = False,
        file_path: str | None = None,
        command: str | None = None,
    ) -> PermissionDecision

@dataclass
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""
```

### 8.2 多层安全机制

| 层级 | 机制 | 说明 |
|------|------|------|
| 权限模式 | DEFAULT / PERMISSIVE / RESTRICTIVE | 基线行为 |
| 工具级 | allowed_tools / denied_tools | 白名单/黑名单 |
| 路径级 | PathRule (glob pattern + allow/deny) | 目录 ACL |
| 命令级 | denied_commands | 危险命令拦截 |
| 硬编码 | 敏感路径保护 | SSH 密钥、AWS/GCP/Azure/K8s 凭证等 |
| 只读 | is_read_only 跳过严格检查 | 读操作默认放行 |

---

## 9. Hook 模块

**路径**：`src/openharness/hooks/`
**职责**：生命周期事件系统，在关键节点注入自定义逻辑。

### 9.1 Hook 事件

```python
class HookEvent(Enum):
    USER_PROMPT_SUBMIT    # 用户提交提示词
    AGENT_LOOP_START      # Agent 循环开始
    AGENT_LOOP_END        # Agent 循环结束
    TOOL_EXECUTION_START  # 工具执行前（可 block）
    TOOL_EXECUTION_END    # 工具执行后
    SESSION_START         # 会话启动
    SESSION_END           # 会话结束
```

### 9.2 Hook 类型

| 类型 | 类名 | 说明 |
|------|------|------|
| Shell 命令 | `CommandHookDefinition` | 执行 Shell 命令，`$ARGUMENTS` 变量替换（已做 shell escape 防注入） |
| HTTP 调用 | `HttpHookDefinition` | POST 事件 payload 到 HTTP 端点 |
| 提示词注入 | `PromptHookDefinition` | 通过模型验证 payload |
| Agent 触发 | `AgentHookDefinition` | 触发 Agent 进行深层验证 |

### 9.3 HookExecutor

```python
class HookExecutor:
    async def fire(event: HookEvent, payload: dict) -> list[HookResult]
    # - fnmatch 匹配 payload 过滤
    # - 支持超时
    # - blocked=True 可阻止后续操作
```

---

## 10. MCP 模块

**路径**：`src/openharness/mcp/`
**职责**：Model Context Protocol 集成，动态扩展工具和资源。

### 10.1 McpClientManager

```python
class McpClientManager:
    # 生命周期管理（async context manager）
    async def connect(config: McpServerConfig)
    async def disconnect(name: str)
    async def reconnect(name: str)

    # 工具/资源暴露
    def list_tools() -> list[McpToolInfo]
    def list_resources() -> list[McpResourceInfo]
    async def call_tool(server, tool_name, arguments) -> ToolResult
    async def read_resource(server, uri) -> str

    # 状态追踪
    def get_status() -> dict[str, McpConnectionStatus]
```

### 10.2 传输协议

```python
McpServerConfig = McpStdioServerConfig    # 子进程 stdio
                | McpHttpServerConfig     # HTTP POST
                | McpWebSocketServerConfig # WebSocket

McpConnectionStatus:
    name: str
    state: "connected" | "failed" | "pending" | "disabled"
    transport: str
    tools: list[McpToolInfo]
    resources: list[McpResourceInfo]
```

### 10.3 工具适配

MCP 工具通过 `McpToolAdapter` 包装为 `BaseTool`，动态注册到 `ToolRegistry`。JSON Schema 类型自动推断，无需手动类型映射。

---

## 11. 记忆模块

**路径**：`src/openharness/memory/`
**职责**：持久化项目记忆，跨会话保留上下文。

### 11.1 存储结构

```
.claude/memory/  或  ~/.openharness/memory/
  ├── MEMORY.md           # 索引文件（<200 行）
  ├── user_role.md         # 用户记忆
  ├── feedback_testing.md  # 反馈记忆
  └── project_auth.md      # 项目记忆
```

每条记忆使用 YAML frontmatter：

```markdown
---
name: 记忆名称
description: 一行描述（用于相关性判断）
type: user | feedback | project | reference
---

记忆正文内容...
```

### 11.2 核心类

| 类 | 文件 | 职责 |
|----|------|------|
| `MemoryManager` | `manager.py` | 创建/删除记忆，维护 MEMORY.md 索引，文件锁保证线程安全 |
| `MemoryScanner` | `scan.py` | 解析 YAML frontmatter 提取 title/description/type/body_preview |
| `MemorySearch` | `search.py` | Token 化搜索，metadata 权重 2x > body，支持 ASCII + Han 字符 |
| `MemoryHeader` | `types.py` | 记忆元数据（path, title, description, modified_at, type, body_preview） |

---

## 12. Swarm 多 Agent 模块

**路径**：`src/openharness/swarm/`
**职责**：多 Agent 团队执行。

### 12.1 架构

```
Leader Agent
  │
  ├── BackendRegistry.detect_best_backend()
  │     ├── SubprocessBackend（默认，独立进程）
  │     ├── InProcessBackend（asyncio Task + contextvars 隔离）
  │     ├── TmuxBackend（tmux pane 可视化）
  │     └── ITermBackend（iTerm2 可视化）
  │
  ├── TeammateMailbox（异步消息队列）
  │     └── 文件级原子写入：~/.openharness/teams/<team>/agents/<id>/inbox/
  │
  ├── Permission Sync（权限同步）
  │     └── 文件/Mailbox 双模式：pending → resolved
  │
  └── Git Worktree（文件隔离）
```

### 12.2 核心类

| 类 | 文件 | 职责 |
|----|------|------|
| `BackendRegistry` | `registry.py` | 后端注册 + 优先级自动检测 |
| `TeammateSpawnConfig` | `types.py` | 生成配置（name, team, prompt, cwd, permissions, model, color, worktree） |
| `SpawnResult` | `types.py` | 生成结果（task_id, agent_id, backend_type, pane_id） |
| `TeammateMailbox` | `mailbox.py` | 文件级异步消息队列（`.tmp` → rename 原子写入） |
| `TeammateContext` | `in_process.py` | 基于 ContextVar 的 per-task 状态隔离 |
| `TeammateAbortController` | `in_process.py` | 双信号中止（graceful + force kill） |

### 12.3 消息类型

- `user_message`：Leader → Agent 消息
- `permission_request` / `permission_response`：权限审批流
- `shutdown`：终止信号
- `idle_notification`：空闲通知

### 12.4 环境继承

`spawn_utils.py` 中 `build_inherited_cli_flags()` 和 `build_inherited_env_vars()` 将关键环境变量（API Key、代理、CA 证书）传播到子 Agent。当 model 值为 `"inherit"` 时跳过 `--model` 标志，通过 `OPENHARNESS_MODEL` 环境变量继承。

---

## 13. Coordinator 模块

**路径**：`src/openharness/coordinator/`
**职责**：结构化多 Agent 编排（Coordinator 模式）。

### 13.1 核心类

```python
class TeamRegistry:
    """内存中团队注册表"""
    teams: dict[str, TeamRecord]  # team_name → TeamRecord(agents, messages)

class TaskNotification:
    """Agent 任务完成通知"""
    task_id: str
    status: str
    summary: str
    result: str
    usage: UsageSnapshot

class WorkerConfig:
    """Worker Agent 配置"""
    agent_id: str
    name: str
    prompt: str
    model: str
    team: str
    color: str
```

### 13.2 Worker 工具清单

| 类型 | 允许的工具 |
|------|----------|
| Full Worker | bash, file_read, file_edit, file_write, glob, grep, web_fetch, web_search, task_*, skill |
| Simple Worker | bash, file_read, file_edit |

### 13.3 XML 通信协议

Coordinator 与 Worker 之间通过 XML 格式交换任务通知：

```xml
<task-notification>
  <task_id>...</task_id>
  <status>completed</status>
  <summary>...</summary>
  <result>...</result>
</task-notification>
```

通过 `CLAUDE_CODE_COORDINATOR_MODE` 环境变量激活。

---

## 14. Bridge 模块

**路径**：`src/openharness/bridge/`
**职责**：管理隔离的 Claude Code 子会话。

```python
class BridgeSessionManager:
    """单例，管理生成的 Bridge 会话"""
    sessions: dict[str, SessionHandle]

    def spawn_session(command, cwd) -> BridgeSessionRecord
    def get_session(session_id) -> BridgeSessionRecord
    def list_sessions() -> list[BridgeSessionRecord]

class SessionHandle:
    """进程包装器"""
    process: asyncio.subprocess.Process
    stdout_pipe / stderr_pipe
    output_path: str  # ~/.openharness/bridge/<session_id>.log
```

---

## 15. 任务模块

**路径**：`src/openharness/tasks/`
**职责**：后台 Shell 和 Agent 子进程管理。

### 15.1 BackgroundTaskManager（单例）

```python
class BackgroundTaskManager:
    # 创建
    create_shell_task(command, cwd, description) -> TaskRecord
    create_agent_task(prompt, cwd, api_key, model) -> TaskRecord

    # 查询
    get_task(task_id) -> TaskRecord
    list_tasks(status_filter) -> list[TaskRecord]

    # 控制
    update_task(task_id, description, status_note)
    stop_task(task_id)                    # 优雅终止（3s）→ 强制 kill
    write_to_task(task_id, text)          # 写入 stdin（Agent 任务）
    read_task_output(task_id) -> str      # 读取输出（最多 12000 bytes）

    # 监听
    register_completion_listener(callback)
```

### 15.2 TaskRecord

```python
@dataclass
class TaskRecord:
    id: str
    type: "local_bash" | "local_agent" | "remote_agent" | "in_process_teammate"
    status: "pending" | "running" | "completed" | "failed" | "killed"
    description: str
    cwd: str
    output_file: str
    command: str | None       # Shell 任务
    prompt: str | None        # Agent 任务
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    return_code: int | None
    metadata: dict[str, Any]
```

### 15.3 关键机制

- 每个任务一个 `.log` 文件
- Generation counter 防止进程重启后出现陈旧的 watcher
- 完成监听器异步回调
- 全局单例按 `tasks_dir` 路径索引

---

## 16. 服务模块

**路径**：`src/openharness/services/`
**职责**：定时调度、会话持久化、上下文压缩。

### 16.1 Cron 调度器

**文件**：`cron.py` + `cron_scheduler.py`

```python
# 作业定义
CronJob(name, schedule, command, enabled, next_run, created_at)

# 调度器（独立守护进程）
CronScheduler:
    run_scheduler_loop()     # 主循环，每 30s 轮询
    execute_job(job)         # 子进程执行，300s 超时
    # PID 文件管理：write_pid / read_pid / is_scheduler_running / stop_scheduler
    # JSONL 历史记录：append_history / load_history
```

- 通过 croniter 验证 cron 表达式
- PID 文件确保单实例
- SIGTERM → SIGKILL 升级终止
- 执行历史写入 `~/.openharness/data/cron_history.jsonl`

### 16.2 会话存储

**文件**：`session_storage.py` + `session_backend.py`

```python
class SessionBackend(Protocol):
    async def save_snapshot(cwd, name, snapshot)
    async def load_latest(cwd, name) -> SessionSnapshot
    async def list_snapshots(cwd) -> list[SessionInfo]
    async def export_markdown(cwd, name) -> str

# 存储结构：
# ~/.openharness/data/sessions/{name}-{sha1(cwd)}/
#   ├── latest.json
#   └── session-{id}.json
```

每次 turn 结束后自动快照，包含：model、system_prompt、messages、usage、tool_metadata 子集、created_at、summary。

### 16.3 上下文压缩

**文件**：`compact/__init__.py`

三级压缩策略：

| 级别 | 名称 | 触发条件 | 方式 |
|------|------|----------|------|
| 1 | **Microcompact** | token 接近阈值 | 清除旧工具输出（保留最近 5 个），替换为 `"[Old tool result content cleared]"` |
| 2 | **Session Memory** | 确定性摘要 | 保留最近 12 条消息，旧消息合并为单条摘要 |
| 3 | **Full Compact** | LLM 调用 | 生成 `<analysis>/<summary>` 结构化摘要（max 20k tokens，25s timeout） |

**可压缩工具**：`read_file`, `bash`, `grep`, `glob`, `web_search`, `web_fetch`, `edit_file`, `write_file`

**Auto-Compaction 触发**：`tokens ≥ context_window - 20k_reserved - 13k_buffer`

**Attachments 保留**：压缩后保留 `task_focus`、`recent_verified_work`、`recent_files`、`plan_mode`、`invoked_skills`、`async_agent_state`、`recent_work_log` 用于上下文连续性。

**安全机制**：
- Tool use / tool result 成对保留（不会切断配对）
- Token 估算使用 4/3 保守系数
- "prompt too long" 错误触发 reactive compaction（头部截断：保留前 900 + 后 500 字符）
- 最多 2 次流式重试 + 3 次 PTL 重试

### 16.4 Token 估算

```python
def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4    # 字符启发式

def estimate_message_tokens(message: ConversationMessage) -> int:
    # 累加所有 text/tool_result/tool_use blocks
```

---

## 17. 插件模块

**路径**：`src/openharness/plugins/`
**职责**：插件扩展系统，兼容 claude-code plugins。

### 17.1 核心类

```python
class PluginManifest(BaseModel):
    """plugin.json schema"""
    name: str
    version: str
    description: str
    skills_dir: str = "skills"    # 技能目录
    tools_dir: str = "tools"      # 工具目录（BaseTool 子类自动发现）
    hooks_dir: str = "hooks"      # Hook 定义
    commands_dir: str = "commands" # 命令目录
    agents_dir: str = "agents"    # Agent 定义
    mcp_dir: str = "mcp"          # MCP 服务器

class LoadedPlugin:
    manifest: PluginManifest
    enabled: bool
    skills: list[SkillDefinition]
    commands: list[...]
    agents: list[...]
    tools: list[BaseTool]
    hooks: list[HookDefinition]
    mcp_servers: list[McpServerConfig]
```

### 17.2 发现路径

1. 用户插件：`~/.openharness/plugins/<plugin-name>/`
2. 项目插件：`.openharness/plugins/<plugin-name>/`
3. 每个插件必须包含 `.claude-plugin/plugin.json`

### 17.3 工具发现

v0.1.7+ 支持插件提供 `BaseTool` 子类：插件 `tools/` 目录下的 Python 文件被自动发现、实例化并注册到 `ToolRegistry`。

---

## 18. 技能模块

**路径**：`src/openharness/skills/`
**职责**：按需知识加载。

### 18.1 核心类

```python
@dataclass
class SkillDefinition:
    name: str
    description: str
    content: str           # Markdown 全文
    source: str            # "bundled" | "user" | "plugin:<name>"

class SkillRegistry:
    def register(skill: SkillDefinition)
    def get(name: str) -> SkillDefinition
    def list_skills() -> list[SkillDefinition]
```

### 18.2 加载路径

1. 内置技能：`src/openharness/skills/bundled/content/*.md`（commit, review, debug, plan, test, simplify, diagnose）
2. 用户技能：`~/.openharness/skills/*.md`
3. 项目技能：`.claude/skills/*/SKILL.md`
4. 插件技能：通过 PluginLoader 发现

### 18.3 技能格式

```markdown
---
name: my-skill
description: Expert guidance for my domain
---

# My Skill

## When to use
...

## Workflow
1. Step one
2. Step two
```

使用 `yaml.safe_load` 解析 frontmatter（v0.1.7 修复了 naive line-by-line 解析）。

---

## 19. 命令模块

**路径**：`src/openharness/commands/`
**职责**：斜杠命令注册与分发。

### 19.1 核心类

```python
@dataclass
class SlashCommand:
    name: str
    description: str
    handler: Callable
    remote_invocable: bool = False      # 可远程触发
    remote_admin_opt_in: bool = False   # 需管理员确认
    aliases: list[str] = []

class CommandRegistry:
    def register(command: SlashCommand)
    def lookup(name: str) -> SlashCommand        # 支持别名
    def list_commands() -> list[SlashCommand]
    def help_text() -> str

@dataclass
class CommandContext:
    engine: QueryEngine
    mcp_summary: str
    plugin_summary: str
    cwd: str
    tool_registry: ToolRegistry
    app_state: AppState
    session_backend: SessionBackend

@dataclass
class CommandResult:
    message: str
    should_exit: bool = False
    clear_screen: bool = False
    replay_messages: list = []
    continue_turns: bool = False
    submit_prompt: str | None = None
```

### 19.2 命令来源

1. 内置命令：`handle_line()` 中硬编码
2. 插件命令：Markdown 模板，`${ARGUMENTS}` 和 `${CLAUDE_SESSION_ID}` 变量替换

---

## 20. 沙箱模块

**路径**：`src/openharness/sandbox/`
**职责**：隔离执行环境。

### 20.1 双后端

| 后端 | 文件 | 说明 |
|------|------|------|
| **srt CLI** | `adapter.py` | macOS 系统 sandbox-runtime 封装 |
| **Docker** | `docker_backend.py` | Docker 容器隔离 |

### 20.2 SandboxAdapter (srt)

```python
class SandboxAdapter:
    def wrap_command(command: str) -> str       # 包装为 srt 命令
    def build_config() -> dict                  # 转换为 srt JSON 配置

class SandboxAvailability:
    enabled: bool
    available: bool
    reason: str
```

### 20.3 DockerSandboxSession

```python
class DockerSandboxSession:
    async def create()      # 创建容器
    async def execute(cmd)  # 在容器中执行
    async def destroy()     # 销毁容器

    # 特性：
    # - 自动构建 Docker 镜像
    # - 网络隔离
    # - 文件系统 ACL（挂载规则）
    # - CPU/内存资源限制
```

不可用时优雅降级（`SandboxUnavailableError`）。

---

## 21. 频道模块

**路径**：`src/openharness/channels/`
**职责**：多平台聊天集成（Pub-Sub 消息总线）。

### 21.1 消息总线

```python
class MessageBus:
    inbound: asyncio.Queue[InboundMessage]     # 频道 → Agent
    outbound: asyncio.Queue[OutboundMessage]   # Agent → 频道

    async def publish_inbound(msg)
    async def consume_inbound() -> InboundMessage
    async def publish_outbound(msg)
    async def consume_outbound() -> OutboundMessage

@dataclass
class InboundMessage:
    channel: str
    sender_id: str
    chat_id: str
    content: str
    media: list | None
    metadata: dict

@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    reply_to: str | None
    media: list | None
```

### 21.2 ChannelBridge

连接 MessageBus 与 QueryEngine：消费 inbound → 提交到 engine.submit_message() → 收集流式事件 → 发布 outbound 回复。

### 21.3 支持的频道

Slack, Discord, Telegram, Feishu, WhatsApp, Email, DingTalk, QQ, Matrix, MoChat（可插拔架构）。

---

## 22. 认证模块

**路径**：`src/openharness/auth/`
**职责**：多流程认证管理。

### 22.1 AuthManager

```python
class AuthManager:
    # Profile 管理
    def switch_profile(name: str)
    def get_active_profile() -> ProviderProfile
    def list_profiles() -> list[ProviderProfile]

    # 凭证操作
    def store_credential(auth_source, value)
    def get_credential(auth_source) -> str | None
    def clear_credential(auth_source)
```

### 22.2 凭证存储

```python
class CredentialStorage:
    # 文件：~/.openharness/credentials.json（mode 600）
    # 可选 keyring 后端
    # 支持 "external bindings"（OAuth 流程由外部 CLI 管理）
```

### 22.3 认证流程

| 流程 | 类 | 适用场景 |
|------|-----|---------|
| API Key | `ApiKeyFlow` | 大多数 Provider（提示用户输入） |
| Device Code | `DeviceCodeFlow` | GitHub Copilot（浏览器授权） |
| Browser | `BrowserFlow` | 浏览器 OAuth + token 复制 |

### 22.4 Auth Kind 映射

- `api_key`：大多数 Provider
- `oauth_device`：GitHub Copilot
- `external_oauth`：Codex / Claude 订阅（从 `~/.codex/auth.json` 或 `~/.claude/.credentials.json` 读取）

---

## 23. 提示词模块

**路径**：`src/openharness/prompts/`
**职责**：系统提示词运行时组装。

### 23.1 构建流程

```python
build_runtime_system_prompt() =
    build_system_prompt()           # 基础指令（工具使用规则、安全边界、语气）
    + environment section           # 自动检测 OS/Shell/Python/Git/venv
    + fast-mode guidance            # 快速模式指引
    + effort/passes settings        # 努力级别设置
    + skills section                # SkillRegistry → 可用技能列表
    + delegation/agent instructions # Agent 委托指引
    + personalization rules         # 个性化规则注入
    + CLAUDE.md discovery           # 项目级 + 用户级 CLAUDE.md
    + memory context                # MEMORY.md + 相关记忆
```

### 23.2 环境检测

`environment.py` 自动检测：
- 操作系统、Shell 类型、Python 版本
- 虚拟环境（venv/conda）
- Git 分支、是否 git 仓库
- 终端类型

---

## 24. UI 模块

**路径**：`src/openharness/ui/`
**职责**：交互式会话管理（Python 后端）。

### 24.1 RuntimeBundle

```python
@dataclass
class RuntimeBundle:
    """所有运行时组件的容器"""
    api_client: SupportsStreamingMessages
    mcp_manager: McpClientManager
    tool_registry: ToolRegistry
    app_state_store: AppStateStore
    hook_executor: HookExecutor
    command_registry: CommandRegistry
    session_backend: SessionBackend
    permission_checker: PermissionChecker
    # ...
```

### 24.2 运行模式

| 模式 | 函数 | 说明 |
|------|------|------|
| 交互 REPL | `run_repl()` | 启动 React TUI，持续对话 |
| 打印模式 | `run_print_mode()` | 执行单次 prompt，输出到 stdout，退出 |
| 输出格式 | — | `text` / `json` / `stream-json` |

### 24.3 后端通信协议

**ReactBackendHost** 通过 JSON-lines 与 React 前端通信：

- **下行（Python → React）**：`OHJSON:` 前缀的 BackendEvent JSON
  - 事件类型：transcript, state_snapshot, tasks, modals, tool_execution
- **上行（React → Python）**：FrontendRequest JSON（stdin）
  - 请求类型：line_submission, permission_response, selection_response

### 24.4 异步机制

- Permission futures：权限确认使用 `asyncio.Future` 桥接 UI 对话框
- Question futures：用户问答同理
- 并发权限 modal 通过 `asyncio.Lock` 串行化，防止覆盖

---

## 25. React TUI 前端

**路径**：`frontend/terminal/`
**职责**：终端交互界面（React 18 + Ink 5）。

### 25.1 组件清单（21 个 TSX）

| 组件 | 职责 |
|------|------|
| `App.tsx` | 主编排器（~472 行），管理状态、键盘、命令历史、自动化 |
| `ConversationView.tsx` | 多角色会话渲染（user/assistant/tool/system/status/log） |
| `StatusBar.tsx` | 底部状态栏：model、tokens、permissions、task count、MCP 状态 |
| `PromptInput.tsx` | 多行输入：Shift+Enter 换行，Enter 提交，历史导航，Vim 模式 |
| `CommandPicker.tsx` | `/` 触发的命令自动补全下拉 |
| `ModalHost.tsx` | Modal 容器 |
| `SelectModal.tsx` | 选项选择 UI |
| `TodoPanel.tsx` | 任务追踪面板 |
| `SwarmPanel.tsx` | 多 Agent 协作状态面板 |
| `ToolCallDisplay.tsx` | 工具调用 + 结果渲染 |
| `WelcomeBanner.tsx` | 首次运行欢迎信息 |
| `MarkdownText.tsx` | Markdown 渲染（标题、列表、代码、表格、引用、链接） |
| `ThemeContext.tsx` | 主题 Provider（颜色、图标） |
| ... | 其他辅助组件 |

### 25.2 设计特点

- `useDeferredValue` 性能优化
- 键盘驱动（箭头、Escape、数字键、Tab）
- `codex` 输出风格：减少 streaming 时的 buffer flush 频率
- 分组渲染：连续 `tool` + `tool_result` 合并为复合行
- Windows 兼容：保守 ASCII spinner、减少闪烁

---

## 26. 状态管理模块

**路径**：`src/openharness/state/`
**职责**：可观察的应用状态容器。

```python
@dataclass
class AppState:
    model: str
    theme: str
    cwd: str
    vim_enabled: bool
    voice_enabled: bool
    voice_available: bool
    voice_reason: str
    effort: str
    passes: int
    mcp_server_count: int
    output_style: str
    keybindings: dict

class AppStateStore:
    def get() -> AppState
    def set(**updates)                          # 部分更新
    def subscribe(listener) -> unsubscribe_fn   # 观察者模式
```

状态变更通过 `BackendEvent.state_snapshot()` 流向 React UI。

---

## 27. 主题与样式模块

### 27.1 主题（`src/openharness/themes/`）

```python
class ThemeConfig(BaseModel):
    name: str
    colors: ColorsConfig
        primary, secondary, accent, error, muted, bg, fg: str
    borders: BorderConfig
        style: str
        char: str
    icons: IconConfig
        spinner, tool, error, success, agent: str
    layout: LayoutConfig
        compact: bool
        show_tokens: bool
        show_time: bool
```

- 内置主题 + 自定义主题（`~/.openharness/themes/*.json`）
- `load_theme(name)` 自定义优先，内置后备
- 通过 `/theme` 命令切换

### 27.2 输出样式（`src/openharness/output_styles/`）

```python
@dataclass
class OutputStyle:
    name: str
    content: str        # Markdown 模板
    source: str         # "builtin" | "user"
```

- 内置样式：`default`, `minimal`, `codex`
- 自定义样式：`~/.openharness/output_styles/*.md`
- 通过 `/output-style` 命令切换

---

## 28. 键绑定模块

**路径**：`src/openharness/keybindings/`
**职责**：可自定义的键盘快捷键。

```python
# 默认键绑定
DEFAULT_KEYBINDINGS: dict = { ... }

# 用户覆盖
# ~/.openharness/keybindings.json

# 解析流程：
def resolve_keybindings(overrides: dict) -> dict:
    return {**DEFAULT_KEYBINDINGS, **overrides}
```

---

## 29. 个性化模块

**路径**：`src/openharness/personalization/`
**职责**：从对话中自动提取环境事实并注入未来会话。

### 29.1 事实提取

```python
def extract_facts_from_text(text: str) -> list[Fact]:
    """正则匹配提取环境信息"""
    # 识别类型：SSH hosts, IPs, data paths, conda/python envs,
    #           API endpoints, git remotes, ray clusters, cron schedules

@dataclass
class Fact:
    key: str
    type: str
    label: str
    value: str
    confidence: float
```

### 29.2 规则管理

```python
def merge_facts(existing, new_facts) -> list[Fact]:
    """按 key 去重，保留高 confidence"""

def facts_to_rules_markdown(facts) -> str:
    """分组生成 Markdown 规则"""
```

存储位置：`~/.openharness/local_rules/{rules.md, facts.json}`

---

## 30. 语音模块

**路径**：`src/openharness/voice/`
**职责**：语音输入能力。

```python
class VoiceDiagnostics:
    """检测可用性"""
    available: bool
    reason: str
    # 检查 sox / ffmpeg / arecord 是否安装

def toggle_voice_mode(enabled: bool)
```

- `keyterms.py`：关键词检测
- `stream_stt.py`：流式 Speech-to-Text
- `AppState` 追踪 `voice_enabled` / `voice_available` / `voice_reason`

---

## 31. Vim 模块

**路径**：`src/openharness/vim/`
**职责**：Vim 模式开关。

```python
def toggle_vim_mode(enabled: bool):
    """切换 Vim 模式标志"""
```

实际 Vim 键绑定由 React TUI 前端的 PromptInput 组件处理。

---

## 32. Autopilot 模块

**路径**：`src/openharness/autopilot/`
**职责**：仓库级自动化任务编排。

### 32.1 任务模型

```python
@dataclass
class RepoTaskCard:
    id: str
    fingerprint: str
    title: str
    source_kind: str    # "github_issue" | "claude_code_candidate" | ...
    status: str         # queued → accepted → running → pr_open →
                        # waiting_ci → completed | merged | failed
    score: float        # 优先级评分
    labels: list[str]
    metadata: dict

class RepoRunResult:
    card_id: str
    status: str
    assistant_summary: str
    verification_steps: list[str]
    pr_number: int | None
    pr_url: str | None
    worktree_path: str
```

### 32.2 编排流程

```
GitHub Issue 扫描 → 任务入队（去重）
     → 评分排序
     → 接受任务
     → Worktree 隔离执行（max_turns, full_auto）
     → 验证门（fast: pytest/ruff/mypy, repo, harness）
     → 自动修复（CI 失败时）
     → PR 创建
     → CI 轮询
     → 自动合并（通过 human gate）
```

### 32.3 Journal

```python
class RepoJournalEntry:
    kind: str           # 事件类型
    summary: str
    task_id: str | None
    metadata: dict
```

Append-only 日志，用于 Autopilot Dashboard 展示。

### 32.4 CI 集成

- `autopilot-scan.yml`：每 30 分钟扫描任务
- `autopilot-run-next.yml`：每 2 小时执行下一个任务（self-hosted runner）
- `autopilot-pages.yml`：部署 Dashboard 到 GitHub Pages

---

## 33. ohmo 个人 Agent

**路径**：`ohmo/`
**职责**：构建在 OpenHarness 之上的个人 AI Agent 应用。

### 33.1 架构

```
聊天平台 (Feishu / Slack / Telegram / Discord)
  │
  ▼
OhmoGatewayService
  ├── ChannelManager → 多频道消息接收
  ├── MessageBus → 事件路由
  ├── SessionRouter → (channel, chat_id, thread_id, sender_id) → session_id
  └── OhmoSessionRuntimePool → 活跃会话管理
        └── QueryEngine → OpenHarness 核心引擎
```

### 33.2 工作空间

```
~/.ohmo/
  ├── SOUL.md          # 人格身份文档（长期行为）
  ├── identity.md      # 谁是 ohmo（可选扩展）
  ├── user.md          # 用户画像（时区、偏好、决策风格）
  ├── BOOTSTRAP.md     # 首次运行引导仪式
  ├── memory/          # 个人记忆
  ├── sessions/        # 会话持久化
  ├── gateway.json     # 网关配置（Provider + 频道）
  └── state.json       # 运行时状态
```

### 33.3 系统提示词组装

```python
build_ohmo_system_prompt() =
    base harness prompt            # OpenHarness 基础指令
    + ohmo soul (SOUL.md)          # Agent 价值观和行为准则
    + ohmo identity (identity.md)  # Agent 身份扩展
    + user profile (user.md)       # 用户偏好和上下文
    + project memory               # 项目记忆
    + personal memory              # 个人记忆
```

### 33.4 会话持久化

```python
class OhmoSessionBackend:
    save_snapshot(session_id, model, system_prompt, messages, usage, summary)
    load_latest(session_id) -> SessionSnapshot
    # 存储：~/.ohmo/sessions/{session_id}.json
```

### 33.5 Gateway

```python
class OhmoGatewayService:
    """长运行服务，管理多频道集成"""
    start() / stop() / restart()

class OhmoSessionRuntimePool:
    """并发会话运行时管理"""
    get_or_create(session_id) -> QueryEngine
```

### 33.6 CLI

```bash
ohmo                    # 启动 TUI
ohmo init               # 初始化 ~/.ohmo 工作空间
ohmo config             # 配置频道和 Provider
ohmo gateway start      # 启动网关
ohmo gateway stop       # 停止网关
ohmo gateway status     # 检查网关状态
ohmo gateway restart    # 重启网关
ohmo memory add         # 添加记忆
ohmo memory list        # 列出记忆
ohmo memory remove      # 删除记忆
```

---

## 34. Autopilot Dashboard

**路径**：`autopilot-dashboard/`
**职责**：Autopilot 任务可视化（React + Vite）。

### 34.1 功能

- **看板视图**：4 列（To Do / In Progress / In Review / Done）
- **任务卡片**：ID、标题、状态徽章、标签、元数据（验证步骤、CI 摘要、human gate）
- **Journal 视图**：事件时间线
- **筛选搜索**：按标题、正文、标签、ID 实时过滤
- **Hero Section**：当前焦点任务 + 动画管线可视化
- **统计栏**：各状态计数

### 34.2 数据源

静态 `snapshot.json`，由 Autopilot CI 流水线定期生成并提交。无后端依赖。

### 34.3 状态配色

| 状态 | 颜色 |
|------|------|
| running | teal |
| repair | orange |
| verify | blue |
| failed | red |
| completed/merged | green |

---

## 35. CLI 接口

### 35.1 主命令

```bash
oh [OPTIONS]                       # 交互式会话
oh -p PROMPT [OPTIONS]             # 非交互打印模式
oh --continue / -c                 # 继续上一次会话
oh --resume ID / -r ID             # 按 ID 恢复会话
oh --dry-run [-p PROMPT]           # 预览配置（不执行模型/工具）
oh --version / -v                  # 版本号
```

### 35.2 会话选项

```bash
--model / -m MODEL                 # 模型别名或 ID
--effort LEVEL                     # low | medium | high | max
--max-turns N                      # Agent turn 限制
--output-format FORMAT             # text | json | stream-json
--verbose / -d                     # 调试日志
--bare                             # 最小化模式
```

### 35.3 权限选项

```bash
--permission-mode MODE             # default | plan | full_auto
--allowed-tools TOOLS              # 工具白名单
--disallowed-tools TOOLS           # 工具黑名单
--dangerously-skip-permissions     # 跳过所有权限检查（危险）
```

### 35.4 上下文选项

```bash
--system-prompt / -s PROMPT        # 自定义系统提示词
--append-system-prompt PROMPT      # 追加系统提示词
--settings FILE                    # 指定配置文件
--api-key KEY                      # API Key
--base-url URL                     # API Base URL
--api-format FORMAT                # anthropic | openai | copilot
```

### 35.5 子命令组

```bash
# 设置向导
oh setup                           # 交互式 Provider 选择 + 认证

# Provider 管理
oh provider list                   # 列出 Profile
oh provider use PROFILE            # 激活 Profile
oh provider add PROFILE [OPTIONS]  # 创建 Profile

# 认证
oh auth login [PROVIDER]           # 交互式认证
oh auth status                     # 认证状态
oh auth logout [PROVIDER]          # 清除凭证
oh auth switch PROVIDER            # 切换 Provider
oh auth copilot-login              # GitHub Copilot 设备码流程
oh auth copilot-logout             # 清除 Copilot 凭证

# MCP 服务器
oh mcp list                        # 已配置服务器
oh mcp add NAME CONFIG_JSON        # 添加服务器
oh mcp remove NAME                 # 移除服务器

# 定时任务
oh cron start                      # 启动调度器
oh cron list                       # 列出任务
oh cron create --schedule "..."    # 注册任务

# 插件
oh plugin list                     # 列出插件
oh plugin install NAME             # 安装插件
oh plugin enable NAME              # 启用插件

# Autopilot
oh autopilot scan all              # 扫描任务
oh autopilot tick                  # 执行下一个任务
```

### 35.6 Dry-run 模式

```bash
oh --dry-run                       # 预览交互式会话配置
oh --dry-run -p "Review this bug"  # 预览 prompt（匹配技能/工具）
oh --dry-run -p "/plugin list"     # 预览斜杠命令路径
```

输出包含：
- 解析的设置、认证状态
- 匹配的技能和工具
- 就绪性判定：`ready` / `warning` / `blocked`
- `next_actions`：建议的修复步骤

---

## 36. 协议与数据模型

### 36.1 工具调用协议（LLM ↔ 引擎）

**请求**（LLM → 引擎）：
```json
{
  "type": "tool_use",
  "id": "toolu_...",
  "name": "bash",
  "input": {
    "command": "ls -la",
    "cwd": "/path",
    "timeout_seconds": 300
  }
}
```

**响应**（引擎 → LLM）：
```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_...",
  "content": "output text",
  "is_error": false
}
```

### 36.2 UI 通信协议（Python ↔ React）

**下行**（OHJSON: 前缀 + JSON）：
```python
BackendEvent:
  ├── transcript(role, content, tool_name, is_error)
  ├── state_snapshot(AppState)
  ├── tasks(list[TaskRecord])
  ├── modal(type, title, options)
  ├── tool_execution(name, status, input, output)
  └── line_complete()    # Turn 结束信号
```

**上行**（JSON stdin）：
```python
FrontendRequest:
  ├── line_submission(text)
  ├── permission_response(allowed: bool)
  └── selection_response(selected: str)
```

### 36.3 Swarm 通信协议

```python
TeammateMessage:
  types: user_message | permission_request | permission_response | shutdown | idle_notification

# 传输：文件级原子写入
# 路径：~/.openharness/teams/<team>/agents/<agent_id>/inbox/<timestamp>.json
```

### 36.4 关键数据模型汇总

```python
# Provider
ProviderSpec(name, keywords, env_key, backend_type, default_base_url, ...)
ProviderProfile(label, provider, auth_source, default_model, allowed_models, base_url)

# 任务
TaskRecord(id, type, status, description, cwd, output_file, command, prompt, timestamps, return_code, metadata)

# MCP
McpServerConfig = McpStdioServerConfig | McpHttpServerConfig | McpWebSocketServerConfig
McpConnectionStatus(name, state, transport, tools, resources)

# Swarm
TeammateSpawnConfig(name, team, prompt, cwd, permissions, model, color, worktree)
SpawnResult(task_id, agent_id, backend_type, pane_id)

# Autopilot
RepoTaskCard(id, fingerprint, title, source_kind, status, score, labels, metadata)
RepoRunResult(card_id, status, assistant_summary, verification_steps, pr_number, pr_url, worktree_path)

# Session
SessionSnapshot(model, system_prompt, messages, usage, tool_metadata, created_at, summary)
```

---

## 37. 测试策略

### 37.1 测试规模

- **107+ 测试文件**，~19,000 行测试代码
- **37 个测试目录**，按功能区域组织
- **4 个大型集成测试**（使用真实 API 调用）

### 37.2 测试分层

| 层级 | 目录/文件 | 说明 |
|------|-----------|------|
| 单元测试 | `tests/test_*/` | 各模块独立测试，mock 外部依赖 |
| 集成测试 | `tests/test_real_*.py` | 真实 API + 多 turn 测试 |
| E2E Smoke | `scripts/e2e_smoke.py` | 真实端到端场景（含 fixture MCP 服务器） |
| E2E Docker | `scripts/test_docker_sandbox_e2e.py` | Docker 沙箱生命周期 |
| E2E TUI | `scripts/react_tui_e2e.py`, `test_tui_interactions.py` | React TUI 交互 |
| CLI 标志 | `scripts/test_cli_flags.py` | CLI 参数验证 |
| Skills/Plugins | `scripts/test_real_skills_plugins.py` | 真实技能和插件加载 |
| Headless | `scripts/test_headless_rendering.py` | 无头渲染验证 |

### 37.3 测试框架

- **pytest** + `asyncio_mode = "auto"`
- **pytest-asyncio**：异步测试夹具
- **pytest-cov**：覆盖率报告
- **pexpect**：终端交互测试
- 全局 fixture：`conftest.py` 中 `_reset_background_task_manager()` 自动清理

### 37.4 覆盖区域

Engine, Tools (43+), UI (React + Textual), Swarm, Plugins, Skills, Hooks, Config, Auth, Sandbox (Docker), Memory, MCP, Coordinator, Commands, Permissions, Prompts, Bridge, Channels, Autopilot, ohmo (Gateway + CLI)

### 37.5 CI

`.github/workflows/ci.yml`：
- Python 3.10 + 3.11 矩阵
- Ruff lint
- pytest
- Frontend TypeScript type check（Node 20）

---

## 38. 构建与发布

### 38.1 开发安装

```bash
git clone https://github.com/HKUDS/OpenHarness.git
cd OpenHarness
uv sync --extra dev
uv run pytest -q
```

### 38.2 用户安装

```bash
# Linux / macOS / WSL
curl -fsSL https://raw.githubusercontent.com/HKUDS/OpenHarness/main/scripts/install.sh | bash

# Windows PowerShell
iex (Invoke-WebRequest -Uri '...' )

# pip
pip install openharness-ai
```

安装脚本将 `oh`, `ohmo`, `openharness` 链接到 `~/.local/bin`（避免 Conda 冲突）。

### 38.3 打包

- 构建后端：hatchling
- 前端资源打包进 wheel：`frontend/terminal/src` → `openharness/_frontend/src`
- Wheel 包含：`src/openharness/` + `ohmo/`
- 排除：`.git`, `.venv`, `.openharness-venv`, `.openharness`, 缓存目录

### 38.4 验证命令

```bash
ruff check src/ ohmo/ tests/        # Lint
pytest -q                            # 测试
npx tsc --noEmit                     # 前端类型检查
mypy src/openharness                 # 类型检查（strict）
```

---

## 39. 设计决策总览

### 39.1 架构模式

| 模式 | 应用位置 | 说明 |
|------|----------|------|
| **Provider 抽象** | API 层 | 统一协议接口屏蔽 23 个 Provider 差异 |
| **工具注册表** | 工具层 | 名称 → 实现映射，支持动态注册（MCP、插件） |
| **事件流** | 引擎 → UI | AsyncIterator[StreamEvent] 解耦引擎与 UI |
| **权限检查链** | 中间件 | 每次工具调用前多层拦截评估 |
| **Hook 系统** | 中间件 | 7 个生命周期事件 × 4 种 Hook 类型 |
| **Mailbox 通信** | Swarm | 文件级原子写入的 Agent 间异步消息 |
| **观察者模式** | State | AppStateStore.subscribe() 驱动 UI 更新 |
| **Pub-Sub 消息总线** | Channels | asyncio.Queue 解耦平台与 Agent |
| **Pydantic 验证** | 全局 | 类型安全的数据模型与输入验证 |
| **Async-first** | 全局 | 所有 I/O 操作均为异步 |
| **ContextVar 隔离** | Swarm in-process | per-task 状态不需要显式传参 |
| **双信号中止** | Swarm in-process | graceful + force kill 语义 |
| **后端注册表** | Swarm | 可插拔执行后端，优先级自动检测 |
| **状态机** | Autopilot | 任务状态流转（queued → merged/failed） |

### 39.2 关键设计决策

1. **工具循环驱动**：LLM 的 `stop_reason` 决定是否继续（`tool_use` → 循环，`end_turn` → 停止），而非预设步骤
2. **配置 4 级优先级**：CLI > ENV > 配置文件 > 默认值
3. **React TUI 为主 UI**：React 18 + Ink 5 而非纯 Python TUI，获得组件化和 Markdown 渲染能力
4. **Python 后端 + TypeScript 前端**：通过 JSON-lines 协议桥接，前后端解耦
5. **沙箱双后端 opt-in**：srt（macOS）+ Docker，不可用时优雅降级
6. **多 Agent 隔离**：Git Worktree 文件隔离 + Mailbox 通信隔离
7. **敏感路径硬编码保护**：SSH 密钥、云凭证路径硬编码保护，不依赖用户配置
8. **三级压缩策略**：Micro → Session → Full，平衡成本与上下文连续性
9. **模型继承**：子 Agent 使用 `model="inherit"` 继承父 session 模型，而非硬编码
10. **原子文件操作**：Mailbox 使用 `.tmp` → rename 模式防止并发读取不完整数据
11. **Shell escape 防注入**：Hook 中 `$ARGUMENTS` 替换做了 shell escape
12. **流式限制保护**：grep 工具设置 8MB asyncio 流限制，跳过超长行而非崩溃
13. **兼容性优先**：Skills 兼容 anthropics/skills，Plugins 兼容 claude-code plugins

---

## 40. 与 Claude Code 的对比

| 维度 | Claude Code | OpenHarness |
|------|-------------|-------------|
| 语言 | TypeScript | Python |
| Provider | 仅 Anthropic | 23 个 Provider |
| 开源 | 部分开源 | 完全开源（MIT） |
| 工具数 | 相似 | 43+（功能对齐） |
| 聊天集成 | 无 | Telegram / Slack / Discord / Feishu |
| 个人 Agent | 无 | ohmo 模块 |
| 沙箱 | macOS seatbelt | srt + Docker |
| 前端 | 内置 React TUI | React TUI（独立进程，JSON-lines 通信） |
| 多 Agent | 内置 | 4 种后端（subprocess / in-process / tmux / iTerm2） |
| Autopilot | 无公开 | 完整流水线（扫描 → 执行 → 验证 → PR → CI） |
| 定时调度 | 内置 | Cron 守护进程 + JSONL 历史 |
| 个性化 | 内置 | 环境事实自动提取 + 规则注入 |

---

## 附录 A: 实现级细节（Implementation Details）

> 本附录包含从零重建项目所需的算法、常量、协议精确定义和格式转换规则。

### A.1 系统提示词实际内容

`build_system_prompt()` 生成的基础指令包含 5 大 section：

```
You are OpenHarness, an open-source AI coding assistant CLI. You are an interactive
agent that helps users with software engineering tasks. Use the instructions below
and the tools available to you to assist the user.

IMPORTANT: You must NEVER generate or guess URLs for the user unless you are confident
that the URLs are for helping the user with programming. You may use URLs provided by
the user in their messages or local files.
```

**Section 结构**：

| Section | 内容要点 |
|---------|----------|
| `# System` | 工具执行规则、权限模式说明、prompt injection 处理、auto-compression 行为 |
| `# Doing tasks` | 软件工程任务指导：先读再改、最小化新文件、错误诊断优先于切换策略 |
| `# Executing actions with care` | 可逆性/影响范围评估、高风险操作需确认 |
| `# Using your tools` | 工具选择指导（Read 替代 cat，Edit 替代 sed，Grep 替代 grep 等） |
| `# Tone and style` | 简洁、代码引用格式 `file_path:line_number`、输出聚焦 |

`build_runtime_system_prompt()` 在此基础上追加：
- 环境信息（OS/Shell/Python/Git/venv 自动检测）
- Fast-mode 指引
- Effort/passes 设置
- SkillRegistry 可用技能列表
- Agent 委托指引
- Personalization 规则
- CLAUDE.md 内容（项目级 + 用户级）
- MEMORY.md + 相关记忆上下文

### A.2 权限检查器完整算法

**SENSITIVE_PATH_PATTERNS（硬编码，不可覆盖）**：

```python
SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    "*/.ssh/*",
    "*/.aws/credentials",
    "*/.aws/config",
    "*/.config/gcloud/*",
    "*/.azure/*",
    "*/.gnupg/*",
    "*/.docker/config.json",
    "*/.kube/config",
    "*/.openharness/credentials.json",
    "*/.openharness/copilot_auth.json",
)
```

**evaluate() 评估顺序（短路逻辑，命中即返回）**：

```
1. 敏感路径匹配 → fnmatch(SENSITIVE_PATH_PATTERNS) → denied（不可覆盖）
2. 工具黑名单 → denied_tools 列表 → denied
3. 工具白名单 → allowed_tools 列表 → allowed
4. 路径规则 → path_rules (glob ACL) 按序匹配 → denied if any deny
5. 命令黑名单 → denied_commands (fnmatch) → denied
6. FULL_AUTO 模式 → allowed
7. 只读工具 → allowed（read-only 默认放行）
8. PLAN 模式 → denied（阻止 mutation）
9. DEFAULT 模式 → requires_confirmation（bash 工具附加提示）
```

### A.3 Bash 工具实现细节

**常量**：
- 默认超时：**600 秒**（可通过 `timeout_seconds` 参数覆盖）
- 输出截断：**12,000 字符**（超出追加 `...[truncated]...`）
- 空输出返回：`(no output)`
- 终止流程：SIGTERM → 2s 等待 → SIGKILL

**交互命令检测**（`_preflight_interactive_command()`）：
- 检测关键词：`create-next-app`, `npm create`, `pnpm create`, `yarn create`, `bun create`, `npm init`, `pnpm init`, `yarn init`
- 排除非交互标志：`--yes`, `-y`, `--skip-install`, `--defaults`, `--non-interactive`, `--ci`
- 输出提示检测：`"would you like"`, `"ok to proceed"`, `"select an option"`, `"which"`, `"press enter to continue"`, `"?"`

**执行机制**：
```python
process = await asyncio.create_subprocess_shell(
    command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,  # 合并 stderr 到 stdout
    cwd=context.cwd,
)
# PTY 优先（prefer_pty=True）
# UTF-8 decode with errors="replace"
# \r\n → \n 规范化
```

### A.4 File Edit 工具算法

```python
def execute(arguments, context):
    content = Path(arguments.file_path).read_text("utf-8")

    if arguments.old_str not in content:
        return ToolResult(output="old_str not found", is_error=True)

    if arguments.replace_all:
        new_content = content.replace(arguments.old_str, arguments.new_str)
    else:
        new_content = content.replace(arguments.old_str, arguments.new_str, 1)  # 仅首次

    Path(arguments.file_path).write_text(new_content, "utf-8")
```

**注意**：无显式唯一性校验。依赖 LLM 选择足够唯一的 `old_str`。

### A.5 Grep 工具实现

**ripgrep 命令构建**：
```bash
rg --no-heading --line-number --color never [--hidden] [--glob <pattern>] [-i] -- <pattern> .
```

- `--hidden`：当 `.git` 或 `.gitignore` 存在时启用
- 流限制：`asyncio.create_subprocess_exec` 设置 `limit=8*1024*1024`（8MB）
- 超长行：`try/except ValueError` 跳过而非崩溃
- 结果限制：默认 200 行，最大 2000 行
- 超时标记：`__OPENHARNESS_GREP_TIMEOUT__:{timeout_seconds}`
- 无匹配返回：`"(no matches)"`

### A.6 Web Fetch 工具安全

**SSRF 防护**（`validate_http_url()`）：
- 仅允许 HTTP/HTTPS scheme
- 拒绝私有 IP（10.x, 172.16-31.x, 192.168.x）
- 拒绝 localhost 模式
- 返回 `(is_valid: bool, error_message: str)`

**内容提取**：
- 自定义 `_HTMLTextExtractor`（HTMLParser 子类）
- 跳过 `<script>` 和 `<style>` 标签（深度追踪）
- HTML 实体解码：`&nbsp;` → 空格, `&amp;` → `&`
- 空白规范化：`[ \t\r\f\v]+` 合并为单空格

**限制**：
- 默认 max_chars：**12,000**（范围 500-50,000）
- HTTP 超时：**15 秒**
- 最大重定向：**5 次**
- User-Agent：`OpenHarness/0.1.7`

### A.7 上下文压缩完整算法

**COMPACTABLE_TOOLS**（可清除旧输出的工具）：
```python
COMPACTABLE_TOOLS = frozenset({
    "read_file", "bash", "grep", "glob",
    "web_search", "web_fetch", "edit_file", "write_file",
})
```

**关键常量**：
| 常量 | 值 | 说明 |
|------|-----|------|
| AUTOCOMPACT_BUFFER_TOKENS | context_window - 20,000 - 13,000 | 触发阈值 |
| MAX_COMPACT_OUTPUT_TOKENS | 20,000 | LLM 摘要最大 token |
| COMPACT_TIMEOUT | 25 秒 | LLM 调用超时 |
| MAX_STREAMING_RETRIES | 2 | 流式重试次数 |
| MAX_PTL_RETRIES | 3 | "prompt too long" 重试次数 |
| DEFAULT_KEEP_RECENT | 5 | Microcompact 保留最近 N 个工具输出 |
| SESSION_MEMORY_KEEP_RECENT | 12 | Session Memory 保留最近 N 条消息 |
| PRESERVE_RECENT | 6 | Full Compact 保留最近 N 条 |

**BASE_COMPACT_PROMPT**（LLM 压缩指令）：

要求 LLM 输出两部分：
1. `<analysis>` 标签：按时间顺序遍历对话，提取用户请求、方法、代码/文件（含路径/行号）、错误、用户反馈
2. `<summary>` 标签：9 个结构化 section：
   - Primary Request and Intent
   - Key Technical Concepts
   - Files and Code Sections
   - Errors and Fixes
   - Problem Solving
   - All User Messages（精确原文）
   - Pending Tasks
   - Current Work
   - Optional Next Step

前后包裹 `NO_TOOLS_PREAMBLE`（禁止 LLM 使用工具）和 `NO_TOOLS_TRAILER`。

**Boundary Detection 算法**：
```python
def _boundary_crosses_tool_pair(msg_before, msg_after) -> bool:
    """检查切分是否会分离 tool_use 和对应的 tool_result"""
    # 如果 msg_before (assistant) 有未配对的 ToolUseBlock ID
    # 且 msg_after (user) 包含这些 ID 的 ToolResultBlock
    # → 返回 True（不可切分）

def _split_preserving_tool_pairs(messages, preserve_recent):
    split_index = len(messages) - preserve_recent
    # 向前回退，直到不再切断 tool_use/result 配对
    while _boundary_crosses_tool_pair(messages[split_index-1], messages[split_index]):
        split_index -= 1
    # 清理保留段中的孤立 tool_use blocks
```

**Context Collapse**：文本块 > 2,400 字符时 → 保留前 900 + `...[collapsed X chars]...` + 后 500 字符。

**PTL Retry**：每次重试删除 1/5 的 prompt 轮次（从头部截断）。

### A.8 Provider 注册表完整值

| # | name | keywords | env_key | backend_type | default_base_url | detect_prefix |
|---|------|----------|---------|-------------|-----------------|---------------|
| 1 | github_copilot | (copilot,) | — | copilot | — | — (OAuth) |
| 2 | openrouter | (openrouter,) | OPENROUTER_API_KEY | openai_compat | https://openrouter.ai/api/v1 | sk-or- |
| 3 | aihubmix | (aihubmix,) | AIHUBMIX_API_KEY | openai_compat | https://aihubmix.com/v1 | — |
| 4 | siliconflow | (siliconflow,) | SILICONFLOW_API_KEY | openai_compat | https://api.siliconflow.cn/v1 | — |
| 5 | volcengine | (volcengine, volces, ark) | VOLCENGINE_API_KEY | openai_compat | https://ark.cn-beijing.volces.com/api/v3 | — |
| 6 | anthropic | (anthropic, claude) | ANTHROPIC_API_KEY | anthropic | — | sk-ant- |
| 7 | openai | (gpt, o1, o3, o4) | OPENAI_API_KEY | openai_compat | — | sk- |
| 8 | deepseek | (deepseek,) | DEEPSEEK_API_KEY | openai_compat | https://api.deepseek.com/v1 | — |
| 9 | gemini | (gemini,) | GEMINI_API_KEY | openai_compat | https://generativelanguage.googleapis.com/v1beta/openai | — |
| 10 | dashscope | (qwen, dashscope) | DASHSCOPE_API_KEY | openai_compat | https://dashscope.aliyuncs.com/compatible-mode/v1 | — |
| 11 | moonshot | (moonshot, kimi) | MOONSHOT_API_KEY | openai_compat | https://api.moonshot.ai/v1 | — |
| 12 | minimax | (minimax,) | MINIMAX_API_KEY | openai_compat | https://api.minimax.io/v1 | — |
| 13 | zhipu | (glm, chatglm) | ZHIPU_API_KEY | openai_compat | https://open.bigmodel.cn/api/paas/v4 | — |
| 14 | groq | (groq,) | GROQ_API_KEY | openai_compat | https://api.groq.com/openai/v1 | gsk_ |
| 15 | mistral | (mixtral, codestral) | MISTRAL_API_KEY | openai_compat | https://api.mistral.ai/v1 | — |
| 16 | stepfun | (step,) | STEPFUN_API_KEY | openai_compat | https://api.stepfun.com/v1 | step- |
| 17 | baidu | (ernie, baidu) | BAIDU_API_KEY | openai_compat | https://qianfan.baidubce.com/v2 | — |
| 18 | bedrock | (bedrock,) | AWS_ACCESS_KEY_ID | openai_compat | — | — |
| 19 | vertex | (vertex,) | GOOGLE_APPLICATION_CREDENTIALS | openai_compat | — | — |
| 20 | ollama | (ollama,) | — | openai_compat | http://localhost:11434/v1 | — (is_local) |
| 21 | vllm | (vllm,) | — | openai_compat | — | — (is_local) |

**Provider 检测链**：
```python
def detect_provider_from_registry(api_key, base_url, model):
    # 1. API Key 前缀匹配（sk-ant- → anthropic, sk-or- → openrouter, gsk_ → groq, step- → stepfun）
    # 2. base_url 关键词匹配（url 中包含 provider.detect_by_base_keyword）
    # 3. model 名称关键词匹配（model 名包含 provider.keywords 中的任一）
    # 4. 优先级：按 PROVIDERS tuple 顺序
```

### A.9 API 格式转换规则

**Anthropic → OpenAI 工具 Schema**：
```python
# Anthropic:
{"name": "bash", "description": "...", "input_schema": {"type": "object", "properties": {...}}}
# → OpenAI:
{"type": "function", "function": {"name": "bash", "description": "...", "parameters": {"type": "object", "properties": {...}}}}
```

**消息格式转换**：
- System prompt → `{"role": "system", "content": "..."}` 作为第一条消息
- `ToolResultBlock` → `{"role": "tool", "tool_call_id": "...", "content": "..."}`
- Tool calls 通过 index-keyed dict 在流式中累积
- `reasoning_content` 字段用于思考模型，存储在 `msg._reasoning` 属性
- `<think>...</think>` 块从可见文本中通过正则剥离

**Token 限制分支**：
```python
if model.startswith(("gpt-5", "o1", "o3", "o4")):
    params["max_completion_tokens"] = max_tokens
else:
    params["max_tokens"] = max_tokens
```

**重试逻辑**：
- MAX_RETRIES = 3
- 可重试状态码：429, 500, 502, 503 + ConnectionError, TimeoutError, OSError
- 退避：`min(1.0 * 2^attempt, 30.0)` 秒

### A.10 UI 通信协议精确定义

**BackendEvent 类型（18 种）**：

| type | 携带字段 |
|------|----------|
| `ready` | state, tasks, mcp_servers, bridge_sessions, commands |
| `state_snapshot` | state, ?mcp_servers, ?bridge_sessions |
| `tasks_snapshot` | tasks |
| `transcript_item` | item: TranscriptItem |
| `compact_progress` | compact_phase, compact_trigger, attempt, compact_checkpoint, compact_metadata |
| `assistant_delta` | message (text chunk) |
| `assistant_complete` | message (final text) |
| `line_complete` | message |
| `tool_started` | tool_name, tool_input |
| `tool_completed` | tool_name, output, is_error |
| `clear_transcript` | (无) |
| `modal_request` | modal: dict |
| `select_request` | select_options |
| `todo_update` | todo_markdown |
| `plan_mode_change` | plan_mode: bool |
| `swarm_status` | swarm_teammates, swarm_notifications |
| `error` | message, is_error |
| `shutdown` | (无) |

**TranscriptItem 结构**：
```json
{
  "role": "system" | "user" | "assistant" | "tool" | "tool_result" | "log",
  "text": "...",
  "tool_name": "..." | null,
  "tool_input": {...} | null,
  "is_error": true | false | null
}
```

**FrontendRequest 类型（7 种）**：

| type | 字段 |
|------|------|
| `submit_line` | ?line |
| `permission_response` | ?allowed, ?request_id |
| `question_response` | ?answer, ?request_id |
| `list_sessions` | (无) |
| `select_command` | ?command |
| `apply_select_command` | ?command, ?value |
| `shutdown` | (无) |

### A.11 settings.json 完整 Schema

```json
{
  "api_key": "",
  "model": "claude-sonnet-4-6",
  "max_tokens": 16384,
  "base_url": null,
  "timeout": 30.0,
  "context_window_tokens": null,
  "auto_compact_threshold_tokens": null,
  "api_format": "anthropic",
  "provider": "",
  "active_profile": "claude-api",
  "profiles": {},
  "max_turns": 200,
  "system_prompt": null,
  "permission": {
    "mode": "default",
    "allowed_tools": [],
    "denied_tools": [],
    "path_rules": [],
    "denied_commands": []
  },
  "hooks": {},
  "memory": {
    "enabled": true,
    "max_files": 5,
    "max_entrypoint_lines": 200,
    "context_window_tokens": null,
    "auto_compact_threshold_tokens": null
  },
  "sandbox": {
    "enabled": false,
    "backend": "srt",
    "fail_if_unavailable": false,
    "enabled_platforms": [],
    "network": {},
    "filesystem": {},
    "docker": {}
  },
  "enabled_plugins": {},
  "allow_project_plugins": false,
  "mcp_servers": {},
  "theme": "default",
  "output_style": "default",
  "vim_mode": false,
  "voice_mode": false,
  "fast_mode": false,
  "effort": "medium",
  "passes": 1,
  "verbose": false
}
```

**PermissionMode 枚举**：`DEFAULT`, `PLAN`, `FULL_AUTO`（映射标签：`"Default"`, `"Plan Mode"`, `"Auto"`）

**内置 Provider Profiles**：`claude-api`, `claude-subscription`, `openai-compatible`, `codex`, `copilot`, `moonshot`, `gemini`, `minimax`

**模型别名**：`default` → sonnet, `best` → opus, `opusplan` → opus (plan 模式) / sonnet (其他)

### A.12 记忆搜索评分算法

```python
def search(query: str, memories: list[MemoryHeader], max_results: int = 5) -> list:
    tokens = tokenize(query.lower())
    # ASCII: regex [A-Za-z0-9_]+, 最小 3 字符
    # Han: Unicode \u4e00-\u9fff + \u3400-\u4dbf, 每字符独立 token

    scored = []
    for mem in memories[:100]:  # 最多扫描 100 条
        meta_text = f"{mem.title} {mem.description}".lower()
        body_text = (mem.body_preview or "").lower()

        meta_hits = sum(1 for t in tokens if t in meta_text)
        body_hits = sum(1 for t in tokens if t in body_text)

        score = meta_hits * 2.0 + body_hits  # metadata 权重 2x
        if score > 0:
            scored.append((score, mem.modified_at, mem))

    scored.sort(key=lambda x: (-x[0], -x[1]))  # 分数降序，同分按时间降序
    return [item[2] for item in scored[:max_results]]
```

### A.13 Hook 执行器细节

**匹配算法**：
```python
def _matches_hook(hook, subject: str) -> bool:
    if hook.matcher is None:
        return True  # 无 matcher = 匹配所有
    return fnmatch.fnmatch(subject, hook.matcher)
# subject = tool_name | prompt | event | ""
```

**执行流程**：
1. 从 `HookRegistry.get(event)` 按注册顺序获取
2. `_matches_hook()` 过滤
3. 按类型分发执行：
   - Command：`asyncio.create_subprocess_shell()`，`$ARGUMENTS` 用 `shlex.quote()` 替换
   - HTTP：`httpx.AsyncClient.post(url, json=payload, timeout=hook.timeout_seconds)`
   - Prompt/Agent：LLM 调用验证
4. 超时处理：SIGTERM → 2s → SIGKILL
5. `block_on_failure=True` 时：失败/超时 → `HookResult(blocked=True, reason=...)`

### A.14 Swarm Mailbox 原子写入

```python
async def write(msg: TeammateMessage):
    filename = f"{time.time():.6f}_{msg.id}.json"
    final_path = inbox_dir / filename
    tmp_path = inbox_dir / f".{filename}.tmp"

    async with exclusive_file_lock(lock_path):
        # 1. 写入临时文件
        tmp_path.write_text(json.dumps(msg.to_dict(), indent=2))
        # 2. 原子重命名
        os.replace(tmp_path, final_path)

async def read_all(unread_only=True) -> list[TeammateMessage]:
    # 按文件名排序（timestamp_id 自然排序）
    # 跳过 .tmp 和 dotfiles
    # JSON 解析失败静默跳过
    # unread_only=True 时过滤 msg.read == False
```

**消息类型枚举**：
```python
"user_message" | "permission_request" | "permission_response" |
"sandbox_permission_request" | "sandbox_permission_response" |
"shutdown" | "idle_notification"
```

### A.15 凭证存储实现

- **文件**：`~/.openharness/credentials.json`
- **权限**：`mode 0o600`（仅 owner 读写）
- **锁**：`credentials.json.lock` 独占文件锁
- **无加密**：XOR 混淆（`_obfuscate/_deobfuscate`）是可逆 round-trip，**不是加密**
- **Keyring 集成**：
  - Service name：`"openharness"`
  - Key 格式：`"{provider}:{key}"`
  - 可用性探测：`keyring.get_password("openharness", "__probe__")`，缓存结果
  - Fallback：ImportError 或异常 → 回退文件存储 + 警告

### A.16 Agent 工具子 Agent 生成

```python
async def execute(arguments, context):
    # 1. 模型解析：arguments.model > agent_definition.model > None
    # 2. 团队分配：arguments.team or "default"
    # 3. 子 Agent 类型查找：get_agent_definition(arguments.subagent_type)
    #    → 获取 system_prompt, permissions, model
    # 4. 生成 TeammateSpawnConfig
    # 5. 通过 subprocess executor 生成（BackgroundTaskManager 注册）
    # 6. 注册完成回调：SUBAGENT_STOP hook 事件
    # 7. task_type: local_agent | remote_agent | in_process_teammate
```

**Worktree 隔离**：通过 `arguments.isolation = "worktree"` 触发，创建独立 Git Worktree，Agent 在隔离副本中工作。无更改时自动清理。

---

## 附录 B: 重建检查清单

从零重建本项目时，建议按以下顺序实现：

| 阶段 | 模块 | 依赖 |
|------|------|------|
| 1 | config, platforms | 无 |
| 2 | api (client + registry) | config |
| 3 | engine (messages, query_engine, run_query) | api |
| 4 | tools/base + ToolRegistry | engine |
| 5 | permissions/checker | config |
| 6 | hooks (schemas, loader, executor) | config |
| 7 | tools/* (43+ 工具实现) | tools/base, permissions, hooks |
| 8 | prompts (system_prompt, environment) | config, skills |
| 9 | memory (manager, scan, search) | config |
| 10 | skills (types, registry, loader) | config |
| 11 | plugins (manifest, loader) | skills, hooks, tools |
| 12 | commands/registry | engine, tools, skills |
| 13 | services/compact | engine |
| 14 | services/session_storage | engine |
| 15 | services/cron | config |
| 16 | tasks/manager | 无 |
| 17 | state + themes + keybindings | config |
| 18 | ui/protocol + backend_host | state, engine |
| 19 | ui/app (REPL + print mode) | ui/protocol, all above |
| 20 | cli.py | ui/app, all above |
| 21 | mcp/client | config, tools |
| 22 | sandbox (adapter, docker) | config, permissions |
| 23 | swarm (registry, mailbox, backends) | tasks, tools, permissions |
| 24 | coordinator | swarm |
| 25 | bridge | engine |
| 26 | channels (bus, bridge) | engine |
| 27 | personalization | config |
| 28 | voice, vim | state |
| 29 | autopilot | engine, tasks, swarm |
| 30 | auth (storage, flows, manager) | config |
| 31 | frontend/terminal (React TUI) | ui/protocol |
| 32 | ohmo/* | engine, channels, memory, prompts |
| 33 | autopilot-dashboard | autopilot (数据格式) |

---

*本规格文档由逆向工程分析生成，基于 OpenHarness v0.1.7 源码。覆盖 36 个子模块、43+ 工具、23 个 Provider、21 个 TSX 组件、107+ 测试文件。附录包含从零重建所需的全部算法、常量和协议定义。*
