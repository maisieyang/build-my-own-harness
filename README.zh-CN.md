# OpenHarness — 生产级 Python LLM Agent Harness

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

[![CI](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **生产级 Python LLM Agent Harness —— 一个人从零到一的 Harness 工程化实验。**
>
> > *"What I cannot create, I do not understand."* — **Richard Feynman**
>
> 想在 Harness 这个领域真正成为专家，在**工程实现** 与 **产品 trade-off** 两条轴上都拥有 first-principles 判断，光读 Claude Code / Codex / LangChain 的源码不够，必须自己从空目录搭一个 production-grade 的出来。这个项目就是这个想法的落地 —— 单人 (开发者 + Claude Code 协作)，多 phase 迭代，每一步保留 boundary doc → plan → execute → retro 的完整 trail。

---

## 它做了什么

一份完整的 Python LLM agent runtime,覆盖以下基础设施:

- Agent loop
- tools / skills / commands / bundles / plugins / mcp
- web tools (WebSearch + WebFetch)
- memory / session resume / focus state
- permissions / hooks / sandbox
- sub-agent dispatch
- context management (4-tier auto-compaction)
- provider workflows
- TUI rendering (rich.Live spinner)
- ⭐ eval substrate (Sample / Score / Scorer Protocol)

---

## 为什么做这个

这是一个**deliberate learning project** —— 不是 OSS 社区竞品，不是商业产品，是为了**用建造的方式真正掌握 Harness 领域**。

Harness (Anthropic 提出的概念) = 围绕模型构建的全部工程脚手架 —— 工具调度、状态管理、权限、观测、可扩展性、错误恢复。Claude Code、Cursor、LangChain、Aider 都是 harness。它们都在解同一类问题，但在 trade-off 上做了不同的取舍。

只读源码很难看清这些取舍**为什么是这样**。自己从零搭一遍才能体会:

- **为什么 tool result 要 LLM-visible 而不是 engine 内部 retry?** —— 跑通 Bash → 调通 WebFetch → 试着 engine 自动重试 → 发现 LLM 自己决定怎么处理失败远比硬编码 retry 健壮。这是产品 trade-off。
- **为什么 sub-agent 用 Protocol + 不可变 context 继承?** —— Phase 6 spawn → Phase 7c 加 gVisor 沙箱时第二个实现只用了首个 12% 的代码量。这是工程结构的复利。
- **为什么 memory extraction 默认要给 stub LLM 测试 testability tax?** —— Phase 11 默认 ON 把所有 stub 测试一次性污染了。这是测试与功能的冲突点。

这类**只能 build-then-see 的判断**是这个项目的真正产物。代码本身可以参考开源；判断不行。

---

## 详细能力分块

### 1. Agent 引擎

`run_query()` 是 async iterator，by `stop_reason` 驱动循环 (`end_turn` / `tool_use` / `max_tokens` / `stop_sequence`)，**caller 的 `initial_messages` 永不 mutate**。Streaming events 解耦 UI 渲染:`ApiTextDeltaEvent` / `ToolUseEvent` / `ToolResultEvent` / `ApiMessageCompleteEvent` / `ConversationCompleteEvent`。

API client 走 OpenAI-compatible wire format，自带 `httpx + openai SDK` retry + 错误分类层 (`AuthenticationFailure` / `RateLimitFailure` / `RequestFailure` / `PromptTooLongFailure` / `MalformedToolCallFailure`)。

### 2. 工具系统

- **6 内置 tool**：`Read` / `Write` / `Edit` / `Bash` / `Grep` 实现 BaseTool 模式；`Agent` (sub-agent) 通过 `dataclasses.replace` 不可变继承 context，带 depth limit (默认 3)。
- **2 网络 tool**：`WebSearch` 通过 `WebSearchProvider` Protocol 抽象 (v1 装 Tavily，可换 Brave / Serper)；`WebFetch` httpx streaming GET + BeautifulSoup chrome strip (`script` / `style` / `nav` / `aside` / `header` / `footer` decompose) + markdownify HTML → markdown。
- **每个 tool 强类型**：`BaseTool[InputT extends BaseModel]` Pydantic schema + `ToolResult` 结构化返回。
- **MCP 适配器**：stdio transport，第三方工具服务器透明注册进同一个 `ToolRegistry`。

### 3. 扩展点 + 用户内容

- **Slash commands** —— 写 `~/.openharness/commands/<name>.md` 注册 `/<name>` 命令，frontmatter 支持参数模板。
- **Skills** —— `~/.openharness/skills/<name>.md` 是专家文档；LLM 看到 catalog (只有 name + description)，决定相关时 `LoadSkill(name)` 主动加载，惰性节省 context。
- **Mode Bundles** —— 把"系统提示词 + tool whitelist + deny_paths + 命名 hooks" 复合成命名"模式"，slash command 的 `mode:` frontmatter 引用。
- **Plugins** —— 统一 `~/.openharness/plugins/<name>/manifest.toml` 注册 hooks / skills / commands / bundles，`<plugin>__<hook>` namespacing 防冲突，`--enable-plugins` opt-in。

### 4. 状态管理

- **Memory** —— YAML-frontmatter 文件，3 scope (user / project / team)，relevance scoring (meta hits + body hits + importance + recency)，零 token 命中直接 drop。
- **LLM extraction (write path)** —— 每 turn 后台 LLM 抽取候选 memory，signature dedupe，team scope 走 6 模式 secret scanner (PEM / AWS / GitHub / Anthropic / OpenAI / 通用) 拦截泄漏。
- **Sessions** —— 每 turn 写 `~/.openharness/snapshots/<cwd-hash>/current.json` (atomic via `tempfile + os.replace`)，`--resume` / `--resume-id` 跨进程恢复 `QueryContext.from_snapshot()`，旧 `current.json` 自动轮转到 `history/<git-head>-<utc-ts>.json` (count + age 双阈值 GC)。
- **LLM-authored task focus state** —— `--llm-focus-state` 启动，每 turn 二级 LLM call 推断当前 task / 下一步 / blockers 写入 `tool_metadata.task_focus_state`，opt-in (默认 OFF) 避免 stub-LLM testability tax。
- **4-tier auto-compaction**：
  - L0: token 估算
  - L2: deterministic head/tail 折叠 (head 900 / tail 500 messages)
  - L3: session-memory checkpoint 复用 (1h freshness window)
  - L4: LLM-driven 9-slot 全量压缩

### 5. 安全 + 可观测

- **3-tier permissions** —— 硬编码敏感路径 deny + 用户 glob (`OPENHARNESS_DENY_PATHS`) + 模式覆盖 (`--auto` / `--dry-run`)。
- **5 lifecycle hooks** —— `PreToolUse` / `PostToolUse` / `PreApiCall` / `PostApiCall` / `OnError`，deny / modify / allow 语义，`HookSpec.re_run_on_reactive_rebuild` 字段支持 PTL drop-oldest 后选择性重跑。
- **Sandbox** —— `--sandbox` 启动 Docker 容器 (cwd bind-mount + `network=none` + cgroup memory/cpu/pids 限制)；`--sandbox-runtime runsc` 走 gVisor 用户态 syscall 隔离。
- **结构化日志** —— `structlog` 绑定 `run_id` / `turn_id` / `agent_depth`，`OPENHARNESS_LOG_FORMAT=json` 用 `jq` 重建完整调用链。
- **rich.Live TTY spinner** —— 工具调度时屏幕实时动画 (TTY-only 检测，CI / pipe 自动 fallback 纯文本)。

### 6. ⭐ Eval substrate (`src/openharness/eval/`)

> LLM 项目最容易忽视的能力 —— agent capability 的改进，靠 vibes 还是靠 metric？这个 substrate 提供"靠 metric"的工程基础。

- **接口契约**:
  - `Sample` (dataclass) —— 一条评估样本，含 `id` / `input` / `expected` / `meta`。
  - `Score` —— 一次评分结果，`value: float` (0-1) + `passed: bool` + `details`。
  - `Scorer` (Protocol) —— `async def score(sample, actual) -> Score`。
- **4 类 scorer 组合 (覆盖 90% 评估需求)**:
  - `ParseOkScorer` —— 结构性 assertion (输出能否被解析？JSON valid？schema match？)
  - `GoalKeywordMatchScorer` —— 关键词匹配 baseline
  - `CapabilityAssertionsScorer` —— 字段级 assertion (`field == expected`)
  - `CapabilityLLMJudgeScorer` —— LLM-as-judge，自然语言 rubric
- **Dataset 形态** —— `evals/<service>/dataset.yaml` (cases) + `dataset_card.md` (采样方法 / 样本数 / 已知偏差)
- **Async runner** —— `run_eval(cases, scorers, executor)` 一次跑 N case × N scorer，输出 `CaseResult` 列表
- **跨模型稳定性档案** —— 同 dataset 跑 N=4 model 看 case 通过率 → 区分 "model bug" vs "harness bug" vs "case 设计有歧义"
- **首个 consumer** `evals/focus_state/` —— 验证 Phase 13 LLM-authored 元数据准确率，Day 1 (2026-06-03) spike 出来 4 ✓ + 1 ⚠ + 1 ✗ 的 cross-model stability baseline
- **设计原则** —— substrate 永远不知道具体 capability 含义，service-specific eval 各自写 `evals/<service>/`；Stage 2+ 演进时 substrate 接口不动 (Phase 11 substrate 跨 6 个 phase 7 个 consumer 零修改的复利模式延续到 eval)

### 7. CLI + Provider

- **`oh ask "<prompt>"`** —— 单次流式，默认 max_tokens 8192
- **`oh chat`** —— 多轮 REPL，gnureadline (macOS GNU readline 替代 libedit) + chat-aware base instructions ("Match response length to user intent")
- **7 introspection 子命令** —— `oh tools list/show` / `oh config show/edit` / `oh hooks list/describe` / `oh memory list/show/path` / `oh snapshot list/show/gc`
- **Provider 抽象** —— `OPENHARNESS_BASE_URL` 一改即切 (DeepSeek / Qwen via DashScope / Anthropic 兼容端 / Moonshot)，OpenAI-shape wire format 不动代码

---

## Production-grade 基线

| | |
|---|---|
| Tests | **2031** on CI (Python 3.10 / 3.11) |
| 类型检查 | `mypy --strict src/` clean (105 source files) |
| Lint | `ruff` check + format clean |
| 覆盖率 | **≥95%** gate held |
| CI | GitHub Actions matrix |
| 当前版本 | **v0.3.0** (per-release notes 在 [CHANGELOG.md](./CHANGELOG.md)) |

---

## 快速开始

需要 Python ≥3.10 和 [uv](https://docs.astral.sh/uv/)。

```bash
# 1. 装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. clone + sync
git clone https://github.com/maisieyang/build-my-own-harness.git
cd build-my-own-harness
uv sync

# 3. 装全局 oh (editable;改代码立即生效)
uv tool install --editable .

# 4. 配置 ~/.openharness/.env (任何目录跑 oh 都用同一份)
mkdir -p ~/.openharness
cat > ~/.openharness/.env <<EOF
# Provider 任选一家 OpenAI-compatible:
OPENHARNESS_API_KEY=<your-llm-key>
OPENHARNESS_BASE_URL=https://api.deepseek.com/
OPENHARNESS_MODEL=deepseek-chat

# Web tools (可选,装了 oh 自动用):
OPENHARNESS_WEB__API_KEY=<your-tavily-key>
EOF

# 5. 跑
oh ask "你好"
oh chat
oh ask "调研一下 GPT-5 最近的更新"   # 自动调 WebSearch + WebFetch
```

---

## 项目的另一面 —— Process artifact

这个项目除了能跑的 harness，还是**整个 Harness 设计过程的完整可读 trail**。每个 phase 都有:

- [`decisions/<NN>-phase-X-boundary.md`](./decisions/) —— 进入 phase 前 lock 的 invariant + 关键决策 + alternatives 评估
- [`tasks/phase-X-plan.md`](./tasks/) —— capability 级 plan (故意不细化到 sub-task)
- [`learnings/phase-X.md`](./learnings/) —— ship 后复盘 + framework 级 lesson

两份方法论 playbook:

- [`PLAYBOOK.md`](./PLAYBOOK.md) —— 工程师视角，4-step phase 循环 + 5 lesson + 3 anti-pattern (~6500 字)
- [`PLAYBOOK-PM.md`](./PLAYBOOK-PM.md) —— PM 视角，LLM Harness 产品决策框架 + 评测 + 灰度 + 跨角色协作 (~18500 字)

如果只能读一个文件了解方法论 → [`learnings/phase-7.md`](./learnings/phase-7.md) (v0.1.0 close-out meta-retro)。

---

## License

MIT。详见 [LICENSE](./LICENSE)。
