# Decision 01 — Project Scaffolding

- **Date**: 2026-04-27
- **Phase / Module**: Phase 1 / Module 1
- **Status**: Decided

## Context

第一个模块决定了 Python 工具链——后续每个模块都跑在这个地基上。错选会带来长期摩擦（poetry 慢、不开 strict 后期类型债务、pre-commit 不上 PR 反馈滞后）。

## Decisions

### D1.1 — 构建 / 包管理：`uv` + `hatchling`
- **Why**: Astral 系（Rust）已成 2025-2026 新项目事实选择；速度 10-100x；lockfile 现代；与 SPEC 一致
- **Trade-off**: 选 uv 而非 poetry——接受"工具相对新（< 1 年）"的风险，换"sync/lock/run 一体 + 极快"
- **打包后端**: hatchling（轻量、PEP 517 标准、无外部依赖）

### D1.2 — 类型检查：`mypy --strict`
- **Why**: 用户 TS 出身适应严格类型；Pydantic v2 + Protocol + AsyncIterator + discriminated union 模式没静态检查会写得很痛苦；strict 是大型 Python 项目的事实门槛
- **Trade-off**: 写代码慢 ~30%，但重构信心高 10x；**这是承诺**，开了之后所有新代码必须类型完整
- **特例**: `tests/*` 放宽 `disallow_untyped_decorators`（pytest fixtures 经常无类型）

### D1.3 — pre-commit：上 pre-commit，**只跑 ruff**
- **Why**: 单人项目本地反馈要快、commit 不能慢；mypy/pytest 留给 CI 把关
- **Trade-off**: commit 时不会发现类型错误（要等 CI），但保住了本地迭代速度

## Locked-in（业界几乎无悬念，无需深度对比）

| 项 | 选择 | 一句话理由 |
|---|------|-----------|
| Lint + Format | `ruff` | Astral 系，一统 flake8/black/isort/pyupgrade |
| 测试 | `pytest` + `pytest-asyncio` + `pytest-cov` | 事实标准；async 必须 |
| 目录布局 | `src` layout | Python 官方推荐，避免 dev import 路径污染 |
| CI | GitHub Actions | 开源项目事实标准 |
| Python 版本 | 3.10 (min) / 3.11 (target) | 与 SPEC 一致；3.12+ 生态尚在追赶 |

## Revisit Triggers（什么情况下重新评估）

- **uv → poetry**：uv 出现严重 bug 或生态包不兼容阻塞开发
- **mypy strict → 放宽**：开发节奏被类型推导卡死超过 1 周（先 grep 痛点再判断）
- **pre-commit 加 mypy**：项目协作人数 > 1，统一性比单人速度更重要

## What this decision excludes

- 不用 `setup.py` / `requirements.txt`（被 uv lockfile 取代）
- 不用 `pipenv` / `pdm`（场景已被 uv 覆盖）
- 不开 `pyright`（生产 lint 不如 mypy 主流；IDE 端可继续用 pylance）
- 不引入 `tox` / `nox`（uv + GHA matrix 已足够）
