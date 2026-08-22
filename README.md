# OpenHarness

<p align="center">
  <a href="README.md"><strong>简体中文</strong></a> ·
  <a href="README.en.md"><strong>English</strong></a>
</p>

[![CI](https://github.com/maisieyang/open-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/maisieyang/open-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **一个用 Python 独立构建的 local-first Coding Agent Harness。**

OpenHarness 当前以 CLI 为交互入口，核心工程是 Agent Runtime：管理模型下一次看见什么、
任务如何持续推进、动作实际能够影响什么，以及模型参与的关键决策如何被重复验证。

这个项目主要回答三个问题：

1. 模型下一次应该看到什么？
2. Agent 如何在可控边界内持续工作？
3. 当模型参与决策，一个 Coding Agent 到底应该怎么验证？

**工程证据：** 2,791 个稳定测试、95.06% stable-core coverage；9 份 Eval contract、
6 / 6 replay gates；SWE-bench Lite 170 / 300。
[查看验证方法与证据边界 →](#工程证据基线)

## 快速开始

以下启动方式适用于 macOS。需要 Python 3.10+、[uv](https://docs.astral.sh/uv/)，以及
一个兼容 OpenAI API 的模型服务。

```bash
git clone https://github.com/maisieyang/open-harness.git
cd open-harness
cp .env.example .env  # 填写 API Key、API 地址和模型名
uv run oh              # 加 --auto 自动审批精确权限请求
```

启动后进入 OpenHarness REPL。根据任务所需的控制方式，可以直接工作、先规划，或交给
Goal 持续推进：

```text
Default  直接探索、修改和验证，完成一轮后把控制权交还给人
/plan    只读探索并形成可批准的计划；批准后返回 Default
/goal    根据目标和完成条件持续接续 turn；独立 Judge 判断继续、完成或暂停
```

## 我在解决的三个问题

### 1. Content Management：为下一次推理编译 Working Set

LLM 的一次 API 调用本身没有记忆。所谓 Context，是 Harness 在每次调用前重新提交给
模型的一份有限输入。

因此，Context 不是一段不断增长的聊天记录，而是 Harness 为下一次推理编译的
**Working Set**。它需要同时管理三类内容：

| 内容 | 回答的问题 | 典型来源 |
|---|---|---|
| 任务 | 现在要完成什么 | User、Goal、Project Instructions |
| 证据 | 已经知道和验证了什么 | Conversation、Tool Results、Memory、Snapshots |
| 能力 | 当前可以采取什么行动 | Tools、Skills、Plugins、Permissions、Plan |

OpenHarness 在一个 Session 内持续组装 System Prompt、Tool catalog 和 Conversation；
通过渐进式暴露加载 Skills、Plugins 与 Memory；限制单次 Tool Result 的增长；清理较早
结果，并在必要时对 older history 做语义压缩。跨 Session 后，Project Memory 保存可复用
知识，Snapshot 保存会话状态，Resume 再用当前环境和权限重新编译下一次 Working Set。

这里管理的不只是 Context Window 的容量，也是模型有限的注意力。更大的窗口可以容纳
更多信息，却不会自动判断哪些信息充分、可信、当前，并且适合此刻的行动。

阅读全文：[Content Management：如何为 Coding Agent 管理有限注意力](https://maisieyang.github.io/writing/content-management.html)

### 2. Goal、Permission 与 Sandbox：让 Agent 持续工作，让人可控离场

我实现 `/goal`，是为了让人不再逐轮接棒：用户一次定义目标和完成条件，系统持续推进，
独立 Judge 根据执行证据判断任务是否已经完成。

但 Goal 只解决了任务为什么继续、什么时候停止。它不会自动回答 Agent 可以做什么、
越界时谁来决定，以及动作最多能造成什么后果。

| 控制问题 | 机制 | 责任边界 |
|---|---|---|
| 任务是否继续、何时完成 | Goal Controller + independent Judge | 不授予新的能力 |
| 某次边界例外是否获得授权 | Permission | 不负责强制本地执行边界 |
| 本地动作实际上最多能影响什么 | Sandbox | 不判断动作是否符合人的意图 |

`/goal` 设置完成条件后立即开始工作。Worker 每次干净结束一轮后，禁用工具的独立
Judge 只检查 Goal 设立后产生的执行证据：条件满足则停止；证据不足则把缺口反馈给
下一轮；无法判断则保留状态并暂停。

Permission 与 Sandbox 守在动作边界。Permission Profile 表达基础授权意图；Sandbox
把其中的本地部分编译为可验证的执行边界；越界动作只能获得精确、一次性的例外授权。
如果新的授权必须由人决定，系统会 park 当前 continuation，而不是让 Goal 围绕同一个
能力缺口继续空转。

这三个机制共同改变的是人的位置：

```text
逐轮盯住 Agent
        ↓
预先定义目标、完成条件和基础边界
        ↓
任务完成，或真正抵达人的决策边界时再回来
```

阅读全文：[我实现了 `/goal`，但人还是不能离开](https://maisieyang.github.io/writing/goal-external-completion.html)

### 3. Eval：一个 Coding Agent，到底应该怎么验证？

真正做过 Eval 以后，我发现它并不是一套脱离软件工程的特殊技术。它只是把软件测试继续
向前推进了一步——推进到那些由模型参与、不能再用一次确定性断言覆盖的地方。

> **当模型参与决策，Eval 开始。**

当系统行为需要经过 LLM 输出才能决定时，测试对象就不再只是确定性机制，还包括模型
决策面。

OpenHarness 用四种证据回答不同的问题：

| 验证方式 | 回答的问题 | 使用节奏 |
|---|---|---|
| 机制测试（TDD） | 状态机、权限规则、工具执行、持久化和失败路径是否正确 | 日常开发 |
| 决策面 Eval | 由 LLM 输出决定的系统行为，是否满足该决策面的能力契约 | 日常开发 |
| Dogfood 与真实使用 | 完整产品是否解决真实任务、值得持续使用，还有哪些问题尚未被测试描述 | 持续使用 |
| 公共 Benchmark | 核心 coding loop 能否在公共任务和外部判定下完成端到端工作 | 阶段性运行 |

TDD、决策面 Eval、Dogfood 与真实使用构成持续开发闭环；公共 Benchmark 为核心
coding loop 补充一份有边界的外部坐标，其分数属于模型、Harness、工具、运行预算与
执行环境共同组成的系统。

**Eval 是把人的品味变成工程资产。**

阅读全文：[一个 Coding Agent，到底应该怎么验证？](https://maisieyang.github.io/writing/agent-eval-demystified.html)

## 工程证据基线

截至 2026-08-22：

- **软件机制**：2,791 个稳定测试通过，stable-core coverage 为 95.06%；
  mypy strict、Ruff 与 format check 全部通过，[CI](./.github/workflows/ci.yml)
  覆盖 Python 3.10 与 3.11。
- **Agent 决策**：9 份 capability eval contract；其中 6 项完成真实模型
  live ratification，当前 replay gates 为 6 / 6。
  详见 [Eval 手册](./evals/README.zh-CN.md)。
- **公共任务**：OpenHarness 0.4.0 与 qwen3.7-max 在
  [SWE-bench Lite](./benchmarks/swebench/TAXONOMY.md) 中解决
  170 / 300 个任务，resolved rate 为 56.7%。

Replay 只验证已录制行为的回归；Benchmark 反映模型、Harness、工具、预算与运行环境
组成的系统。这些证据不能替代 Dogfood 与真实用户反馈。

## 开发与验证

日常 CI 与贡献者门禁包括：

```bash
uv run pytest -m "not integration and not eval" -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
```

涉及模型决策的改动，还应在以上确定性门禁之外运行对应的 capability eval，详见
[Eval 手册](./evals/README.zh-CN.md)。

## Plugin 与延伸

[finance-skills](https://github.com/maisieyang/finance-skills) — Harness 基座上的垂直行业
能力包：复用 OpenHarness Runtime，通过 Skills 与 Plugins 加载金融知识和工作流。

更多文章见我的博客 [梅茜的世界｜Maisie’s World](https://maisieyang.github.io/writing/)。

## 致谢

名称与最初的模块词汇来自 [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness)
（MIT）。本仓库是独立、从零的实现。

## License

MIT — 见 [LICENSE](./LICENSE)。
