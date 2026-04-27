# Phase 1: Foundation + Hello LLM

> Phase 0 已完成（见 [ARCHITECTURE.md](../ARCHITECTURE.md)）。本文档是 Phase 1 的具体规划。

## Phase 1 目标

**交付物**：`oh ask "hi"` → 流式调用 Anthropic API → 终端实时打印响应；项目具备生产级 Python 工程基线（CI、类型检查、测试、打包）。

## 验收标准（Definition of Done）

- [ ] `uv sync` 可一键搭建开发环境
- [ ] `ruff check` + `mypy --strict` + `pytest` 全部通过
- [ ] `oh ask "hi"`（或等价命令）能调通 Anthropic API 并流式打印响应
- [ ] CI 在 PR 上自动跑 lint + type-check + test
- [ ] 单元测试覆盖率 ≥ 70%（流式部分用 mock）
- [ ] README.md 给出"如何安装 + 如何使用"两段
- [ ] `learnings/phase-1.md` 记录本 Phase 学到的 Python 模式 + 产品决策

## Phase 1 模块顺序

| # | 模块 | 工时估算 | 关键学习点 |
|---|------|---------|-----------|
| 1 | 项目脚手架 | 2-3 天 | uv / ruff / mypy strict / src layout / CI |
| 2 | 协议与数据模型 | 2-3 天 | Pydantic v2 / discriminated union / 类型化设计 |
| 3 | API Provider 抽象 + Anthropic 客户端 + 流式事件 | 4-5 天 | Protocol / async / AsyncIterator / 重试 / SSE 解析 |
| 4 | CLI 入口 + Print 模式 | 2-3 天 | Typer / Rich / async 集成 |

> 流式事件层级是 Phase 1 跨模块的核心设计——会在 Module 3 详细讨论。

## 模块进入节奏

每个模块进入前：
1. 在 chat 里跑完 Three-Axis 讨论（**领域问题** / **产品决策 trade-off 矩阵** / **工程实现**）
2. 你做产品决策；我把决策记录到 `decisions/<NN>-<module>.md`
3. 把模块拆成 ≤ 0.5 天的任务，追加到 `tasks/phase-1-todo.md`
4. 开始动手实现

模块完成后：
- 更新 todo 勾选状态
- 在 `learnings/<module>.md` 沉淀（Python 模式 / 产品决策回顾 / 重做时会改什么）

## 当前位置

- [x] Phase 0 完成
- [ ] **Phase 1 启动中——正在做 Module 1 (项目脚手架) 的 Three-Axis 讨论**
- [ ] Module 2
- [ ] Module 3
- [ ] Module 4
- [ ] Phase 1 验收
