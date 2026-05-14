# Learnings — Phase 3 (Safety + Production Hardening)

> Phase 3 起止 / 2026-05-10 – 2026-05-14 / 用时 ~5 天
> 6 capabilities (P3-T1…T6) / 30+ sub-units / 32 commits / 622+ tests / 96.9% coverage
>
> 本文件**不是** sub-unit 的合集 —— 那些在 commit message 里已经详尽记录。
> 它回答的题:**做完 Phase 3,关于 "把封闭工具变成可配置政策中继" 这件事,
> 学到了什么 framework-level 的东西。**

---

## 1. 数据点

| 维度 | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Capability(task) | 5 | 6 | **6** (T1-T6) |
| Sub-units | — | — | **30+** |
| Decision records | 5 (D1-D5) | 2 (D6-D7) | **2** (D8 boundary, D9 settings-file) |
| Sub-decisions | — | 35+ | **20+** (D13.1-D13.6 + Three-Axis micro-decisions) |
| 总测试数 | 173 | 352 | **628** (+276) |
| 总覆盖率 | 92.83% | 94.76% | **96.90%** (gate 抬到 95%) |
| 总 commits | 43 | 35 | **32** |
| Phase 3 加的 module | — | — | `errors/` + `permissions/` + `hooks/` + `observability/` |
| Phase 3 触碰的既有 module | — | — | `api/retry` + `engine/query` + `cli` + `config/settings` + 5 tools |

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P3-T1 — Pre-flight cleanup batch** | 把 Phase 1/2 的 6 个 carryover 一次性清掉,**用一个 batch 而不是新 capability 上的 sub-units 散布** —— 后续 5 个 capability 才能 diff 聚焦在新概念上。Phase 3 的"开机动作"。 |
| **P3-T2 — Error Taxonomy 重命名 + 扩展** | `OpenHarnessApiError` 降级为 `OpenHarnessError` 的 subclass + 加 `ToolError` / `PermissionError` / `HookError` / `LoopError` 4 个 namespace —— **error 分类不是 5 个新 except 分支,是给后续 P3-T3/T4 提供"我崩了的语义"**。 |
| **P3-T3 — AuthZ 三层 Tier** | Tier 1 framework-owned + Tier 2 user-glob + Tier 3 mode-based + Bash 灾难 carryover = **first-match-wins 政策栈**;`DecisionResult` 替代 bare enum,**LLM 看到的拒绝理由可以指导它 plan B**。 |
| **P3-T4 — Hook 系统 ⭐ 主菜** | 5 lifecycle events + `HookResult` 单类 + `execute_hook_chain` 算法核心(**modify 累积 + first-deny-wins + OnError 一层防递归**);**hook 跑在 AuthZ 之后** —— 加严可,松绑不可。 |
| **P3-T5 — Observability (structlog)** | 8 log 点 + 3 层 ID(`run_id` / `turn_id` / `tool_use_id`)+ sanitize 二层防御(processor 兜底 + helper 显式)+ stderr-only;**"穷人版 trace 的 wire format"** —— 留出口接 OTel / LangSmith。 |
| **P3-T6 — 测试加固 + retro** | Coverage 94.76% → 96.9%,gate 95%;**Coverage.py 的 per-module gate 太脆,放弃** —— 改用 capability checkpoint 的 audit。 |

---

## 3. Framework-level 主题 — Phase 3 真正学到的

### 3.1 横向扩展层 vs 纵向 dispatch 流 —— **engine 不感知 horizontal capability**

Phase 3 在 dispatch pipeline 上**插了 5 件事**(AuthZ 检查 + 4 个 hook 事件
+ DRY_RUN 短路),但 `_dispatch_one` 的核心 10 步**直接表达业务**;横向层
(observability / hook chain / sanitize)都**不在 `_dispatch_one` 内部**,
而是:

- **AuthZ**:作为 `_dispatch_one` Step 3 的一个 hard gate(framework safety
  baseline 强制 inline)
- **Hook**:Step 5(PreToolUse) / Step 7(PostToolUse) 各 1 个 chain 调用
- **Observability**:**全部在 `run_query` 外层 + 各 module 内部**,
  `_dispatch_one` **0 行 log call** —— observability 不污染 dispatch 逻辑

判决:**dispatch 流 = 业务核心,扩展点 = "在已知位置开窗"**。开窗位置必须
显式(我们用 5 个 lifecycle events 命名),而不是让扩展层 monkey-patch 业务。
这是 Express middleware / Java Servlet Filter / gRPC interceptor 30 年学到
的同一件事。

### 3.2 AuthZ 单点 vs Hook 多点 —— **对称设计的对偶**

|  | AuthZ | Hook |
|---|---|---|
| 谁拥有 | Framework | User |
| 可绕过吗 | 不可(safety baseline) | 可(framework 之内) |
| 抽象形态 | Protocol(单 checker) | Registry(FIFO chain) |
| 决策语义 | first-match-wins 三态 | first-deny-wins / modify 累积 |
| 错误处理 | DENY 走 is_error result | deny 走 is_error,crash 走 HookError |

这两个系统**形态互补**:
- AuthZ 是"不可协商的底线"(rule-based,谁都改不了)
- Hook 是"用户的可编程层"(callback-based,任意逻辑)

**关键不变量**:`PreToolUse hook` 跑在 `permission_checker.evaluate` **之后**
(`_dispatch_one` Step 3 vs Step 5)。这意味着 **hook 永远只能加严,不能松
绑** —— 用户 hook 想 ALLOW 一个 AuthZ 已经 DENY 的 tool,**根本跑不到 hook
那一步**。这是 Phase 3 安全姿态的核心。

判决:**安全靠"顺序",不靠"信任"**。AuthZ 在 hook 之前的顺序由 `_dispatch_one`
的 hard-coded 10 步控制 —— 用户无法重排。

### 3.3 Errors as messages 在 Phase 3 三层落地

P2 D8.5 把 "errors as messages" 当口号写下:可恢复错误用 `is_error=True`
flag,programming bug 用 `raise`。Phase 3 把它**变成了三层不变量**:

| 错误类型 | 处理 | LLM 看到的 | 上层看到的 |
|---|---|---|---|
| Tool not found / 验证失败 / 权限拒 / hook deny / tool 自己 is_error | `ToolResultBlock(is_error=True)` | "permission denied: ..." 等可读 reason | 正常事件流 |
| Tool execute() raise / Hook 自己 crash | OnError chain + `ToolError` / `HookError` 上抛 | 看不到(loop 退出) | named except 分支 + 退出码 1 |
| PreApiCall hook deny / max_turns 超 | `LoopError` / `LoopLimitExceeded` 上抛 | 看不到 | "Loop error:" 分类 |
| Provider 错误(retryable / not) | `RequestFailure` / `AuthenticationFailure` / `RateLimitFailure` | retry 期间不见;最终见(若耗尽) | named except 分支 |

判决:**"Recovery vs Raise" 的分界线决定 LLM 能否自适应**。LLM 重试 = is_error
flag;harness/code 本身坏了 = raise 让上层接。这条线在 P3-T3 / T4 / T5 三处
反复验证,没漂移过。

### 3.4 "穷人版 trace" 工程化 —— 3-ID + contextvar + JSONL = 90% 的 OTel 价值

Phase 3 没接 OpenTelemetry,但我们把 trace 的**核心 wire format** 落地了:

```
run_id    (一次 oh ask)         contextvar  in run_query 入口 bind_run()
turn_id   (一轮 LLM 调用)        contextvar  in for-loop bind_turn()
tool_use_id (一次 tool dispatch) 在 ToolUseBlock 上,call-site 直接传
```

3 个 ID 通过 structlog `contextvars.merge_contextvars` processor **自动注入**
每条 log record,call-site **不需要手动传 run_id**。结果:

```bash
oh ask --log-format json "..." 2> trace.jsonl
cat trace.jsonl | jq -c .
```

输出可以**按 run_id 聚合 + 按 timestamp 排序 + 按 turn_id 缩进** 重建成一棵
trace 树 —— 这就是 LangSmith / Helicone 在 UI 上做的事。Phase 4+ 想接 OTel
exporter,**只需把 JSONL 投射到 OTel SpanContext format**,**核心数据不动**。

判决:**先做 wire format,渲染留 Phase 4+**。这是"产品决策已锁,工程为主"
策略的胜利 —— 不冒"自建 trace UI"的险,但留死接口给未来。

### 3.5 Sanitize 二层防御 —— processor 兜底 + helper 显式

隐私契约不能靠"call site 都记得脱敏":

```
Layer 1: processor 全局拦截(无差别 / defense-in-depth)
   - SENSITIVE_KEYS = {api_key, password, token, secret, ...}
   - TOKEN_PATTERNS = (sk-..., ghp_..., AWS AKID, JWT, ...)
   - 兜底所有遗漏

Layer 2: call-site helper(语义化 / 精细)
   - sanitize_path(path, cwd)    → cwd-relative 或 <redacted>
   - sanitize_command(cmd)        → first_token + len
   - call site 显式调用
```

为什么不能只一层?
- **只 processor**:看到 `key="path"` 不能假设 value 是 fs path(可能是 URL
  path / JSON path / S3 key);**processor 不能做语义化**
- **只 call-site**:开发者忘了脱敏一处就泄漏;**没有兜底**

判决:**"安全 = 默认不漏 + 显式精细"**。Sentry / Datadog 多年的实践;P3-T5
把它做进 wire-format-only 层(processor 在 renderer 之前),保证"无论谁忘
了什么,credentials 都不会到 stderr"。

测试钉了 4 处具体角度:`SENSITIVE_KEYS` 大小写/横线归一(11 parametrized
variants);`TOKEN_PATTERNS` 7 种 token shape 全识别;**第三方 logger 的
INFO 记录不出现在 stream**(httpx 真世界爆出来的 leak);call-site command
helper 在 `API_KEY=sk-... cmd` env-prefix 模式下也能 catch。

### 3.6 真踩坑 4 个 —— framework 层的 pitfall 都很微妙

| # | 坑 | 触发 | 修复 | 钉子测试 |
|---|---|---|---|---|
| 1 | `structlog.cache_logger_on_first_use=True` + module-level `logger = get_logger(...)` | CLI 测试先跑 → engine.query.logger 在 import 时 cache;observability 测试后跑 → reconfigure 无效 | `cache=False` + `reset_defaults()` | 全套 597 + 测试稳定 |
| 2 | `event` 是 structlog 保留 kwarg(消息名) | hook executor `logger.debug("hook_invoke", event=event)` 报 `multiple values for argument 'event'` | 重命名 `hook_event=event` | 22 个 hooks test 一次性炸 + 修后绿 |
| 3 | pytest-asyncio 在 fixture 跑完后重置 root.handlers | `configure_logging` 在 fixture 里调 → test body 启动后 handler 被冲掉 → log_stream 空 | configure 改在 test body 里调 | docstring 警示 + 沿用至 5d/5e |
| 4 | openai SDK 的 httpx INFO 记录穿透 stderr | 真 Qwen 跑 `oh ask --log-format json` 时 stderr 出现 `HTTP Request: POST ...` 非 JSON 行 | `_NOISY_THIRD_PARTY_LOGGERS = ("httpx", "openai", ...) setLevel(WARNING)` | 4 个 parametrized + INFO 不出现 + WARNING 仍透传 |

判决:**"读完文档觉得自己懂了 ≠ build 完踩了坑才真懂"**。Phase 3 这 4 个
坑都是 docstring 警示后 Phase 4+ build 时**不会再翻车** —— learnings 文件
真正的价值就是这些。

### 3.7 Three-Axis 在 Phase 3 的工作模式 —— **轻度讨论 + 紧凑 build**

Phase 1 时 Three-Axis 讨论占 1-2 小时,产出大量 sub-decision。Phase 3 多数
capability 走"轻度 15 分钟",原因:
- 产品决策大都已锁(plan 写得很细 + decisions/08 boundary 提前固化)
- 工程方向有 OpenHarness REFERENCE 参照

只有 **P3-T4 Hook 系统**做了深度 Three-Axis(1.5 小时),产出 10 个 Micro-
Decision(A-J)—— 因为 hook 的**链 resolve 语义**(modify 累积 vs 投票
vs first-deny-wins)在业界有多种主流方案,需要明确选 Express 模型。

判决:**深度按"产品决策有多少未锁"调整**。规则化(Phase 4-7 都这么做):
- 产品决策已锁 / 工程主导 → 15-30 分钟
- 产品决策有 1-2 个开放分歧 → 45 分钟
- 链 resolve / 状态机这类核心算法 → 1.5-2 小时

---

## 4. Phase 3 的契约预测 —— Phase 4 会验证什么

> P1/P2 retro 的 §3 评估了"Phase 1 押的契约 Phase 2 实际怎样"。这里反过来:
> Phase 3 押的契约,Phase 4 会怎么压测?

### 4.1 Hook 系统的 5 events 够不够

Phase 4 (Compaction) 会加 memory injection —— **大概率走 PreApiCall hook**
的 `modify_request` 路径。如果够用,P3-T4 的 5 events 设计胜出。

**潜在缺位**:`SessionStart` / `SessionEnd` —— 一次 `oh ask` 整体的开始 /
结束 hook。今天用 `bind_run()` 入口可以模拟,但没正式 hook event。Phase 4
如果需要"loop 开始前注入 memory + loop 结束后 persist memory",可能要加。

**预测**:加 `SessionStart` / `SessionEnd` 两个 event,5 → 7。

### 4.2 三层 ID 够不够

Phase 6 sub-agent 会有"子 agent 的子 trace"—— 现有 3 层 ID 不足以描述
parent-child agent 关系。但 contextvars task-local 隔离我们已经测过
(P3-T5.5a `test_concurrent_tasks_see_independent_run_ids`),Phase 6 加
`parent_run_id` 字段即可。

**预测**:`parent_run_id` + 子 agent 在 hooks 里 emit 自己的 trace。
3 层 → 4 层(嵌套)。

### 4.3 AuthZ 三层够不够

D13.2 说"Tier 3 mode-based 是 P3 简化版,P4+ 可加"。预测 Phase 5+ 会:
- 加 Tier 4 用户自定义 callback checker(plugin 走 hook? 还是 checker?)
- Bash command 的细粒度白名单(不只 deny pattern,加 allow pattern)

**预测**:Tier 4 落地后,`TierBasedPermissionChecker` 需要扩展;考虑用
**checker chain** 模式(类似 hook chain)替代当前的 first-match-wins 单 checker。

### 4.4 Observability 是不是真够用

Phase 4 Compaction / Phase 6 sub-agent 加进来后,trace 会变深。当前 8 个
log point + 3 层 ID 是否足够?**很可能**:
- 加 `compaction_start` / `compaction_complete`(P4 内部状态变化)
- 加 `subagent_spawned` / `subagent_completed`(P6 任务委派)

**预测**:8 events → 12 events。但**核心 wire format(structured KV + ID
contextvar)0 改动**。

---

## 5. 给 Phase 4 的 input

1. **不要重复 P3-T4 的 hook 深度讨论** —— hook chain 算法已经稳定,
  Phase 4 想加 event 直接 additive。
2. **Compaction 的契约层 boundary 先锁** —— 跟 P3 boundary doc 同款。
  Compaction 是 horizontal capability(类似 Hook),挂在哪个 lifecycle
  位置先想清楚:`PreApiCall` modify 改 messages? 单独抽 messages
  layer? 这两条路工程影响差很多。
3. **trace 流的 exporter 工作** —— 如果 Phase 4 用户开始要 LangSmith
  集成,做一个 `observability/exporters/` 子包,把 JSONL 投射成
  OTel SpanContext / LangSmith record。核心代码 0 改动。
4. **真踩坑的 4 个 pitfalls** —— Phase 4 想引入新的 structured logging
  调用前,先回 §3.6 看看。特别是 `cache_logger_on_first_use=False` 已
  是 Phase 3 默认契约,不要无 review 调回 True。

---

## 6. Phase 3 最浓缩的 1 句

> Phase 3 把 OpenHarness 从 "Phase 2 能跑" 升级到 "可放心给别人用" —
> 安全(AuthZ + Hook 政策中继)、可观察(structlog 3-ID trace)、错误
> 分类(5 个 except namespace)、retry 加固(P1 carryover)四件套齐。
> **现在,这个 harness 在第三方手里跑出问题,你能在 stderr 一行 JSON
> 里看到原因。**

---

## 7. Pointers

- Phase 3 boundary: [`decisions/08-phase-3-boundary.md`](../decisions/08-phase-3-boundary.md)
- Phase 3 framing: [`learnings/phase-3-framing.md`](./phase-3-framing.md)
- P1+P2 retro(参考结构): [`learnings/phase-1-and-2.md`](./phase-1-and-2.md)
- 32 个 P3-T commits:`git log --oneline | grep P3-T`
- Phase 3 plan: [`tasks/phase-3-plan.md`](../tasks/phase-3-plan.md)
