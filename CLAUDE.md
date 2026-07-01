# CLAUDE.md

> **build-my-own-harness**：亲手重建一个 agent harness。What I cannot create, I do not understand. 对标 HKUDS/OpenHarness，solo，无协作。

## Cognition map（认知地图）

`REFERENCE.md` = 逆向 OpenHarness v0.1.9 的认知地图，**整份冻结**：§1-§4 认知（§2 目录树+数据流 / §3 九要素 / §4 跨要素模式），§5 有序 build 模块拆分（按依赖排）。它是防玩具化的底图——动手前对着它，动手时按 §5 的顺序选模块。

## The module loop（一个模块怎么走）

0. **参照系** → `reverse-spec`（已完成 → `REFERENCE.md`）。
1. **实现** → 见下「Solo coding loop」。

横切盲区：prompt / memory / 概率性行为这层，确定性 GREEN 覆盖不到——改到它就靠判断兜。（`eval` 是将来给这层的尺，现仍 draft，**不设成完成门**。）

## Solo coding loop（写码流）

- **TDD 驱动**：先写测试 → **亲眼见 RED** → 写代码到 GREEN。没见过红的绿是假绿。挂了改代码，绝不弱化断言 / 改测试凑绿。

## Project reference（项目速查）

干活要用、code 里看不出、又不能猜的项目事实——写在这，要用时来查：

| What | Value |
|---|---|
| 跑全量测试 | `uv run pytest -q` |
| 提交前质量门 | `uv run mypy --strict src/` + `uv run ruff check && uv run ruff format --check` |
| 对标 spec | `REFERENCE.md`|
| 行业对比拿谁比 | Claude Code（体感锚点）、OpenAI Codex、LangChain、Cursor、上游 OpenHarness |
