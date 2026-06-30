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

> **一套用 Python 从零重建的 LLM agent harness，做到生产级标准——每个子系统、每个取舍都亲手拥有。**

你给一个 prompt，它流式驱动 LLM，LLM 选工具，harness 安全地执行，循环持续到模型说"做完了"。一个严肃的 agent runtime 该有的都在——流式工具循环、三层权限、hook 中间件、结构化可观测、Docker/gVisor 沙箱、slash 命令、插件、递归子 agent、多轮 REPL、capability 锚定的 eval substrate、Claude-Code 式自动记忆。Provider 无关：同一个循环跑 Qwen、DeepSeek 或任何 OpenAI 兼容端。`mypy --strict`、`ruff` 干净、≥95% 覆盖率门禁。

---

## 架构

三层关注点，按垂直切分：

1. **Engine**（`engine/`）—— `run_query` 是一个流式吐 `ApiStreamEvent` 的异步生成器。每轮：发消息 → 处理 `tool_use` stop reason → 派发工具 → 追加结果 → 循环到 `end_turn`。caller 的 `initial_messages` 永不被 mutate（防御性不可变）。
2. **Tools**（`tools/`）—— `BaseTool` 抽象基类 + Pydantic 校验的输入 schema；`ToolRegistry` 是 engine 内省的目录。权限检查在派发**之前**跑。
3. **Hooks**（`hooks/`）—— 覆盖 5 个生命周期事件的中间件链；hook 可 deny / modify / observe。它是扩展缝：压缩、mode bundle、插件全挂在它上面。

---

## 里面有什么

按子系统（`src/openharness/`）：

- **流式工具循环**（`engine/`）—— 心脏；LLM 驱动，`end_turn` 终止。
- **工具**（`tools/`）—— `Read` / `Write` / `Edit` / `Bash` / `Grep`，Pydantic 校验出入参。
- **权限**（`permissions/`）—— 硬编码敏感路径 deny + glob deny + mode 覆盖（`--auto` / `--dry-run`）。
- **Hooks**（`hooks/`）—— 5 个生命周期事件，deny/modify/allow；驱动自动截断；对插件开放。
- **可观测**（`observability/`）—— JSON 日志带 `run_id` / `turn_id` / `agent_depth`，可用 `jq` 重建 trace。
- **MCP**（`mcp/`）—— Model Context Protocol（stdio），注册第三方工具 server。
- **Slash 命令**（`commands/`）+ **Skills**（`skills/`，懒加载目录）+ **ModeBundle**（`bundles/`，组合 prompt+工具+deny+hook）。
- **插件 hook**（`plugins/`）—— 第三方 Python，经 entry points 或投放 `.py`；opt-in。
- **子 agent**（`engine/`）—— 递归 `SpawnAgent` 带深度上限；上下文经 `dataclasses.replace` 不可变继承。
- **沙箱**（`execution/`）—— Docker via `--sandbox`，运行时可选（`runc` / `runsc` gVisor）。
- **REPL** —— `oh chat` 经 `ConversationCompleteEvent` 跨轮累积历史。
- **压缩**（`compaction/` + `services/`）—— L1 逐工具结果截断 + L2 反应式 PromptTooLong 恢复。
- **Eval substrate**（`eval/`，*experimental*）—— `Sample`/`Score`/`Scorer` + scorer（程序化 + LLM-judge）+ cassette 录制/回放 + 版本戳结果 + `oh eval`。两个 consumer 已落地；该子系统在稳定前不纳入覆盖率门禁。
- **自动记忆**（`memory/`）—— LLM 自行决定何时持久记住；两步内联 `Write` + `Edit` `MEMORY.md`；按项目存储。由一个多轮 eval 把关。
- **基座**（`api/` · `protocols/` · `config/` · `prompts/` · `markdown_store/`）—— OpenAI-compatible client + 流式翻译、Pydantic v2 wire 类型、`OPENHARNESS_*` 配置、system prompt、共享 Markdown store。

---

## 质量门禁

- 全量 `src/` 跑 `mypy --strict` · `ruff` lint + 格式干净 · **≥95% 覆盖率门禁**
- CI 在 **Python 3.10 和 3.11** 上跑 lint + 类型检查 + 全量测试（[`ci.yml`](./.github/workflows/ci.yml)）
- 测试**零外部依赖**即可通过；集成/沙箱测试按 env var / Docker / gVisor 把关，缺失则干净跳过
- 差异化错误，默认模式下无 Python traceback（配置错误 / 401 / 429 / 循环轮次上限各自给出独立信息）

---

## 快速开始

需要 Python ≥ 3.10 和 [uv](https://docs.astral.sh/uv/)。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # 1. 安装 uv（一次性）

git clone https://github.com/maisieyang/build-my-own-harness.git
cd build-my-own-harness && uv sync                  # 2. clone + sync

cp .env.example .env && $EDITOR .env                # 3. 填 OPENHARNESS_API_KEY + BASE_URL
                                                    #    （任何 OpenAI 兼容端）
uv run oh ask "list 5 git commands"                 # 4. 提问
uv run oh chat                                       #    或多轮 REPL
```

所有配置都是 `OPENHARNESS_*` 环境变量（经 `pydantic-settings`）；见
[`.env.example`](./.env.example)。完整命令面：`oh --help`（`ask` / `chat` /
`tools` / `config` / `hooks` / `memory` / `eval`）。

---

## 它是怎么建起来的

单人开发者 + Claude Code：人留在契约层（范围、取舍、验收），agent 驱动实现——**从零造起、至今仍在迭代，~7 周、20 个子系统、300+ commits、solo**。

---

## 项目的总览

这个 harness 是基座层。还有应用层，我写了一个plugin，它可以在这个Harness上跑。

- **harness** → **build-my-own-harness**（你在这，对标生产级的 harness。）
- **plugin** → [**finance-skills**](https://github.com/maisieyang/finance-skills) —— 同一个动作跑进垂直：研究 Anthropic 开源的 `financial-services`，再从零造 [`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk)。
- **my-skills** → [**my-skills**](https://github.com/maisieyang/my-skills) —— 方法本身，被编码成可复用 skill（fork 自 agent-skills，只编码基石没有的）。

---

## 致谢

名称与模块词汇承袭自 [**HKUDS/OpenHarness**](https://github.com/HKUDS/OpenHarness)（MIT）——最初的 Python LLM harness。本仓库是**独立、从零的重新实现**，作为学习 artifact 而建。[`REFERENCE.md`](./REFERENCE.md) 捕获了上游 v0.1.9 spec 作为研究标的，非拷贝源。

## License

MIT —— 见 [LICENSE](./LICENSE)。
