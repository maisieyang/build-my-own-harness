# Phase 1 Todo

> 本文档随 Phase 1 推进**增量更新**——每进入一个模块，先做 Three-Axis 讨论，决策完成后追加该模块的任务。每个任务 ≤ 0.5 天。

## Module 1: 项目脚手架

**状态**：✅ 完成（CI 绿 / 复盘见 [learnings/01-scaffolding.md](../learnings/01-scaffolding.md) / 决策见 [decisions/01-scaffolding.md](../decisions/01-scaffolding.md)）

| # | 任务 | 验收 | 状态 |
|---|------|------|------|
| T1 | 项目骨架：`.gitignore` / `LICENSE` / `README.md` / 目录结构 | 文件就位 | ✅ |
| T2 | `pyproject.toml`：`[project]` + `[build-system]` + `[dependency-groups]` + ruff/mypy/pytest 配置 | 文件结构合规 | ✅ |
| T3 | 占位代码：`__init__.py` + `cli.py` + `__main__.py` | `python -m openharness` 不报错 | ✅ |
| T4 | `uv sync` 跑通 | 命令成功 | ✅ |
| T5 | smoke 测试：`conftest.py` + `test_smoke.py` | `uv run pytest` 全绿 | ✅ |
| T6 | `uv run ruff check` 和 `ruff format --check` 通过 | 0 lint error | ✅ |
| T7 | `uv run mypy --strict src/` 通过 | 0 type error | ✅ |
| T8 | `.pre-commit-config.yaml`（pre-commit-hooks + ruff） | hook 安装并触发 | ✅ 落盘待安装 |
| T9 | `.github/workflows/ci.yml`：matrix Python 3.10/3.11 | push 后 Actions 绿 | ✅ 落盘待 push |
| T10 | README 扩展：徽章、Quick start、Dev workflow、结构说明、决策摘要 | 第一次访问者能跑起来 | ✅ |
| T11 | 验收：本地全套 + git init + 第一次 push CI 绿 | 全部通过 | 🟡 用户操作 |

**当前进度**：T1-T10 全部落盘；T11 等待用户做 `git init` + `pre-commit install` + 第一次 push 验证 CI。

## Module 2: 协议与数据模型

**状态**：🟡 进行中（决策已确定，见 [decisions/02-protocols.md](../decisions/02-protocols.md)）

| # | 任务 | 验收 | 状态 |
|---|------|------|------|
| T1 | 加 `pydantic>=2.5` 依赖 + 配 `[tool.mypy] plugins = ["pydantic.mypy"]` + `[tool.pydantic-mypy]` 严格选项 | `uv sync` 通过 | 🟡 |
| T2 | 建 `src/openharness/protocols/` 目录 + 占位 `__init__.py` | `import openharness.protocols` 不报错 | 🟡 |
| T3 | `content.py`：`TextBlock` / `ImageBlock` / `ToolUseBlock` / `ToolResultBlock` + 顶层 `ContentBlock` discriminated union | mypy strict 过 | ⏸ |
| T4 | `messages.py`：`ConversationMessage`（role + content list） | mypy strict 过 | ⏸ |
| T5 | `usage.py`：`UsageSnapshot`（input/output tokens + total） | mypy strict 过 | ⏸ |
| T6 | `requests.py`：`ApiMessageRequest`（model / max_tokens / system / tools / messages） | mypy strict 过 | ⏸ |
| T7 | `stream_events.py`：`ApiStreamEvent` 层级（`TextDelta` / `MessageComplete` / `Retry`） | mypy strict 过 | ⏸ |
| T8 | `__init__.py` re-export 顶层 API | 短 import 路径可用 | ⏸ |
| T9 | 测试：roundtrip JSON / discriminator dispatch / Anthropic 真实 JSON fixture / 错误情况 | `uv run pytest` 全绿，覆盖率 ≥ 90% | ⏸ |
| T10 | 本地 + CI 全绿 | GitHub Actions 绿 | ⏸ |

**总估时**：~5h（2-3 天含学习节奏）

## Module 3: API Provider 抽象 + Anthropic 客户端 + 流式事件

**状态**：⏸ 暂未进入

## Module 4: CLI 入口 + Print 模式

**状态**：⏸ 暂未进入

---

## Phase 1 验收 Checklist

- [ ] `uv sync` 可一键搭建开发环境
- [ ] `ruff check` 通过
- [ ] `mypy --strict src/` 通过
- [ ] `pytest` 通过，覆盖率 ≥ 70%
- [ ] `oh ask "hi"` 流式打印 Anthropic 响应
- [ ] GitHub Actions CI 绿
- [ ] README.md 安装 + 使用两段
- [ ] `learnings/phase-1.md` 写完
