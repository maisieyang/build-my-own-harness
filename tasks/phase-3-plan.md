# Phase 3 Implementation Plan — Safety + Production Hardening

> Phase 1 / Phase 2 archive: [`tasks/plan.md`](./plan.md) / [`tasks/phase-2-plan.md`](./phase-2-plan.md).
> 此文件是 Phase 3 active plan。
>
> Boundary contract: [`decisions/08-phase-3-boundary.md`](../decisions/08-phase-3-boundary.md)。
> Framing basis: [`learnings/phase-3-framing.md`](../learnings/phase-3-framing.md)。
> Top-level multi-phase strategy: [`ARCHITECTURE.md`](../ARCHITECTURE.md)。

## Overview

**Phase 3 goal**: 把 LLM 调用从「能跑」(Phase 2)升级到「可放心给别人用」
(production)——按 RPC 30 年学到的判断 framework,把横切逻辑(middleware/hook)、
安全策略(AuthZ)、观察手段(observability)、错误分类(error taxonomy)4 件配套
一起装齐。

**Total scope**: ~2-3 weeks of focused work, 6 capabilities, ~30-40 commits expected。

## Architecture Decisions(locked before build)

| Doc | What it locks |
|---|---|
| [`decisions/08-phase-3-boundary.md`](../decisions/08-phase-3-boundary.md) | Phase 3 In/Out scope; D13.1 middleware (hook) 链 5 events + return-value semantics; D13.2 AuthZ 三层 Tier; D13.3 `is_read_only` 加 BaseTool + parallel 推 Phase 6; D13.4 Error Taxonomy + root rename; D13.5 Retry hardening defer Phase 4; D13.6 Observability 选 structlog + 留位 EventLogger / OTel; **Naming Note: middleware ≡ hook** |
| New decisions land here | 每个 capability 入口的 Three-Axis 讨论可能产出新 sub-decision → `decisions/09+.md` |

## Task Sizing Principle

同 Phase 2:**task = capability slice**,可独立验证,~1-3 天聚焦工作,1-5 文件。
Three-Axis 讨论(领域 / 决策 / 工程 / mini-plan)在每个 capability 入口做,
**不预先在此文件展开**——本 plan 保持精简,只列 acceptance + files + sub-unit
sketch。

每个 capability 在入口前的 Three-Axis 深度按 boundary 锁定:

- **P3-T4 Middleware**:深度讨论(1-2 小时)—— 链 resolve / 异常路径 / 流阻塞
- **P3-T3 AuthZ**:中等(45 分钟)—— Tier 1/2/3 具体 deny pattern
- 其他 4 个:轻度(15-30 分钟)—— 工程为主,产品决策已锁

## Task List

### P3-T1: Pre-flight cleanup batch 🔜 NEXT

**Description**: Phase 3 的第一站不是新功能,是把 Phase 1 carryovers + Phase 2
flagged items 一次性清掉。理由:Phase 3 后续 5 个 capability 都会动 hot path
(error / hooks / permissions / observability),先把这些前置 polish 清完,后续
diff 才能聚焦在新概念上。

**Acceptance criteria**:

- [ ] `is_read_only: bool = False` 加到 `BaseTool` 类属性(D13.3);Read/Grep
  设 `True`,Write/Edit/Bash 设 `False`
- [ ] Bash 工具空输出返回 `(no output)` 哨兵字符串(对齐 OpenHarness REFERENCE
  A.3,P2-T3 时漏)
- [ ] Edit 工具改用 `tempfile.NamedTemporaryFile + os.rename` 原子写;mid-write
  崩溃不截断原文件
- [ ] 显式定义 `class SupportsStreamingMessages(Protocol)` 替换内部 duck-typed
  接口(learnings/03 #3)
- [ ] `_FAST_POLICY` 抽到 `tests/api/conftest.py`(learnings/03 #4)
- [ ] `_translate_openai_error` 单独 test file(learnings/03 #6)
- [ ] CI 显式加 `-m "not integration"` 避免 skipped 计入 fail 信号
- [ ] `decisions/00-env.md` 记录代理端口陷阱(learnings/01 #3)
- [ ] Pin `.pre-commit-config.yaml` ruff hook 版本,消除 format 飘移
- [ ] Settings File 层(XDG `~/.config/openharness/config.toml`)decision:砍掉
  或正式记入 Phase 5 multi-profile 伏笔(写决策入 `decisions/`)
- [ ] 全部 P2 测试仍绿;coverage 不降

**Verification**:
```bash
uv run pytest                                      # all green
uv run mypy --strict src/ tests/
uv run pre-commit run --all-files                  # 验 ruff hook drift 消失
uv run oh ask "echo test | head -c 0"              # 验 Bash (no output) 哨兵
```

**Files**: `src/openharness/tools/{base,read,grep,write,edit,bash}.py` +
`src/openharness/api/__init__.py` +
`.pre-commit-config.yaml` +
`tests/api/conftest.py` + `tests/api/test_translation_errors.py` (new) +
`decisions/00-env.md` (new) +
`.github/workflows/ci.yml`

**Sub-units sketch**:
- 1a — `BaseTool.is_read_only` 类属性 + 5 工具取值 + tests
- 1b — Bash `(no output)` 哨兵 + tests
- 1c — Edit 原子写(tempfile + os.rename)+ tests(含 mid-write crash 模拟)
- 1d — `SupportsStreamingMessages` 显式 Protocol + 替换内部 duck-typed + tests
- 1e — Test cleanup batch(`_FAST_POLICY` to conftest + translation errors split)
- 1f — Misc(decisions/00-env / CI flag / pre-commit pin / Settings File 决策)

---

### P3-T2: Error Taxonomy 重命名 + 扩展

**Description**: 实现 D13.4 —— RPC 配套 5(error taxonomy)。把 root 从
`OpenHarnessApiError` 重命名到 `OpenHarnessError`,API 错变 subclass。新增
4 个 subclass branches:`ToolError` / `PermissionError` / `HookError` /
`LoopError`。`LoopLimitExceeded` 从兜底分支迁到 `LoopError`。

**Acceptance criteria**:

- [ ] `OpenHarnessError` 是新 root class
- [ ] `OpenHarnessApiError` 变 `OpenHarnessError` 的 subclass(原 3 个 API 错
  类不动)
- [ ] 新增 `ToolError`(P3-T3 用)/`PermissionError`(P3-T3 用)/`HookError`
  (P3-T4 用)/`LoopError`(now)四个 subclass
- [ ] `LoopLimitExceeded` 改继承 `LoopError`
- [ ] `cli.py` 加 `LoopError` 专属 except 分支(原本走 `OpenHarnessApiError`
  兜底);hint 文案不变(已经名 named `--max-turns`)
- [ ] `from openharness.api import OpenHarnessApiError`(向后兼容路径)仍工作
- [ ] 全部 Phase 2 测试仍绿;mypy strict 干净
- [ ] 覆盖率 on `errors/` ≥ 90%

**Verification**:
```bash
uv run pytest tests/ --cov=openharness.api.errors --cov-fail-under=90
uv run mypy --strict src/ tests/
uv run oh ask --max-tokens 10 "long prompt forcing many turns"  # 触发 LoopError
```

**Files**: `src/openharness/api/errors.py`(扩展) + 可能拆出
`src/openharness/errors/__init__.py`(new top-level errors module) +
`src/openharness/cli.py`(加分支) +
`src/openharness/engine/errors.py`(LoopLimitExceeded 父类换) +
`tests/api/test_errors.py` + 各模块测试

**Sub-units sketch**:
- 2a — `OpenHarnessError` root + `OpenHarnessApiError` 改继承 + tests
- 2b — `ToolError` + `PermissionError` + `HookError` 三个空类(供后续填) + tests
- 2c — `LoopError` + `LoopLimitExceeded` 改继承 + tests
- 2d — `cli.py` 加 `LoopError` 专属 except + hint 文案 + tests
- 2e — Migration:全仓库 grep `OpenHarnessApiError` 看哪些应该改 `OpenHarnessError`

---

### P3-T3: AuthZ 三层 Tier(`PermissionChecker` 升级)

**Description**: 实现 D13.2 —— RPC 配套 3(AuthZ subsystem)。把 P2-T6 的
`DenyListChecker` 占位升级到三层 Tier 完整实现。`PermissionChecker.evaluate(...)`
接口签名不变(P2-T6 故意小的接口,这里只换 implementation)。

**三层 Tier**(D13.2 锁定):

- **Tier 1**: Hardcoded sensitive paths(读/写/执行 都 deny)
  —— 例:`~/.ssh/`, `/etc/passwd`, `~/.aws/`, `~/.gnupg/`
- **Tier 2**: Glob-based deny rules in Settings(用户配置)
  —— `OPENHARNESS_DENY_PATHS="*.env,secrets/**"`
- **Tier 3**: Mode-based(用 P3-T1 加的 `is_read_only`)
  —— Read/Grep 走 lax 默认 ALLOW;Write/Edit/Bash 走 strict 默认走 Tier 1+2
  扫描

**Acceptance criteria**:

- [ ] Tier 1 hardcoded patterns 拒绝 sensitive path(`oh ask "Read ~/.ssh/id_rsa"`
  → permission denied)
- [ ] Tier 2 用户配置 glob deny rules 工作;Settings 增 `deny_paths: list[str]`
  字段(env `OPENHARNESS_DENY_PATHS` 逗号分隔)
- [ ] Tier 3 mode-based:Read 工具读 cwd 内任意 path 默认 ALLOW;Write 默认走
  严格扫描
- [ ] 三层综合:LLM 一次发多 tool_use 时,各自独立评估
- [ ] 拒绝时 LLM 看到 `permission denied: <reason>` 包含 deny 来源(Tier 1 还是
  Tier 2)便于调试
- [ ] `PermissionError` 在不可恢复的 catastrophic case 抛出(目前 Tier 1/2/3
  内部不抛,但接口预留)
- [ ] 旧 `DenyListChecker` 的 hardcoded 模式迁入 Tier 1
- [ ] 覆盖率 on `permissions/` ≥ 90%

**Verification**:
```bash
uv run pytest tests/permissions/ --cov=openharness.permissions --cov-fail-under=90
uv run oh ask "show me ~/.ssh/id_rsa"      # 期望 permission denied
OPENHARNESS_DENY_PATHS="secrets/**" uv run oh ask "read secrets/keys.txt"
                                            # 期望 permission denied
uv run oh ask "list /tmp"                   # 期望正常工作(Read on tmp ALLOW)
```

**Files**: `src/openharness/permissions/{__init__,checker,tiers}.py` +
`src/openharness/config/settings.py`(加 `deny_paths` 字段) +
`tests/permissions/test_*.py`

**Sub-units sketch**:
- 3a — Tier 1 hardcoded sensitive paths 常量 + 匹配逻辑 + tests
- 3b — `Settings.deny_paths` + glob 匹配 helper + tests
- 3c — Tier 2 glob 引擎 + tests
- 3d — Tier 3 mode-based(用 `is_read_only`)分流 + tests
- 3e — `PermissionChecker` 主类 compose 三层 + tests
- 3f — `DenyListChecker` 迁移(Tier 1 吸收)+ 端到端集成测试

---

### P3-T4: Middleware (hook) 链 ⭐ Phase 3 主菜

**Description**: 实现 D13.1 —— RPC 配套 2(middleware / interceptor 链)。
新建 `hooks/` module。**用 hook 命名(行业对齐),docstring + 文档用 middleware
解释概念(Naming Note 锁定)**。

**5 lifecycle events**:`PreToolUse` / `PostToolUse` / `PreApiCall` / `PostApiCall`
/ `OnError`。
**单一 callable + return-value semantics**:hook return None → pass through;
return `HookResult(decision="deny" | "modify" | "allow", ...)` → executor 按
decision 处理。

**Acceptance criteria**:

- [ ] `Hook = Callable[[HookContext], Awaitable[HookResult | None]]` 类型定义
- [ ] `HookEvent` 5 个 Literal 值
- [ ] `HookResult` frozen dataclass:`decision`(Literal)+ `message: str | None`
  + `new_input: dict | None`
- [ ] `HookContext` 一个 union 或 5 个 frozen dataclass(待 Three-Axis 讨论)
  携带 event-specific payload(tool_name / args / exec_context / 等)
- [ ] `HookExecutor.invoke(event, context) -> HookResult | None` 串联多个 hook,
  first-deny-wins
- [ ] Hook 抛 Python 异常 → 触发 `OnError` 链,然后 raise `HookError`(不静默吞)
- [ ] `_dispatch_one` 集成 PreToolUse + PostToolUse;在 permission_checker 之后、
  tool.execute 之前
- [ ] `run_query` 集成 PreApiCall + PostApiCall;在 stream_message 前后
- [ ] OnError 集成在 cli.py except 链的最外圈
- [ ] User-facing API:`from openharness.hooks import Hook, HookEvent, HookResult,
  HookContext, register_hook`
- [ ] Smoke test:写一个 log hook + 一个 cost track hook,挂上去跑 `oh ask`
  能看到日志输出 + cost 累加
- [ ] 覆盖率 on `hooks/` ≥ 90%

**Verification**:
```bash
uv run pytest tests/hooks/ --cov=openharness.hooks --cov-fail-under=90
uv run pytest -m integration tests/hooks/test_smoke.py    # 真 hook 挂载
```

**Files**: `src/openharness/hooks/{__init__,events,context,result,executor,
registry}.py` +
`src/openharness/engine/query.py`(集成 PreApiCall/PostApiCall) +
`src/openharness/engine/dispatch.py`(or wherever _dispatch_one lives,集成
PreToolUse/PostToolUse) +
`src/openharness/cli.py`(集成 OnError) +
`tests/hooks/test_*.py` + `tests/hooks/test_smoke.py`(端到端)

**Sub-units sketch**(需 P3-T4 入口的 Three-Axis 细化):
- 4a — `HookEvent` enum + `HookResult` dataclass + `Hook` type + tests
- 4b — `HookContext` per-event(典型选择是 5 个 frozen dataclass)+ tests
- 4c — `HookExecutor` 链调用 + first-deny-wins 语义 + 异常→OnError + tests
- 4d — Registry(`register_hook(event, hook)` + 多 hook 顺序)+ tests
- 4e — 集成 PreToolUse + PostToolUse 进 _dispatch_one + tests
- 4f — 集成 PreApiCall + PostApiCall 进 run_query + tests
- 4g — 集成 OnError 进 cli.py + tests
- 4h — 端到端 smoke:log + cost 两个 hook 真挂 + 跑 oh ask 验证

---

### P3-T5: Observability(structlog 接入)

**Description**: 实现 D13.6 —— RPC 配套 4(observability)。structlog 接入 +
dispatch 关键点结构化日志。**LLM context 不进 log**(隐私 + 量大);tool input
中敏感字段 sanitize。

**Acceptance criteria**:

- [ ] `structlog` 加进 dependencies + 基础配置(JSON / console renderer 选择
  via env var)
- [ ] dispatch loop 关键点都有 structured log:
  - `turn_start` (turn=N, model=...)
  - `tool_dispatch` (tool=name, input=<sanitized>, run_id=...)
  - `tool_complete` (tool=name, is_error=..., duration_ms=...)
  - `permission_denied` (tool=name, args=<sanitized>, tier=...)
  - `retry` (attempt=N, error=...)
  - `hook_invoke` (hook=name, event=event_name)
  - `hook_failed` (hook=name, error=...)
  - `loop_limit_exceeded` (max_turns=N)
- [ ] CLI flag `--log-level [DEBUG|INFO|WARNING|ERROR]`,默认 WARNING(不污染
  正常 stdout/stderr)
- [ ] CLI flag `--log-format [console|json]`,默认 console
- [ ] LLM messages list 内容**不进** log;只 log `len(messages)`
- [ ] 工具 input 中 path / command 字段在 log 中 sanitize:只 log shape,
  不 log 内容(如 `args={"path": "<str>", "offset": 1}`)
- [ ] structlog 接 stdlib `logging` 让用户能加自己的 handler / sink
- [ ] 覆盖率 on `observability/` ≥ 90%

**Verification**:
```bash
uv run pytest tests/observability/ --cov=openharness.observability --cov-fail-under=90
uv run oh ask --log-level=DEBUG "list /tmp" 2>&1 | head -20    # 看 log 输出
```

**Files**: `src/openharness/observability/{__init__,logging,sanitize}.py` +
`src/openharness/engine/query.py`(加 log calls) +
`src/openharness/api/retry.py`(加 retry log) +
`src/openharness/permissions/checker.py`(加 deny log) +
`src/openharness/hooks/executor.py`(加 hook log) +
`src/openharness/cli.py`(加 --log-level / --log-format flags) +
`tests/observability/test_*.py`

**Sub-units sketch**:
- 5a — `structlog` 加依赖 + 基础 config(console/json renderer)+ tests
- 5b — `sanitize` helper(path / command / api_key 字段)+ tests
- 5c — log calls 入 `engine/query.py`(turn / tool dispatch / tool complete)
- 5d — log calls 入 retry / permissions / hooks
- 5e — CLI `--log-level` / `--log-format` flags + tests
- 5f — 端到端验证:`oh ask --log-level=INFO ...` 看 log shape 正确

---

### P3-T6: 测试加固 + Phase 3 retro

**Description**: Coverage 目标从 70% 抬到 95%+;CI gate 同步;Phase 3
retrospective 写完。

**Acceptance criteria**:

- [ ] Coverage gap audit:列出 < 90% 的模块 + 补测试
- [ ] 总覆盖率 ≥ 95%(从 P2 的 94.76% 抬)
- [ ] CI `--cov-fail-under` 抬到 90%(per-module)+ 总 95%
- [ ] `learnings/phase-3.md` 写完(对齐 phase-1-and-2.md 的 frame:5 RPC 配套
  视角 + 跨 module 涌现的认知)
- [ ] `README.md` 加 Phase 3 features:hook 用法示例 + permission 配置示例 +
  log flag 介绍
- [ ] Smoke integration test:`oh ask "rm ~/.ssh/id_rsa"` 触发 Tier 1 deny
- [ ] Smoke integration test:挂 cost-track hook + log hook 跑 `oh ask`
- [ ] `tasks/phase-3-todo.md` 全部勾上
- [ ] Phase 3 DoD checklist all green

**Verification**:
```bash
uv run pytest --cov=openharness --cov-fail-under=95
uv run pytest -m integration       # 端到端真跑
```

**Files**: `learnings/phase-3.md`(new) + `README.md`(扩展) +
`tasks/phase-3-todo.md`(收尾) + 各模块补测试 + `pyproject.toml`(coverage gate)

**Sub-units sketch**:
- 6a — Coverage gap audit 报告 + 列 missing tests
- 6b — 补测试 batch(估 5-10 个测试)
- 6c — CI gate 抬到 95%
- 6d — `learnings/phase-3.md` 写
- 6e — README 扩展 + Phase 3 features 文档
- 6f — Phase 3 DoD checklist 收尾

---

## Checkpoints

### After P3-T1
- [ ] 所有 P1 carryover + P2 flagged item 清掉
- [ ] **Human review**:`is_read_only` 5 工具取值的实测心智体感(LLM 调用模式
  会不会被影响?)

### After P3-T2
- [ ] Error taxonomy 5 类 subclass 就位,downstream 任务有专属 exception 可用
- [ ] **Human review**:rename 后的 import 链没有遗漏(grep `OpenHarnessApiError`
  on imports)

### After P3-T3
- [ ] Tier 1/2/3 在真 prompt 上验证过(读 ~/.ssh / write README / etc.)
- [ ] **Human review**:Tier 之间的优先级和综合行为符合直觉?有没有 false-
  positive(用户合法操作被拒)?

### After P3-T4 (Phase 3 主菜)
- [ ] Middleware (hook) 端到端工作:能挂、能链、能 deny、能 modify、能 OnError
- [ ] **Human review**:5 个 events 名字符合直觉?HookContext payload 含够信息?
  是不是值得在这一刻加 SessionStart/End?

### After P3-T5
- [ ] structlog 输出 shape 正确;无 PII / LLM context 泄漏
- [ ] **Human review**:log 默认 level / format 在真使用下感觉对吗?日志量
  是否过度?

### After P3-T6 (Phase 3 complete)
- [ ] 整体覆盖率 ≥ 95%;CI 全绿
- [ ] **Decision point**:进 Phase 4(Compaction)还是先做一个 Phase 3 学习
  深化的 polish round

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Hook 链 first-deny-wins 语义和用户直觉不符 | Med | P3-T4 入口 Three-Axis 显式讨论;module docstring + tests 锁住语义 |
| structlog 日志量在 default level 下污染终端 | Low | 默认 WARNING,只输出 retry / deny / hook_failed |
| Error rename 触发大量测试改名 | Med | 单 batch commit;mypy strict 抓所有触点;`OpenHarnessApiError` import 路径保留向后兼容 |
| AuthZ Tier 1 hardcoded paths false-positive | Med | Path patterns 在 review checkpoint 上跑真 prompt 验证;Settings glob 让用户能 override |
| Hook 阻塞 dispatch loop(用户写慢 hook 或同步 IO) | Med | 强制 async signature(Hook 类型签名锁);docstring 警告;Phase 3 不加 timeout(留 Phase 4) |
| `is_read_only` Bash 默认 False 影响 Tier 3 中 read 类 Bash 命令 | Low | Bash 走 strict path 是保守选择;若 LLM 抱怨 `cat foo` 被拒,引导用 Read |

## Open Questions

- **HookContext shape**:5 个 event 各一个 frozen dataclass(typed payload)
 **Tentative**:5 个独立 dataclass
  (`PreToolUseContext` / `PostToolUseContext` / etc.),让 hook 函数签名能精确
  约束;
- **Permission checker vs PreToolUse hook 的执行顺序**:permission 先
**Tentative**:permission 先(framework baseline);hook 在 permission
  通过后才跑(用户扩展层)。
- **Multiple hooks per event 的注册顺序 vs 决策优先级**:目前 first-deny-wins
  ——但如果 hook 1 modify、hook 2 deny,decision 是什么?
  **Tentative**:modify 先生效改 input,后续 hook 看到 modified input,再决策。
- **structlog handler 接 JSONL persistence**:Phase 3 P3-T5 就做
**Tentative**:Phase 3 加 `--log-format=json` flag(structlog
  原生支持);

## Pre-flight Cleanup(已并入 P3-T1)

Phase 1 / Phase 2 carryover 全部进 P3-T1 acceptance criteria,不再独立列。
见 `learnings/phase-1-and-2.md` §10.2 包袱清单。

## Pointers

- Boundary contract: [`decisions/08-phase-3-boundary.md`](../decisions/08-phase-3-boundary.md)
- Framing basis: [`learnings/phase-3-framing.md`](../learnings/phase-3-framing.md)
- Phase 1+2 retrospective: [`learnings/phase-1-and-2.md`](../learnings/phase-1-and-2.md)
- Phase 1 archive: [`tasks/plan.md`](./plan.md), [`tasks/todo.md`](./todo.md)
- Phase 2 archive: [`tasks/phase-2-plan.md`](./phase-2-plan.md), [`tasks/phase-2-todo.md`](./phase-2-todo.md)
- OpenHarness reference (analogous modules): REFERENCE.md §8(permissions),
  §9(hooks),§4.3(error hierarchy),§37(testing)
