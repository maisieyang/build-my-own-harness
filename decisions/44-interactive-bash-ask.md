# Decision 44 — 交互模式 Bash 默认 ASK(F9 修复,对齐 Claude Code)

> Created 2026-07-14 · 上游:dogfood Day 2 F9
> (`learnings/dogfood-day2-error-feedback.md`)、CC 权限模型查证。
> 本周首个安全性改动。

## 一、Why now

F9(dogfood Day 2 双证据):交互模式下 Bash 默认放行(`legacy ALLOW`),
`echo > /tmp/x`(D2-1)、`brew install`(D2-3)全部无提示执行——**Bash
绕过了整个 path-based 文件权限体系**(Tier2/Tier3/headless 门判定依据
全是 `path`,Bash 参数是 `command`,`path is None`,从所有路径门下穿过)。
根因是行业共有结构(CC 官方文档确认:path 授权对任意子进程不可见);
但 CC 交互默认**对每条 Bash 请求许可**(第 1 层人门),本 harness 缺此层,
安全全押 sandbox 而 sandbox 默认关。本决策补上第 1 层。

## 二、In / Out

**IN**:交互模式(headless=False)非只读工具的**无路径**调用(即 Bash
命令,已过灾难 deny-list + git 红线 + allow 规则)→ **ASK**。

**OUT**:

| 推到哪 | 项 | 原因 |
|---|---|---|
| 不做 | 静态解析 Bash 命令的文件副作用 | CC 已证不可行(命令可任意复杂);OS 级隔离归 sandbox |
| 不做 | 改 headless 姿态 | headless 的 fail-closed 门已网住 Bash(step 6),无缺口 |
| 不做 | 动 read-only 工具 | Grep/Read 等本就 ALLOW,不涉越权副作用 |
| 等触发 | Bash allow 规则的 per-project 持久记忆(CC 有) | 会话级已够;持久化是独立 UX 切片 |

## 三、Decisions

### D44.1 — 交互模式无路径的非只读工具 → ASK

**Chosen**:evaluate 决策序 step 7 前加一支:`not headless and not
tool.is_read_only and path is None` → `DecisionResult.ask(...)`。位置在
灾难 deny(step1)/ 红线(step1b)/ allow 规则(step4)/ headless 门
(step6)**之后**——所以:①危险命令仍先被 deny;②用户写了
`Bash(git *)` allow 规则的命令在 step4 已短路为 ALLOW,不会被再问
(对齐 CC "unless matched by approved rule");③AUTO 模式下 ASK→ALLOW
(`_dispatch_one` 既有语义),`--auto` 保留旧的"预信任"行为。

**Why**:补上与 CC 唯一的安全姿态差距;`path is None` 精确圈定 Bash 这类
通用计算通道,不误伤 path-bearing 工具(它们已有 Tier3 覆盖)。

**Alternatives**:①乙(只改文档+拒绝消息)——查证后否决,等于主动选比
参照系更松的默认;②丙(交互全面 fail-closed)——过激,破坏交互 UX 且
超出 F9 证据支持的范围。

**Reversibility**:easy——一支条件删除即回旧行为。

**Anti-scope**:不承诺静态命令分析;"哪些 Bash 安全"的判断留给用户
(ASK)或 sandbox(OS 隔离),harness 不猜。

## 四、Acceptance

- [ ] 红→绿:交互模式 `Bash(echo > /tmp/x)` 从 ALLOW 变 ASK
- [ ] 交互模式 Write 到 /tmp 仍 ASK(不回归)、read-only 工具仍 ALLOW
- [ ] `Bash(git:*)` allow 规则命中的命令仍 ALLOW(step4 短路,不被问)
- [ ] 灾难命令 / git 红线仍 DENY(step1 优先级不变)
- [ ] headless 模式 Bash 仍 DENY(step6 不变,无回归)
- [ ] `--auto` 下 ASK→ALLOW(旧行为保留)
- [ ] 全仓质量门(pytest / mypy --strict / ruff)

## 五、Wiring audit

| Layer | Verdict | 一句话 |
|---|---|---|
| `permissions/tier_based.py` | extension | evaluate 加一支 ASK,不动既有 6 步 |
| `_dispatch_one`(engine) | unchanged | ASK→(AUTO)ALLOW/(DEFAULT)拒并提示 的既有映射直接复用 |
| 其余全部 | unchanged | 纯 AuthZ 决策序内部扩展 |
