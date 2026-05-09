# Decision 08 — Phase 3 Boundary Contract

- **Date**: 2026-05-09
- **Phase / Module**: Phase 3 entry / Safety + Production Hardening
- **Status**: **Decided** — 用户 2026-05-09 approve all,11 条 checklist 全 ✅;
  D13.1 / D13.2 status 从 🟡 转 ✅
- **Framing 基础**: [`learnings/phase-3-framing.md`](../learnings/phase-3-framing.md)
  ——LLM 调用 ↔ production RPC 的同构性;5 件配套抽象的演化故事

---

## Naming Note: middleware ≡ hook

> **本文档全文用 "middleware" 这个 RPC 术语描述概念;代码 API 和 module 命名保留
> "hook"(行业惯例对齐 Anthropic / OpenAI / Claude Code)。两个词在我们这里指
> 同一件事。**

为什么 boundary 文档用 middleware:
- 在 [phase-3-framing.md §2.3](../learnings/phase-3-framing.md) 的 RPC 演化故事里,
  middleware 是**精确术语**(横切逻辑被抽到管道层的产物,Express/Koa/NestJS/gRPC
  interceptor 都是它)
- 我们 dispatch loop 是**真的线性管道**(LLM tool_use → permission → execute →
  ToolResult),Middleware 的洋葱模型 mental model 跟代码形态对得上
- 用 middleware 这个词,你/未来读者从代码立刻能跳回 framing doc 的演化故事
  (RPC 演化第二站)

为什么 API 命名保留 hook:
- Anthropic Claude Code / OpenAI Functions / 整个 Claude 生态用 "hook"
- 这是 Emacs / Git / WordPress 30 年文化继承,工程师见到 `Hook` 类型立刻知道是
  "lifecycle 点挂回调"
- 跟未来 MCP / 第三方扩展生态对齐成本最低

代码里的命名规则:
- Module: `src/openharness/hooks/`
- Public types: `Hook` / `HookEvent` / `HookResult` / `HookContext`
- Docstring 第一段:**"Hook(也称 middleware)——在 dispatch 管道关键边界挂载
  的横切逻辑"**

---

## Context

Phase 2 closed runtime + test-wise on 2026-05-08。Combined retrospective
[`learnings/phase-1-and-2.md`](../learnings/phase-1-and-2.md) 写于 2026-05-09,
其 §10 列出 6 条必拍决策 + 工程债清单。本文件正式拍板。

按 framing doc 的 RPC 演化故事,production RPC 框架反复演化出 5 件配套抽象:

| 配套(RPC 名) | 解决的痛点 | 我们项目里 | Phase |
|---|---|---|---|
| **1. Retry / backoff** | 网络不可靠,inline 重写 | `api/retry.py` 指数退避 + jitter | ✅ Phase 1 |
| **2. Middleware 链(hook)** | 横切逻辑要写 100 遍(auth/log/metric/trace) | 🔨 D13.1 | Phase 3 |
| **3. AuthZ 子系统** | 安全规则演进 ≠ 业务演进 | 🔨 D13.2 | Phase 3 |
| **4. Observability 三件套** | 事后回查没痕迹 | 🔨 D13.6 | Phase 3 |
| **5. Error Taxonomy** | 一个兜底吞信息 | 🔨 D13.4 | Phase 3 |

**Phase 3 = 把后 4 件配套配齐**(配套 1 已 Phase 1 做完);D13.3 (`is_read_only`)
是配套 3 (AuthZ) 的修饰 metadata,D13.5 (retry hardening) 是配套 1 的扩展(推
Phase 4 跟 Compaction 一起做)。

---

## Phase 3 Essence

> **把 LLM 调用从「能跑」(Phase 2)升级到「可放心给别人用」(production)**
> ——按 RPC 30 年学到的判断 framework,把横切逻辑(middleware/hook)、
> 安全策略(AuthZ)、观察手段(observability)、错误分类(error taxonomy)
> 4 件配套一起装齐。

**Deliverable**: `oh ask "..."` 在以下场景都行为正确:

- 危险命令被精确拦截(不只是 hardcoded `rm -rf /`)——`oh ask "rm ~/.ssh/id_rsa"`
  必拒
- 用户能用 middleware (hook) 挂自定义横切逻辑——log / cost / memory inject /
  custom safety rule
- 外部观察者(structlog handler)能完整看到 dispatch 事件流——为 Phase 4 session
  persistence 埋伏笔
- LoopLimitExceeded / ToolError / PermissionError / HookError 各有独立 except
  分支与精确 hint

---

## Scope

### IN (Phase 3 must-do)

| Module | Responsibility | RPC 配套 / framing doc 锚 |
|---|---|---|
| `hooks/` (new) | Middleware (hook) 链 + 5 挂载点 + Hook executor + return-value 决策(D13.1) | **配套 2** / framing §2.3 |
| `permissions/checker.py` 升级 | 三层 tier:hardcoded paths + glob + mode-based(D13.2) | **配套 3** / framing §2.4 |
| `observability/` (new) | structlog 接入 + dispatch 关键点结构化日志(D13.6) | **配套 4** / framing §2.5 |
| `errors/` 重组 | `OpenHarnessError` root + `ToolError` / `PermissionError` / `HookError` / `LoopError`(D13.4) | **配套 5** / framing §2.6 |
| `tools/base.py` | 加 `is_read_only: bool = False` 类属性(D13.3) | 配套 3 的 input metadata |
| 工程债 batch | P1 carryovers + Bash `(no output)` 哨兵 + Edit atomic write + Settings File 层重审 | 见 retro §10.2 |

Estimated 6 capabilities, 2-3 weeks。

### OUT (deferred, with rationale)

| Deferred to | Item | 为什么不现在做 |
|---|---|---|
| Phase 4 | Cost cap / token budget tracker | 强耦合于 Compaction(都是 budget mgmt);一起设计避免双重重构(D13.5) |
| Phase 4 | Per-tool retry policy(application 层) | 当前没有 use case;`is_error=True` 喂回让 LLM 自己 retry 已够,framework retry 在 prompt-aware 场景下反而有害 |
| Phase 5 | MCP / Slash commands / Skills | 扩展点本身;Phase 3 的 middleware (hook) + observability 是它们的基础设施 |
| Phase 6 | **Parallel tool execution** | `is_read_only` 加上(D13.3)是 Phase 3,但 parallel 派发本身需要 D6.3 翻转 + render 重排,工程量大 |
| Phase 6 | Sub-agents / Worktree / Docker sandbox | Advanced isolation;middleware 之上的进一步隔离 |
| Out of scope | OpenTelemetry / Prometheus / 商业 APM | structlog 已覆盖单人项目需求;留 handler 接口位让 Phase 5+ 可选接入 |

---

## Decisions

> **Status legend**: ✅ confirmed / ❌ override / 🟡 needs more discussion before implementing

### D13.1 — Middleware (hook) 链 + 5 挂载点 ⭐⭐⭐ (最关键)

**Status**: ✅ — 2026-05-09 approve;子问题 1-3 留给 P3-T4 入口的 Three-Axis 展开。

#### Framing(对照 framing doc §2.3)

> **痛点**:用着用着发现每个 handler 第一行都长一样——auth、log、metric、trace
> 业务一行,横切五行。inline 写就是**重复 + 易漏 + 无法统一升级**。
>
> **抽象** = 把横切逻辑从"每个 handler 自己写"提到"框架统一管"。

我们的对应场景:

```python
# 不抽 middleware,用户想加"每次 tool 调用前打 log + cost track"就要这样:
class MyTool(BaseTool):
    async def execute(self, args, ctx):
        log.info(f"tool called: {self.name}", input=args)    # ← 每个 tool 重复
        cost.track_tool(self.name)                            # ← 每个 tool 重复
        result = ...real work...
        log.info(f"tool done: {self.name}", output=...)       # ← 每个 tool 重复
        return result
```

5 个 tool 重复 5 遍。加新 tool 漏了一行 silent breakage。

**抽象 = Middleware (hook) 洋葱模型**:

```
LLM tool_use
   ↓
 [Auth middleware]    ┐
   ↓                   │  ← 这一层是用户能挂的横切逻辑
 [Log middleware]      │     (PreToolUse hooks)
   ↓                   │
 [Cost middleware]    ┘
   ↓
 tool.execute(args, ctx)   ← 业务这一行
   ↓
 [Cost middleware]    ┐
   ↓                   │  ← 返回路径上 middleware 倒序运行
 [Log middleware]      │     (PostToolUse hooks)
   ↓                   │     (洋葱模型)
 ToolResult            ┘
   ↓
 LLM 看到结果,继续 turn
```

#### 选项

- **A) 完整版**(matches OpenHarness §9):7 lifecycle events × 4 hook types
  (`allow` / `deny` / `modify` / `observe`)。Hook 注册时声明 type;executor
  按 type 决策。
- **B) 简化版**:仅 `PreToolUse` + `PostToolUse` 两个事件;hook 是 plain async
  callable;没有 type 概念,return value 即决策。
- **C) Protocol-based**:用户实现 `Hook` Protocol,事件名由 emitter 任意定义;
  framework 提供 emit + dispatch。

#### Recommendation: **A 的简化变体——5 events + unified return-value semantics**

```python
# 5 lifecycle events(覆盖即时 + 留位)
HookEvent = Literal[
    "PreToolUse",     # tool 派发链入口(在 _dispatch_one 之前)
    "PostToolUse",    # tool 派发链出口(在 _dispatch_one 之后)
    "PreApiCall",     # LLM 调用入口(在 client.stream_message 之前)
    "PostApiCall",    # LLM 调用出口(在 stream 完成之后)
    "OnError",        # 异常路径(任何 dispatch 阶段抛错)
]

# 单一 callable signature;return value 表达决策
Hook = Callable[[HookContext], Awaitable[HookResult | None]]

@dataclass(frozen=True)
class HookResult:
    decision: Literal["deny", "modify", "allow"]
    message: str | None = None         # for "deny": 喂回 LLM 的 ToolResult.output
    new_input: dict | None = None      # for "modify": 替换 tool_use.input
```

**5 个挂载点对应"用户必然撞到"的横切逻辑**(framing doc §6 视角):

| 挂载点 | 用户拿来干什么 | 不抽要重复在哪 |
|---|---|---|
| `PreToolUse` | 自定义权限规则 / 改 tool 参数 / cost 预扣 | 每个 tool 的 execute 第一行 |
| `PostToolUse` | 日志 / metric / cost 累加 / 结果 sanitize | 每个 tool 的 execute 末尾 |
| `PreApiCall` | Memory 注入 / context truncate / 模型路由 | run_query 每轮的 request build |
| `PostApiCall` | Token usage 累加 / cost cap 触发 | run_query 每轮的 stream 消费末尾 |
| `OnError` | 统一上报 / fallback / 错误丰富化 | cli.py 5 except 分支前 |

#### 为什么不分 4 个 hook type(allow/deny/modify/observe)

- **4 个 type = inheritance 视角**——middleware 注册时要选一个"我是哪种"
- **1 callable + return-value = expression 视角**——同一个 middleware 这次 deny、
  下次 modify、再下次 observe 都行
- Express / NestJS / gRPC interceptor / Koa **全部走 expression 视角**——这是 RPC
  middleware 30 年的工业共识
- 我们的代码精确对齐这个共识

#### 为什么不加 SessionStart / TurnStart / TurnEnd 这种事件

- 在 framing doc 视角下,这些**不是横切逻辑,是事件流**——消费者已经能从
  `ApiStreamEvent` 推导
- 不进 middleware 系统,留给 observability(D13.6)的事件流处理

#### Reversibility

- 加新 event 名字是 additive(union 加成员)
- 改 `HookResult` shape 是 breaking → **这条最值得多花时间确认 shape**
- 加 hook type 概念回来 = user-facing API 重构(避免)

#### Three-Axis 入口前需要回答的子问题

1. Hook 链中多个 hook 同时返回 deny / modify 时怎么 resolve?(first-wins?
   merge?)—— Express 是 first-wins,先 short-circuit 的赢
2. Hook 抛异常 = 拒绝(deny)还是 = 让 OnError 处理? —— 倾向后者(异常是异常,
   deny 是 deny)
3. Hook 是否能阻塞 LLM 流式输出? —— probably no,只在 dispatch 边界

---

### D13.2 — AuthZ 子系统升级(策略文件 + decision point + decision engine)⭐⭐⭐

**Status**: ✅ — 2026-05-09 approve;Tier 1/2/3 具体 deny pattern 留给 P3-T3 入口
的 Three-Axis 展开。

#### Framing(对照 framing doc §2.4)

> **痛点**:最初 inline `if not user.is_admin`。明天 manager 也能调,后天加 IP
> 白名单,再后天某个 service 在 dev/prod 规则不同。
>
> **抽象** = 抽到独立子系统:策略文件 + 决策点(PEP)+ 决策引擎(PDP)。
> 具体形态:RBAC / ABAC / OPA。

我们的对应场景:

Phase 2 的 `DenyListChecker` 只硬编码了几条 catastrophic 模式(`rm -rf /` 等)。
production 用户会遇到:
- "我希望 read 任意文件,但 write 限制在 cwd 内"
- "我项目里有敏感目录 `secrets/` 不允许读"
- "Bash 不允许跑 `git push`"
- "Edit 不允许动 `.env` 之类的文件"

**inline 写就是 PermissionChecker 内部一堆 if-else**,跟"安全规则演进 ≠ 业务演进"
撞上。

#### 选项

- **A) 完整 9 步算法**(OpenHarness §8 + Appendix A.2 直搬)——sensitive paths
  + glob + mode + permissions.json + 9 步 evaluation chain
- **B) 增量分级**(三层 Tier):
  - **Tier 1**: hardcoded sensitive paths(`~/.ssh/`, `/etc/passwd`, `~/.aws/`)
  - **Tier 2**: glob-based deny rules in Settings(用户配置)
  - **Tier 3**: mode-based(`is_read_only` tools 走 lax;write/exec 走 strict)
  - 复杂自定义场景 → middleware (hook) `PreToolUse`(D13.1)
- **C) Deny-list + middleware**:framework 只查 hardcoded 危险模式;其余完全
  外包给 middleware

#### Recommendation: **B + 与 D13.1 联动**

- A 一上来 9 步对 single-user 学习项目密度过高,且许多步(如 user-defined
  permissions.json)在 Phase 5 多 profile 出现前用不到
- C 让 framework 缺基线安全;新用户期望"开箱拒绝读 ~/.ssh"——这种常识必须
  framework 自带
- B 是合理中点:**Tier 1 给基线、Tier 2 给配置、Tier 3 给类型分层、middleware
  给完全自定义**

`PermissionChecker.evaluate(tool_name, args, ctx) → Decision` 接口在 P2-T6 已
存在;这次只换 implementation,**接口不动**——这是 P2-T6 时这条接口"故意小"
的红利。

#### Reversibility

- 内部算法替换不影响外部接口(`evaluate` 签名锁死)
- Tier 4/5(用户 permissions.json 等)是后续 implementation 扩展,不破坏 API

---

### D13.3 — `is_read_only` 加到 BaseTool + parallel tool 推迟到 Phase 6

**Status**: ✅ confirmed by retro §10.1 #3。

#### Decision

- **Phase 3 加** `is_read_only: bool = False` 到 `BaseTool` 类属性
- **Phase 6 做** parallel tool execution(D6.3 翻转 + render 重排)

#### Framing

`is_read_only` 不是独立的 RPC 配套抽象,**是配套 3(AuthZ)的输入 metadata**——
让 D13.2 的 Tier 3(mode-based)能区分:

- read tools(Read / Grep)→ AuthZ 默认 ALLOW(读取通常无害)
- write/exec tools(Write / Edit / Bash)→ AuthZ 走严格路径

#### 为什么拆开做

- 加属性 5 行代码,**D13.2 立刻受益**
- Parallel 涉及 dispatch 重构 + UI 重排 + race condition 测试,工程量大
- 拆开做让 Phase 3 心理负担可控

#### 5 工具的取值

- Read / Grep → `True`
- Write / Edit / Bash → `False`(Bash 不可知,默认保守)

---

### D13.4 — Error Taxonomy + root rename(对标 gRPC status code 精神)

**Status**: ✅ confirmed.

#### Framing(对照 framing doc §2.6)

> **痛点**:最早 RPC 失败就一个字 "Failure"。但 client 想分别处理:
> 网络问题 → 自动重试;参数错 → 不要重试,告诉用户改;权限不足 → 引导申请。
>
> **抽象** = gRPC 14 个标准 status code / HTTP 4xx vs 5xx / application error
> vs protocol error。让 caller 能分别处理。

我们的对应场景:

`cli.py` 现在的 5 except 分支:`ValidationError`(配置错)/`AuthenticationFailure`
(401)/`RateLimitFailure`(429)/`RequestFailure`(其他 API 错)/
`OpenHarnessApiError`(兜底,**`LoopLimitExceeded` 落这里是 Phase 2 巧合**)。

Phase 3 加 middleware (hook) + 完整 permission 后,会出现:
- `HookError`:hook 自身崩(不是 hook 返回 deny)
- `PermissionError`:permission 决策点出错(不是普通 deny)
- `LoopError`:loop 控制流错误(`LoopLimitExceeded` 该归这里)

兜底到一个 root 让 hint 措辞含糊。

#### Decision

```python
# 现在(Phase 2):
OpenHarnessApiError                # ← 名字 implies "API only"
├── AuthenticationFailure
├── RateLimitFailure
├── RequestFailure
└── LoopLimitExceeded               # ← 落兜底是 Phase 2 巧合

# Phase 3 重组为:
OpenHarnessError                    # 真 root
├── OpenHarnessApiError             # API 层(transport 失败)
│   ├── AuthenticationFailure
│   ├── RateLimitFailure
│   └── RequestFailure
├── ToolError                       # tool execution / validation 层
├── PermissionError                 # AuthZ 决策点错(不是普通 deny)
├── HookError                       # middleware/hook 自身崩
└── LoopError
    └── LoopLimitExceeded
```

#### 为什么

- root 名字 `OpenHarnessApiError` 暗示"API 错";`LoopLimitExceeded` 不是 API 错
  落兜底是逻辑漏洞 —— Phase 3 修
- cli.py 的 except 分支可以演进成精确分类:每条专属 hint
- 类型分层让 caller 能精准处理(framing doc §2.6 的核心痛点)

#### Cost

- 一次性 rename + import 链更新,预计半天
- 测试名 follow

#### Reversibility

- Rename 是 breaking,但 Phase 2 内部代码量小,一次改完
- 外部目前没人 import → rename 时机最好就是现在

---

### D13.5 — Retry hardening 范围(配套 1 的扩展,推 Phase 4)

**Status**: ✅ confirmed defer.

#### Framing

属于 RPC 配套 1 (Retry) 的 application 层扩展。Phase 1 已落 transport 层,
application 层 retry / cost cap / circuit breaker 跟 Compaction 强耦合。

#### Decision

**Phase 3 不做** application-layer retry / cost cap / circuit breaker。
保留现有 `api/retry.py` 的 transport 层 retry。

**Phase 3 实际做的**:
- 重试发生时的 **可观测性增强**(D13.6 structlog 加 retry 日志)
- 文档化清楚 transport vs application retry 的边界(写进 SPEC §6 / `learnings/`)

#### 为什么

- **Cost cap 强耦合于 Compaction**:都是 budget mgmt;Phase 4 一起设计避免双重
  重构
- **Per-tool retry 没有 use case**:`is_error=True` 喂回让 LLM 决定 retry 策略,
  framework retry 在 prompt-aware 场景下反而有害(LLM 可能已经 plan 好下一步,
  framework 再 retry 撞车)
- **Circuit breaker** 是分布式系统模式,1-user 项目用不到

---

### D13.6 — Observability 三件套(配套 4)

**Status**: ✅ confirmed.

#### Framing(对照 framing doc §2.5)

> **痛点**:10 个 server 一起跑,某次调用慢了。哪一 hop?哪个参数触发的?
> 跑完日志一冲就没了。
>
> **抽象** = Logger + Metrics + Tracing 三件套。每次调用必须留指纹:谁发起、
> 什么时间、参数、返回、耗时、有没有错。

我们的对应场景:

production 用户出问题想回查:
- "上次跑 `oh ask "..."` 为什么 LLM 选了 Bash 而不是 Read?"
- "tool 失败了几次?哪次?"
- "permission denied 触发的具体 args 是啥?"

Phase 2 完全靠 stdout/stderr 输出。**事后回查 = 没痕迹**。

#### Decision: **A + 留位 B + Phase 5 留位 C**

- **A 现在**:`structlog` 接入,在 dispatch loop 关键点加结构化日志:
  ```python
  logger.info("turn_start", turn=N, model=...)
  logger.info("tool_dispatch", tool=name, input=args, run_id=...)
  logger.info("tool_complete", tool=name, is_error=..., duration_ms=...)
  logger.warning("retry", attempt=N, error=...)
  logger.error("permission_denied", tool=name, args=...)
  logger.error("hook_failed", hook=name, event=event_name, error=...)
  ```
- **B 留位**:Phase 4 加 `EventLogger`(JSONL persistence)时,同一份事件流可
  直接通过 structlog handler 写文件——**不需要再次 instrument**
- **C 留位**:Phase 5+ 接 OpenTelemetry / Prometheus 时,structlog 是
  pluggable backend(structlog 设计哲学:你写 logger 调用,后端可替换)

#### 为什么 structlog 优先

- **Python 生态主流**(零中间件依赖)
- 接 stdlib `logging` framework,与 Typer / asyncio 协同好
- 比自家 `EventLogger` 省工
- pluggable backend = 未来不被锁死

#### LLM context 不进 log

- 隐私:messages list 含用户 prompt + tool input(可能含 path、command),只
  log 长度不 log 内容
- Cost:full messages 量大,污染 log volume
- tool input 中含 file path / command 等可能含敏感信息的字段需要 sanitize 或
  只 log shape

---

## Process Meta-Decision

### Phase 1 / Phase 2 closure first

Before Phase 3 build:

1. **CI push**(Phase 1 carryover) —— `git push origin main`,5 分钟
2. **`learnings/phase-1-and-2.md` 人工 review** —— 已写完,跑一遍 checklist
3. **`tasks/phase-3-plan.md` 起草** —— 跟此 boundary 同期

### Phase 3 build order

按 RPC 配套依赖链 + framing doc 视角:

| # | Capability | RPC 配套 | 估时 | 依赖 |
|---|---|---|---|---|
| **P3-T1** | Pre-flight cleanup batch(P1 carryovers + Bash sentinel + Edit atomic write + `is_read_only`) | — | 2-3 天 | — |
| **P3-T2** | Error Taxonomy 重命名 + 扩展(D13.4) | **配套 5** | 1 天 | P3-T1 |
| **P3-T3** | AuthZ 子系统升级 三层 Tier(D13.2) | **配套 3** | 2-3 天 | P3-T2(借 PermissionError) |
| **P3-T4** | Middleware (hook) 链(D13.1) | **配套 2** | 3-4 天 | P3-T2(借 HookError) |
| **P3-T5** | Observability 三件套 logger 部分(D13.6) | **配套 4** | 1-2 天 | P3-T3 + P3-T4(都是 log 点) |
| **P3-T6** | 测试加固到 95%+ + Phase 3 retro | — | 1-2 天 | 所有上面 |

总估 **2-3 周**(同 Phase 2 节奏)。

每个 capability 入口前做 Three-Axis 讨论,按 ARCHITECTURE.md §5 模板。

### Three-Axis 优先级排序

按入口 Three-Axis 的预期 ROI:

- **P3-T4 Middleware (hook)**:深度讨论(1-2 小时)—— D13.1 的子问题(链 resolve
  / 异常路径 / 流阻塞)在这里展开
- **P3-T3 AuthZ Tiers**:中等讨论(45 分钟)—— Tier 1/2/3 各自的 deny 模式具体
  pattern
- 其他 4 个:轻度讨论(15-30 分钟)—— 工程为主,产品决策已锁

---

## Consequences

- `tasks/phase-3-plan.md` 紧接此文件起草,组织 6 capability + Three-Axis 入口锚点
- 此文件是 Phase 3 contract,任何偏离需要更新这里(不只改代码)
- D13.{1,2} 的 🟡 status 在用户 review 后转 ✅ 才能进 P3-T4 / P3-T3 build
- `learnings/phase-3.md` 在 Phase 3 close 时写(per CLAUDE.md "模块完成后")
- **本文件用 middleware 词汇,代码用 hook 命名,Naming Note 章节锁住等价性**

---

## User review checklist

- [ ] Naming Note 章节(middleware ≡ hook)接受?如果同意,后续 Phase 4-6 文档
  按同一规则
- [ ] Phase 3 Essence 描述对吗?(2 句话:配齐 RPC 5 配套的后 4 件)
- [ ] OUT 列表 OK?(特别是 cost cap 推 Phase 4 + parallel 推 Phase 6)
- [ ] D13.1 middleware 5 events 列表对吗?是否漏了什么?(如 SessionStart/End?)
- [ ] D13.1 `HookResult` 单一 callable + return-value semantics 接受?还是
  更倾向 4 hook types?
- [ ] D13.2 三层 Tier 对吗?(hardcoded paths + glob + mode-based)
- [ ] D13.3 5 工具的 `is_read_only` 取值同意吗?Bash 默认 False 保守,可以吗?
- [ ] D13.4 root rename(`OpenHarnessApiError → OpenHarnessError`)接受吗?
- [ ] D13.5 cost cap / per-tool retry 推 Phase 4 OK?
- [ ] D13.6 structlog vs 自家 EventLogger,选 structlog OK?
- [ ] Build order 6 capability 顺序合理吗?
- [ ] 估时 2-3 周接受吗?
