# CLAUDE.md

> **build-my-own-harness**：亲手重建一个 agent harness（对标 HKUDS/OpenHarness），用"能创造"换"真懂"。solo，无协作。

## Cognition map（认知地图）

`REFERENCE.md` = 逆向 OpenHarness v0.1.9 的认知地图，**整份冻结**：§1-§4 认知（§2 目录树+数据流 / §3 九要素 / §4 跨要素模式），§5 有序 build 模块拆分（按依赖排）。它是防玩具化的底图——动手前对着它，动手时按 §5 的顺序选模块。

## The module loop（一个模块怎么走）

0. **参照系** → `reverse-spec`（已完成 → `REFERENCE.md`）。
1. **设计** → 上游 `interview-me`（想清楚模块角色 / 核心要素 / trade-off 立场）→ `/plan`（拆成 `tasks/<module>-plan.md`：该模块任务清单，**留档不删**——第一次学是发散的、执行中会叉出去探讨，固化的 plan 是"发散完能回来"的锚；模块是 capability 级、跨度长，光靠末尾 §回顾 不够）。capability 级，不下沉实现。
2. **实现** → 见下「Solo coding loop」。
3. **回顾** → `debrief`（draft）—— 挖隐式决策、对照 REFERENCE §3 自评、行业对比，沉淀一篇模块认知文档。

横切：改动触碰 prompt / memory / 概率性模型行为 → `eval`（draft）。确定性测试 GREEN 证明不了这层没劣化。

> 模块文档放哪、什么格式——**边做边和你一起长出来，有了沉淀再固化，现在不预设结构。**

## Solo coding loop（写码流）

你给信号，我自循环到绿——**循环是 Claude 的本能，不用教；要守的就一条让信号可信的纪律：**

- **TDD 是脊梁**：先写测试 → **亲眼见 RED** → 写代码到 GREEN。没见过红的绿是假绿。**测试是 spec：挂了改代码，绝不弱化断言 / 改测试凑绿。**（"快点过"压力下我最容易朝"变绿"飘，所以非守不可）

加一个 solo gate：**commit 前出示 diff、你点头才提交。**

## Project reference（项目速查）

干活要用、code 里看不出、又不能猜的项目事实——写在这，要用时来查：

| What | Value |
|---|---|
| 跑全量测试 | `uv run pytest -q` |
| 提交前质量门 | `uv run mypy --strict src/` + `uv run ruff check && uv run ruff format --check` |
| 对标 spec | `REFERENCE.md`（OpenHarness v0.1.9 逆向，冻结） |
| eval 决策面 map | `decisions/35-eval-coverage-map.md` |
| eval substrate | `openharness.eval` 子模块（`.runner` / `.cassette` / `.protocol`(Scorer) / `.results`，分别 import）；consumer `evals/<surface>/` |
| 行业对比拿谁比 | Claude Code（体感锚点）、OpenAI Codex、LangChain、Cursor、上游 OpenHarness |
