# OpenHarness

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

[![CI](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/maisieyang/build-my-own-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **一个用 Python 从零重建、做到生产级标准的 LLM agent harness——
> 每个子系统亲手拥有，每个取舍留有记录。**
> *What I cannot create, I do not understand.*

---

## 什么是 harness？

LLM 只能"说话"：你给它一段 prompt，它吐回一段文本。要让它**做事**、
**记得事**、**不出事**，必须在它外面包一层。这层东西就叫 harness。

整个仓库的最底下是一个循环：

```
while True:
    stream = llm.stream(messages)          # API 流式调用
    parse tool_use blocks                  # 模型想做什么？
    for each tool_use:
        check permission                   # 拦
        execute tool                       # 做
        append tool_result to messages     # 喂回
    if stop_reason == "end_turn": break    # 模型说"做完了"
```

这就是 `engine/run_query`——心脏。**LLM 自己就是编排器，循环只负责把它的
输出变成动作、再把结果喂回去。** 仓库里其余的一切之所以存在，是因为真的
让这个循环无人值守地跑在真实代码库上，会暴露出一条问题链——每个子系统
都是其中一个问题的修复。

## 问题链（里面有什么，以及为什么有）

下面每一环的存在，都是因为上一环暴露了它。

**1. 循环跑起来了——但不能让模型想干啥干啥。**
工具调用在派发**之前**过权限闸：硬编码敏感路径 deny、glob deny 规则、
mode 覆盖（`--auto` / `--dry-run`），以及对不可逆 git 动作的无条件红线。
覆盖生命周期事件的 **hooks** 中间件链（`hooks/`）可以 deny / modify /
observe 每一步——它也是扩展缝：压缩、mode bundle、插件全挂在它上面。
→ `permissions/` · `hooks/`

**2. session 变长——context window 会爆。**
逐工具结果截断 + 反应式 `PromptTooLong` 恢复，让循环活过窗口上限。
→ `compaction/` · `services/`

**3. session 结束——模型全忘了。**
Claude-Code 式自动记忆：LLM 自己决定什么值得记住，按项目持久化成
Markdown，下个 session 拿回来。由一个多轮 eval 把关，`oh memory` 可查。
→ `memory/` · `markdown_store/`

**4. 加能力不应该动核心。**
四种扩展机制，全部汇入同一个工具注册表和 hook 目录：**skills**（懒加载
的 Markdown 经验）、**slash 命令**（用户自写的 `/<name>` prompt）、
**插件**（第三方 Python，经 entry points 或投放 `.py`，兼容 Claude Code
插件格式）、**MCP**（Model Context Protocol，stdio）。**Mode bundle**
把 prompt + 工具白名单 + deny + hooks 组合成一个可切换的模式。
→ `skills/` · `commands/` · `plugins/` · `mcp/` · `bundles/`

**5. 一个 agent 的 context 不够用。**
`SpawnAgent` 把 agent 循环自己变成一个工具——递归委派，带深度上限 +
不可变的上下文继承。
→ `tools/spawn_agent.py`

**6. 执行模型选的命令，可能毁掉宿主机。**
opt-in 的进程级隔离：`--sandbox` 走 Docker，`--sandbox-runtime runsc` 走
gVisor，藏在 `ExecutionEnvironment` protocol 后面——engine 永远不知道
自己跑在哪种基底上。
→ `execution/`

**7. 但每一轮迭代，仍然要一个人坐在椅子上。**
交互式 CLI 留下三把被人占着的椅子：*规划*、*验证*、*把关*。
**loop-runtime** 层把三把椅子全部交给 harness：

```bash
oh ask -p "fix the failing tests; do not touch assertions" \
  --output-format json --verify "pytest -q" --max-iter 5 --isolate
```

无头 print mode（`-p`，JSON / stream-JSON，退出码区分成败）是原子。
**验证闸**确定性地判定"做完了没有"——命令退出码（`--verify`），或语义
标准交给独立 LLM 裁判（`--goal-condition`）；模型的自我感觉永远不是闸。
外层**修复循环**每轮把闸的反馈重喂进一个*全新*的 context，直到闸过或撞
迭代上限。围绕它：声明式无头权限策略（fail-closed，无 TTY 弹窗）、goal
自拆解（`--decompose`）、cron 式接单队列（`oh autopilot`）、git worktree
隔离（`--isolate`）、append-only 的 per-run journal——run 可恢复
（`--resume-run`）、可查看（`oh run show`）。goal 和验收写一次——然后走开。
→ `verification/` · `services/`（worktree · run journal · run session）· `cli.py`

这一切的底下：OpenAI-compatible 流式 client、Pydantic v2 wire 类型、
`OPENHARNESS_*` 配置、带 `run_id` / `turn_id` / `agent_depth` 的结构化
JSON 日志——整条 trace 用 `jq` 就能重建。Provider 无关：同一个循环跑
Qwen、DeepSeek 或任何 OpenAI 兼容端。
→ `api/` · `protocols/` · `config/` · `prompts/` · `observability/`

## 快速开始

需要 Python ≥ 3.10 和 [uv](https://docs.astral.sh/uv/)。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # 1. 安装 uv（一次性）

git clone https://github.com/maisieyang/build-my-own-harness.git
cd build-my-own-harness && uv sync                  # 2. clone + sync

cp .env.example .env && $EDITOR .env                # 3. 填 OPENHARNESS_API_KEY + BASE_URL
                                                    #    （任何 OpenAI 兼容端）
uv run oh                                           # 4. 进入 REPL(打 / 弹命令菜单)
uv run oh ask "list 5 git commands"                 #    或单发提问
```

所有配置都是 `OPENHARNESS_*` 环境变量（经 `pydantic-settings`）；见
[`.env.example`](./.env.example)。完整命令面：`oh --help`（`ask` /
`chat` / `tools` / `config` / `hooks` / `memory` / `plugins` /
`snapshot` / `eval` / `autopilot` / `run`）。

## 质量契约

- 全量 `src/` 过 `mypy --strict` · `ruff` lint + 格式干净 ·
  稳定核心 **≥95% 覆盖率门禁**
- CI 在 **Python 3.10 和 3.11** 上跑 lint + 类型检查 + 全量测试
  （[`ci.yml`](./.github/workflows/ci.yml)）
- 测试**零外部依赖**即可通过；集成/沙箱测试按 env var / Docker / gVisor
  把关，缺失则干净跳过
- TDD 作为纪律：先写测试、**亲眼见红**，再写代码到绿——没见过红的绿
  是假绿
- 差异化错误，默认模式下无裸 Python traceback

## 它是怎么建起来的

单人开发者 + Claude Code。人留在契约层——范围、取舍、验收标准；agent
驱动实现。从零造起，至今仍在迭代。

光读代码拿不到的东西：**三条 append-only trail** 保存了每个模块完整的
设计现场，*围绕*代码写下，绝不事后补——

| Trail | 是什么 | 何时写 |
|---|---|---|
| [`decisions/`](./decisions) | 边界文档——范围内是什么、范围外是什么、守哪条不变量 | 每个 phase **之前** |
| [`tasks/`](./tasks) | capability 粒度的 plan（绝不下沉到子任务粒度） | 每个 phase **之前** |
| [`learnings/`](./learnings) | 复盘——哪些抽象站住了、下一步预测什么 | 每个 phase 发布**之后** |

项目里任何一个设计决策，都能按三联顺序重建现场：boundary → plan →
retro。入口：[`tasks/README.md`](./tasks/README.md)（phase 索引），或
[`learnings/openharness-first-principles.md`](./learnings/openharness-first-principles.md)
（本 README 叙事的指南针）。[`REFERENCE.md`](./REFERENCE.md) 是逆向上游
项目冻结下来的认知地图——所有建造都对着它防玩具化。

## 更大的图景

这个 harness 是基座层。同一个"造出来才算懂"的动作贯穿三个仓库：

- **harness** → **build-my-own-harness**（你在这）——生产级标准的
  agent runtime。
- **plugin** → [**finance-skills**](https://github.com/maisieyang/finance-skills)
  ——同一个动作跑进垂直：研究 Anthropic 开源的 `financial-services`
  skills，再从零造
  [`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk)。
  它就跑在这个 harness 上。
- **method** → [**my-skills**](https://github.com/maisieyang/my-skills)
  ——工作方法本身，编码成可复用的 skills（fork 自 agent-skills，只编码
  基座没有的）。

## 致谢

名称与模块词汇承袭自
[**HKUDS/OpenHarness**](https://github.com/HKUDS/OpenHarness)（MIT）——
最初的 Python LLM harness。本仓库是**独立、从零的重新实现**，作为学习
artifact 而建。[`REFERENCE.md`](./REFERENCE.md) 捕获上游 v0.1.9 spec 作为
研究标的，非拷贝源。

## License

MIT —— 见 [LICENSE](./LICENSE)。
