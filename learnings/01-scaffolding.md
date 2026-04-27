# Module 1: 项目脚手架 — 复盘

> Phase 1 / 完成日期：2026-04-27 / 用时 ~1 天（含网络环境坑）

## 这个模块解决了什么 harness 问题

不直接解决 harness 领域问题——但建立了 **"生产级 Python 项目"的地基**。后续每个 harness 模块都依赖这块地基达到生产质量：

- **mypy strict** 让重构有信心
- **ruff** 保证代码风格不需要任何代码评审里争论
- **GitHub Actions CI** 防止回归（每个 PR 自动跑 lint + type-check + test）
- **uv lockfile** 让依赖确定性可复现（"在我机器上能跑"问题被消灭）

## 产品决策回顾

| 决策 | 选了什么 | 替代方案 | 什么时候改选替代方案 |
|------|---------|---------|------------------|
| D1.1 | `uv` + `hatchling` | `poetry` | 团队 > 5 人 + 需要极致生态稳定性时 |
| D1.2 | `mypy --strict` | mypy lenient / 不开类型 | 写快速 prototype（< 1 周生命周期）时 |
| D1.3 | pre-commit 只跑 ruff | + mypy/pytest | 多人协作，统一性 > 单人速度时 |

详见 [decisions/01-scaffolding.md](../decisions/01-scaffolding.md)。

## Python 模式（TS 出身的 reference 笔记）

### 1. `from __future__ import annotations`

全文件第一行加上。两个作用：
- 所有 type annotation 变成"延迟求值的字符串" → 性能更好、循环引用不再是问题
- 启用 PEP 604（`X | Y`）在 Python 3.10（3.10 默认还需要 `Optional[X]`）

```python
from __future__ import annotations

def foo(x: int | None = None) -> str:  # 没有 __future__ 这行在 3.10 会报错
    ...
```

### 2. src layout

包必须在 `src/` 下，**不在仓库根目录**。这逼你 `uv sync` 做 editable install 才能 import，避免 dev 环境的"我从仓库根目录直接 import 也能用"的路径污染——后期打包发布会暴露各种隐藏依赖问题。

### 3. PEP 735 `[dependency-groups]`

比老式 `[project.optional-dependencies]` 更新更标准；uv 原生支持。**等价于 TS 的 `devDependencies`**。

### 4. 集中配置 `[tool.X]`

所有工具配置都进 `pyproject.toml`：`[tool.ruff]` / `[tool.mypy]` / `[tool.pytest.ini_options]` / `[tool.coverage.*]`。一个文件管所有——跟 TS 项目把配置散在 `.eslintrc.js` / `tsconfig.json` / `jest.config.js` 反而更分散是个有趣对比。

### 5. `raise SystemExit(main())`

Python 入口点惯用法。`main()` 返回 int 退出码。优于 `sys.exit(main())`——更显式、避免 import sys。

### 6. CI 生产级细节（容易忽略但有大影响）

- `concurrency: cancel-in-progress` — 同分支新 push 自动取消旧 run，省 runner 分钟
- `uv sync --frozen` — lockfile 是 source of truth；漂移即失败（防"本地能跑、CI 跑不了"）
- `--strict-markers` / `--strict-config` (pytest) — 配置漂移早发现
- 矩阵 `python-version: [3.10, 3.11]` — 验证多版本兼容（不是只测最高版本）

## 网络环境（独立教训）

国内通过代理访问 GitHub 是日常基础设施。**git config 的 `http.proxy` 必须和当前代理 app 端口一致**。常见状况：

- terminal env vars (`HTTPS_PROXY` 等) 指向当前代理（如 6152）
- `git config http.proxy` 指向旧代理（如 7890）— **git 优先用 git config，不会读 env vars**
- 结果：所有 terminal 工具能上 GitHub，**唯独 git 不能**

预防：配置代理工具时同步：
```bash
git config --global http.proxy http://127.0.0.1:<port>
git config --global https.proxy http://127.0.0.1:<port>
```

`pre-commit` 第一次跑也会通过 git fetch hook 仓库，**走的是同一个 git 配置**，所以这条修了 pre-commit 也跟着通。

## 如果重做我会改什么

1. **更早 `git init` + 第一次 push**——T1 完成时就该推一次看 CI 跑通；而不是写完整个 Module 1 才推。增量更安全（坏的 CI 配置可以早发现）。
2. **`pre-commit autoupdate` 在 install 后立刻跑**——把 hook 版本固定到当前最新，避免 `rev` 字段过期到无法 fetch。
3. **代理端口在 `decisions/00-env.md` 里记录**——这种环境性陷阱应该作为 sanity check 的一部分（首次环境搭建脚本就该 verify git proxy）。
4. **更早写 `conftest.py` 的 fixture**——目前是空的。Module 2 起会很快需要测试 fixture（构造 `ContentBlock`、Mock LLM 响应等），下次模块入口就该顺手补。

## 数据点

- pyproject.toml 行数：~95 行（这是产品级单文件配置应有的体量）
- CI 首次成功跑时间：~3-5 min（matrix × 2 Python 版本）
- 测试覆盖率：100%（仅 1 个文件 `__init__.py`，是 trivial 状态——Module 2 之后会有真实数字）
