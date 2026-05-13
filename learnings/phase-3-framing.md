# Phase 3 入口 framing — LLM 调用 ↔ production RPC 的同构性

> 写于 2026-05-09，Phase 2 close-out 之后、Phase 3 boundary 拍板之前。
>
> 起源：两轮第一性原理对话，回答两个问题——
> 1. Phase 3 = Safety + Production Hardening 「这到底要干啥」？从「心脏跳起来」
>    到「可放心给别人用」之间缺的是什么？
> 2. 「类比 production RPC 框架的 4 件配套」——为什么这种类比的本质同构性成立？
>
> 本文档是 [phase-1-and-2.md §6 LLM-as-RPC-client 视角](./phase-1-and-2.md)
> 的延伸，是 [decisions/08-phase-3-boundary.md](../decisions/08-phase-3-boundary.md)
> 草稿的认知基础。

---

## 1. 触发问题

Phase 1+2 跑通了「LLM + tool + loop」——能 work、心脏跳起来了。但「能跑」≠「可
放心给别人用」。这两者之间缺的不是几个功能，是一组让 implicit 信任假设全部
explicit 出来的工程抽象。

这类抽象在 production RPC 框架里反复出现过——不是巧合，是**同构**。要把这条
论断拆透，得先回答 RPC 框架自身是怎么演化出来的，再把它和 LLM 调用的本质 mapping
做一遍。

---

## 2. production RPC 框架是怎么演化出来的

### 2.1 起点：最朴素的 RPC

两个进程，A 想调用 B 的某个函数。

```
A ──(函数名 + 参数)──> B
A <──────(返回值)──── B
```

最低门槛只需要两件事：
- **Wire format**：双方都认识的数据格式（JSON / Protobuf）
- **Service registry**：A 怎么找到 B（地址簿）

写出来跑得通——但这只是 demo 级。一旦真有人用、用得多，会**反复**撞到一类痛点。
每撞一次，演化出一件抽象。下面是这个演化故事。

### 2.2 演化第一站：网络会出错（Retry）

包丢、超时、限流。inline 写就是每个 caller 都重复一遍 try/except retry。

→ 抽到 transport 层的 retry / backoff / circuit breaker。

**痛点 framework**：本地函数不会「丢失」，远程必须假设网络不可靠。

> Phase 1 我们已经走过这一站（`api/retry.py` + 指数退避 + jitter）。

### 2.3 演化第二站（关键）：每个 handler 第一行都长一样（Middleware）

用着用着发现：

```python
def get_user(req):
    if not authz.check(req): raise Unauthorized   # 安全
    log.info("GetUser called", req)              # 日志
    metrics.incr("get_user.calls")               # 指标
    trace_id = tracing.start("get_user")         # 链路
    try:
        return db.find(req.id)                   # ← 业务只有这一行
    finally:
        tracing.end(trace_id)
```

业务一行，其他全是「每个 handler 都要走一遍」的横切关注点（cross-cutting
concerns）。inline 写就是**重复 + 易漏 + 无法统一升级**。

→ 抽出 **Middleware / Interceptor 链**：

```
request → [Auth] → [Log] → [RateLimit] → [Trace] → handler → response
              ↑       ↑        ↑          ↑
              全部独立、可注册、可拔掉、可重排
```

TS anchor：Express middleware / Koa 洋葱模型 / NestJS Interceptor —— 全是同一个东西。

**痛点 framework**：handler 之外**反复出现的横切逻辑**，必须从「每个 handler
自己写」变成「框架统一管」。

### 2.4 演化第三站：权限规则会变（独立 AuthZ）

最初 inline `if not user.is_admin`。明天 manager 也能调，后天加 IP 白名单，再
后天某个 service 在 dev/prod 规则不同。

→ 抽到独立子系统：策略文件（policy）+ 决策点（PEP）+ 决策引擎（PDP）。
具体形态：RBAC / ABAC / OPA。

**痛点 framework**：安全规则的演进速度 ≠ 业务代码的演进速度，必须解耦。

### 2.5 演化第四站：出问题要能查（Observability）

10 个 server 一起跑，某次调用慢了。哪一 hop？哪个参数触发的？跑完日志一冲就没了。

→ **Logger + Metrics + Tracing** 三件套。每次调用必须留指纹：谁发起、什么时间、
参数、返回、耗时、有没有错。事后能 query、能 alert、能可视化。

**痛点 framework**：production 系统的失败是**事后回查**的，没痕迹 = 没办法 debug。

### 2.6 演化第五站：错误要分类（Error Taxonomy）

最早 RPC 失败就一个字 「Failure」。但 client 想分别处理：
- 网络问题 → 自动重试
- 参数错 → 不要重试，告诉用户改
- 权限不足 → 引导申请
- 服务降级 → 走 fallback

→ gRPC 14 个标准 status code / HTTP 4xx vs 5xx / application error vs protocol error。

**痛点 framework**：失败种类越多，「一个兜底」就越是把信息吞掉，**caller 没法精准反应**。

### 2.7 5 件配套的统一 framework

每件配套都回答同一个问题：

> **有没有一类痛点会随着用户/调用增多反复出现，且不抽象就只能 inline 重复？**

| 配套 | 反复出现的痛点 | 抽象后得到 |
|---|---|---|
| **Retry** | 网络不可靠 | transport 层独立处理 |
| **Middleware** | 横切逻辑要写 100 遍 | 可插拔、可重排的拦截链 |
| **AuthZ** | 安全规则的演进速度 ≠ 业务 | 声明式策略层 |
| **Observability** | 事后回查没痕迹 | 留指纹 |
| **Error Taxonomy** | 一个兜底吞信息 | 让 caller 能分别处理 |

这不是「RPC 框架长得都差不多所以照抄」——是**任何被人用的分布式系统都会撞到
这 5 个 surface**，最后用 5 类抽象解决。

---

## 3. LLM 调用 ↔ RPC 不是「像」，是同构

### 3.1 跨进程边界 + 不可信通道

- RPC：A 进程不在 B 进程里，调用要走网络。网络会丢包。
- LLM：tool 不在 LLM 推理上下文里，调用要走 dispatch loop。LLM 会给错参数。

**共同本质**：边界不可控。两边都不能假设「调用按预期发生」。

### 3.2 决策方和执行方分离

- RPC：决策方是 client 代码（"我想 GetUser"）；执行方是 server handler。
- LLM：决策方是 LLM 推理（"我想调 Bash"）；执行方是 tool handler。

**共同本质**：「思考」和「动作」是分开的两件事。这是**为什么需要 dispatch loop**
——把「思考产物」翻译成「执行动作」。普通函数调用没这一层，因为思考和执行在
同一个进程同一个 frame 里。

### 3.3 副作用要喂回决策方

- RPC：调用结果回 client，client 用结果决定下一步。
- LLM：tool result 回 LLM，LLM 用结果决定下一 turn。

**共同本质**：不是 request-response 一次性，是**调用-观察-再调用**的循环。
stateful interaction。

### 3.4 调用密度会爆炸 → 横切逻辑必须抽象

- RPC：production 每秒几千 QPS，每个调用都过权限/日志/错误处理。
- LLM：一个 `oh ask` 跑下来 20 个 tool 调用，每个都过权限/日志/错误处理。

**共同本质**：到了某个密度，「每个 handler 自己 inline 写」就撑不住——必须抽
到框架层。

→ 把「远程」的定义从「另一台机器」扩展到「另一个上下文边界」，
**LLM 推理就是另一种远程**。

---

## 4. LLM 是「奇怪 client」——比普通 RPC 多 4 个特殊性

### 4.1 client 是概率模型

- 普通 client：input X → **必然**产生调用 Y。
- LLM：input X → **可能** Y / Z / 不调用 / 调用 Y 但参数错。

→ 决策方不可信，dispatch 层必须**比普通 RPC server 更防御性**。Pydantic
`model_validate` 喂错 → ValidationError → 包成 is_error 喂回，让 LLM 自己改。
（这就是 retro §4.3 的 4 条 recovery 路径的本质。）

### 4.2 错误是 payload，不是 protocol

- 普通 RPC：tool 失败 → 抛异常 → client 处理。
- LLM：tool 失败 → 包进 ToolResult(is_error=True) → 喂回 LLM → **LLM 自己决定
  retry/fallback/放弃**。

→ **LLM 自己就是 retry policy**——这是普通 RPC 没有的节省，但意味着错误信息
要写得让概率模型读得懂（"file not found; try Grep" 这种 hint 不是给人看的）。

### 4.3 wire history 不丢

- 普通 RPC：包用完就扔，下次调用从零开始。
- LLM：每次调用沉淀到 `messages` list，下一轮 prompt 里**全程带着**。

→ wire history 是 stateful 的，**且有上下文窗口上限** —— 这就是为什么
**Phase 4 必须做 Compaction**。普通 RPC 完全没这个问题。

### 4.4 service catalog 是 push 不是 pull

- 普通 RPC：client 从 registry 查「哪些 service 可用」。
- LLM：每次请求把**整个 catalog 推给 client**（`tools=[...]`）。

→ tool description 不是给开发者读的注释——**是 prompt**。`Field(description=...)`
在 runtime 被概率模型读，措辞影响 LLM 选不选这个 tool。这是普通 RPC 没有的
奇怪职责：**schema 即 prompt**。

→ harness 不是 RPC 框架的翻版——是**为奇怪客户端定制的 RPC 框架**。

---

## 5. 「这一切都朝着工程的方向去了」——直觉放大

LLM 应用从 demo 到产品的整条演化路径：

```
Demo / POC：「能跑」就成功
   ↓ 加 retry / 加监控
Pilot / 内测：「跑得稳」才成功
   ↓ 加权限 / 加日志 / 加扩展点
Production：「可放心给别人用」才成功
   ↓ 加 hooks / SDK / 二次开发面
Platform / SaaS：「能让别人在上面再开发」才成功
```

**每一步都不是「模型变强了所以变好」——是工程抽象装齐了所以变好。**

- 模型能力解决「**会不会做**」
- 工程的成熟解决「**能不能放心做**」

---

## 6. 这个 frame 对 Phase 3 boundary 决策的指导

Phase 3 boundary 的本质，不是「技术选型」，是问：

> **客户/用户/未来自己拿着这个 harness 想做什么时，会撞到哪些工程门槛？**

| 决策（按 retro §10.1 优先级） | 用户会不会撞到？ |
|---|---|
| **Hooks 范围** ⭐⭐⭐ | 必然——只要他想插任何横切逻辑（log / cost / memory / 自定义安全规则）就撞 |
| **Permission 粒度** ⭐⭐⭐ | 必然——demo 里 deny-list 够，production 里需要声明式策略 |
| **`is_read_only` + parallel** ⭐⭐ | 必然——任何"读两个文件再总结"的场景都撞串行体感 |
| **Error 分类** ⭐⭐ | 必然——caller 想精准处理"权限拒绝"vs"hook 崩"vs"网络断" |
| **Retry hardening** ⭐⭐ | 中等——有 cost cap 之类的 application 层重试需求才撞 |
| **Observability** ⭐ | 必然——尤其是出问题之后想回查 |

每条都是「必然会」——这就是 **FDE 思维**：预见客户的工程门槛、**提前**在框架里
装好对应抽象。

不是抄 RPC 框架的形——是抄它**用了 30 年才学会的判断 framework**：
- 哪些痛点会反复出现？
- 哪些必须抽？
- 哪些可以 inline？

---

## 7. 从 framing 到实施——5 条统一处理原则

> 这套 framing（RPC 同构 + 奇怪 client + 5 件配套 + 朝工程方向）落到 Phase 3
> 实施时，沉淀成 5 条可复用的处理原则。它们既是 Phase 3 的 judge framework，
> 也是 Phase 4/5/6 的 inheritance。Phase 2 close-out 之后第一次自我 review 时
> 浮现，记下来。

### 7.1 原则 1：每件抽象 = 回答一个反复出现的痛点

不是「装齐为了像 production 系统」，是**每件抽象必须能回答"哪个反复痛点？"**。

| 反复痛点 | 抽象 |
|---|---|
| 横切逻辑要写 100 遍 | middleware (hook) |
| 安全规则演进 ≠ 业务演进 | AuthZ Tier |
| 事后回查没痕迹 | structlog observability |
| 一个兜底吞信息 | error taxonomy 5 类 |

→ 这是 §2.7 judge framework 的具象化。**所有未来的 capability 都要过这把尺**——
不能回答"哪个反复痛点"的抽象，就是过度设计。

### 7.2 原则 2：「production 化」= 把 implicit 信任假设全 explicit 出来

| Phase 1+2 implicit | Phase 3 explicit |
|---|---|
| "LLM 不会作恶" | AuthZ 三层 Tier |
| "用户在场监督" | hook 5 events |
| "出错我会自己看到" | structlog 接入 |
| "失败兜底就行" | error taxonomy 5 类 |

**这条原则比"加配套"更深**——它说清了「能跑 → 可放心」的差距到底是什么：
**把信任面从模糊变可控**。每一条 implicit 信任假设都是潜在的事故源；
explicit 出来意味着可被审视、可被插入、可被诊断。

### 7.3 原则 3：决策必须可 grep 回 framing

`D{N}.{M} → docstring → code` 的 trace 链：

- boundary doc 每个 D13.x 都有 framing §2.x 锚点
- 实施时每个 module docstring 引用 D13.x（如 `tools/base.py` 引用 D8.1-D8.7）
- 一年后重看代码，决策的 *why* 还在那

retro §8 已经验证过 Phase 2 的可行性（Phase 2 全程 35+ sub-decisions 都按这个
链路落地）。**这是 framework 的复利——不是 build-time 的事，是 maintain-time 的事**。

### 7.4 原则 4：「现在做 / 留接口 / 不做」必须显式分层

不只说做什么，**必须说不做什么 + 为什么**。具体例：

- **D13.5 cost cap 推 Phase 4** → 跟 Compaction 强耦合，一起设计避免双重重构
- **D13.6 ABC 三层留位** → 现在 structlog / Phase 4 EventLogger / Phase 5+ OTel
- **D13.3 拆 `is_read_only`（现在）/ parallel（Phase 6）** → 低成本立刻受益 vs 高成本不紧急

reversibility 显式分层让"未来怎么扩"的边界清晰，避免**当下不做的事变成未来的
silent 锁死**。

### 7.5 原则 5：抄 judge framework，不抄具体形

> **「不是抄 RPC 框架的形——是抄它用了 30 年才学会的判断 framework」**

——§6 一句话原话。**这条是元原则**：前 4 条不是 prescriptive checklist
（"按这个清单挨个打钩就对了"），是 **generative judgment**（"用这套思维方式
判断任何新场景该怎么办"）。

具体形会随技术演进——Express middleware vs gRPC interceptor vs LangGraph
conditional edges vs OpenHarness hook，每一代具体形都不一样。但**判断
framework 跨代不变**：哪些痛点反复出现 → 哪些必须抽 → 哪些可以 inline。

这就是为什么这套原则**能用在 Phase 4 / 5 / 6**——同一套 trace 链 + 同一套
judge framework。**框架构建者的复利就长在这里**。

---

## 8. Language as Substrate, Judgment as Substance — AI 协作时代的能力分水岭

> Phase 2 close-out 之后，用户自己浮现的认知（不是教出来的）：
>
> > 「这个项目都是用 Python 写的，然后其实我并不太熟悉，在今天语言真的不重要
> > 了，因为代码是你写，而我有自己擅长的语言，所以不懂的地方，我可以很快类比，
> > 语言是表层的东西，更深层次的东西是思考和需求。」
>
> 这是 §1-§7 工程实践走过之后**自然显形**的元层洞察，是 §7.5「抄 judge framework
> 不抄具体形」在能力面的对偶——不是单项目的判断 framework，是**跨语言、跨栈、
> 跨技术周期的迁移性资产**。

### 8.1 三层模型：从表层到元层

| 层级 | 性质 | 典型问题 | 跨语言迁移性 |
|---|---|---|---|
| **语言 / 语法** | 表层 substrate | "Python 怎么写 async generator?" | ❌ 锁单语言 |
| **思考** | 在某场景下想清楚 | "这个 hook 应该怎么设计?" | 部分 |
| **需求** | 客户/产品具体要什么 | "客户希望 X" | 部分 |
| ⭐ **判断 framework** | 跨场景的元思考 | "类似场景哪些必抽 / 哪些可 inline?" | ✅ 跨语言 / 跨栈 / 跨周期 |

§1-§7 全部论证集中在最顶层。这就是为什么 framing doc **不教 Python 语法**——
教的是 RPC 同构性 / 5 件配套 / 5 条统一原则。**这些是真正的迁移性资产**。

### 8.2 nuance：语言不无关，只是「在抽象层无关 / 在实施层相关」

实施层有 Python-specific 的 production hardening 要点（retro §9 6 节专门记）：

- `asyncio.to_thread(...)` 是文件 IO 唯一可移植解（Python 没"真异步文件 IO"）
- `Generic[InputT]` 解 LSP 违规（子类 narrow 参数类型）
- Pydantic `model_validate(dict)` 是 LLM JSON → typed args 的边界守门员
- `process.terminate() → wait → kill` 两阶段子进程终止
- `errors="replace"` 软失败 vs `try/except UnicodeDecodeError` 硬拒绝

但**学 Python 的方式**是「跨语言的设计 pattern 在 Python 里是什么 syntactic
shape」——先有原则、再 grok 语法。这比"先学完 Python 再想架构"**快一个量级**。

### 8.3 这条认知在 FDE 角色上是 load-bearing

[`/CLAUDE.md`](../../CLAUDE.md) 4 个项目跨栈分布：

| 项目 | 主语言 | 干的事 |
|---|---|---|
| 1. RAG | TypeScript / Next.js | 评测驱动 + 增量更新 + 权限管控 |
| 2. Social Media Agent | TypeScript / LangGraph | Workflow 编排 + HITL |
| 3+4. Test Agent | TypeScript / Claude Agent SDK | Skill + Safety Hook + Coverage MCP |
| **OpenHarness（本项目）** | **Python** | 把 RPC 框架的演化逻辑在「奇怪 client」上重走一遍 |

**4 个跨语言的项目，在元层是同一件事**——给 LLM 朴素调用装 production 配套
（§6 已 mapping 过）。

客户那边可能来 Java 后端 / Go 微服务 / Rust 工具链——FDE 的能力**不在于"会不会
立刻写它们"，在于"能不能识别这场景该用哪个 RPC 配套 + 什么 trade-off"**。

判断 framework 跨语言、跨技术栈、甚至跨技术周期（RPC 1980s → Workflow 2024 →
Agent 2024，三代抽象层都成立）。

### 8.4 能力分水岭

> **2024 之前**：能不能写代码 = 工程能力
>
> **2026 当下**：能不能判断「该写什么 / 为什么写 / 怎么验证」+ 能让 AI 写得对
> = 工程能力

具体含义：

- 「代码是 AI 写」——但 boundary 拍板、Three-Axis 讨论、code review 必须人主导
- **读代码能力 ≠ 写代码能力**——AI 时代后者门槛大幅降低，前者仍是 hard requirement
- 判断 framework 是 **持有（own）** 的能力，语法是 **借用（borrow）** 的能力
- 当能力轴从"会写"上移到"会判断"，**人对 AI 的杠杆比就从「替代」变「放大」**

### 8.5 一句话

> **Language as substrate, judgment as substance.**
>
> 语言是基底——任何 substance 都要落到某种基底上才能跑。但 substance 才是要**跨
> 周期持有的资产**。这是 framework 构建者在 AI 协作时代的能力轴心，也是 §1-§7
> 这套 framing 真正想沉淀给未来自己的核心。

---

## 9. Framework as Inversion — 业务代码和框架的元关系

> 2026-05-13 用户从第一性原理重新理解 hook / AOP 后浮现的元层洞察（不是教出来的，
> 是 §1-§8 走完之后**自发显形**的）：
>
> > 「框架确实是抽象，把通用的东西抽象出来，然后它必然要再一次接入业务逻辑。
> > 所以最后就是业务代码和框架一起编译成了一个可以运行的代码。在这个业务里面，
> > 你也是自然会想出来这样的 idea，**关注点分离**。**开发框架的，和用框架的
> > 天然就是两拨人。**」
>
> 这条洞察跟 §8 「language as substrate, judgment as substance」是配套——§8
> 讲跨**语言**的能力面，§9 讲跨**角色**的能力面。两者一起构成 framework
> 构建者的完整 self-awareness。

### 9.1 框架的本质 —「抽象通用 + 留位业务」

```
最朴素的代码:      业务逻辑 直接耦合 通用流程
                  → 重复 + 易漏 + 无法统一升级

抽象出框架:        通用流程 (框架) ←hook→ 业务逻辑 (业务代码)
                  → 框架可独立演进, 业务可独立替换
```

任何 framework 都在做同一件事——**把通用部分抽出，留出业务接入点**。具体例：

| Framework | 抽出的通用 | 留位业务接入 |
|---|---|---|
| Express | HTTP request 处理流程 | middleware / route handler |
| React | UI 渲染 + lifecycle | component / hook |
| Django | ORM + admin + url routing | view / model / signal |
| VSCode | 编辑器内核 + 扩展系统 | extension / plugin |
| LangGraph | LLM workflow 编排 | node / conditional edge |
| **你的 harness** | LLM dispatch loop + tool registry + permission | tool / hook / permission rule |

### 9.2 关注点分离 (Separation of Concerns) — 元设计原则

| 关注点 | 谁负责 | 演进速度 |
|---|---|---|
| **通用流程** | 框架开发者 | 慢（基础设施） |
| **业务逻辑** | 业务开发者 | 快（产品需求） |
| **两者的接口** | 框架定义，业务实现 | 中（API 契约稳定） |

→ **演进速度不同必须解耦**。如果两个混在一起，**改任何一个都要重新理解全部**——这就是没有框架时代的代码地狱。

这跟 framing §2.4 RPC AuthZ 演化故事里 "安全规则演进速度 ≠ 业务演进速度" **是同一个 framework**——演进速度不同的事情必须**分层**。Hook / framework 是这个原则的具象化。

### 9.3 IoC + Hook 是关注点分离的**实现机制**

关注点分离是**原则**，但需要**机制**：

```
没有 IoC:
业务代码 → 调用 → 框架 API
(业务代码持有控制权,框架是被动 library)

有 IoC (hook):
业务代码 注册 callback → 框架在关键时刻 → 调用业务代码
(框架持有控制权,业务代码被动等被调)
```

**Hook / Middleware / Plugin / AOP / Decorator** 都是**控制反转的不同 instantiation**——颗粒度 / 注册方式 / 调用语义不同，但本质都是 IoC。

具体到 harness 的 D13.1 hook：

```python
# 框架代码 (harness 内部)
async def _dispatch_one(tool_use):
    await hook_executor.invoke("PreToolUse", ...)  # ← framework emit
    result = await tool.execute(...)
    await hook_executor.invoke("PostToolUse", ...) # ← framework emit
    return result

# 业务代码 (harness 用户)
@register_hook("PreToolUse")
async def my_audit_log(ctx):                       # ← user provides callback
    log.info(f"tool {ctx.tool_name} called")
```

→ **框架决定何时调，业务决定调什么**。这就是 IoC 的核心。

### 9.4 编译/打包是整合手段

你说「**最后就是业务代码和框架一起编译成了一个可以运行的代码**」——抓到关键。

| 整合时机 | 例 | 含义 |
|---|---|---|
| **编译时整合** | AspectJ weaving / TS Decorator compilation | 编译器把 framework 和 business 织在一起 |
| **加载时整合** | Python import 时执行 `@register_hook` decorator | 运行时 register table 建好 |
| **运行时整合** | `framework.register_hook(...)` 显式调用 | 完全 dynamic |

→ **不管哪种时机，最终都是 framework 持有 callback table → 在关键时刻调 callback**。

这条洞察的工程含义：**framework 设计要决定整合时机**。早整合（编译时）= 性能好但灵活度低；晚整合（运行时）= 灵活但有 overhead。harness 选**运行时整合**（async `register_hook`）——优先灵活度，因为 LLM 应用场景需要 dynamic 加载 hook。

### 9.5「框架开发者 vs 业务开发者」天然是两拨人

这条用户洞察**比表面看的更深**——它揭示了一个**产业级 role specialization**：

| 角色 | 关心什么 | 数量 | 稀缺度 |
|---|---|---|---|
| **框架开发者** | 抽象、扩展点、API 稳定性、向下兼容 | 少 | ⭐⭐⭐ 稀缺 |
| **业务开发者** | 具体需求、产品 UX、上线时间 | 多 | ⭐ 普遍 |

具体生态例：

| Framework | 框架开发者 | 业务开发者 (数量级) |
|---|---|---|
| VSCode | Microsoft VSCode team (~50 人) | plugin 作者 (40,000+) |
| Express | TJ Holowaychuk + maintainers (~10 人) | Node web 开发者 (millions) |
| React | Meta + core (~30 人) | React 应用开发者 (millions) |
| LangGraph | LangChain team (~20 人) | LLM 应用开发者 (100,000+) |

→ **比例 1:1000 是常态**。框架开发者人数永远远少于业务开发者——但**框架开发者的设计决策影响所有业务开发者**。

### 9.6 你今天的角色翻转 — 从业务到框架

这是这条洞察对**你具体的含义**。

| 时期 | 你的角色 | 关心什么 |
|---|---|---|
| **FDE 4 个项目（RAG / SMA / Test Agent）** | 业务开发者 | 给客户交付具体方案 |
| **本 harness 项目** | **框架开发者** | 抽象 + 扩展点 + API 稳定性 |

→ 你**第一次站在 framework 开发者视角**做事。这是为什么之前不理解 hook ——业务开发者用框架，**很少需要理解框架为什么这样设计**。

**反过来这条认知翻转对 FDE 工作的 leverage**：

| FDE 场景 | 业务开发者视角 | **框架开发者视角** |
|---|---|---|
| 客户问"为什么 LangGraph 这样设计" | "我也不知道，反正它就这样" | "因为他们要解决 X 痛点，trade-off 是 Y" |
| 客户选型 LangGraph vs Claude Agent SDK | 比 feature list | **比 framework 设计哲学** + 谁的扩展点更匹配客户场景 |
| 客户问"框架不够用怎么办" | "想办法绕过去" | "看哪个扩展点该用，没有就给框架提 issue / PR" |

→ **FDE 的 T-shaped 知识结构有一个隐藏维度**——除了"应用层深、模型层宽"，还有"**框架层的元理解**"。这条认知翻转**让 FDE 跟客户对话的深度上一档**——不只是 "怎么用框架"，而是 "为什么这框架这样设计"。

### 9.7 跟 §7 / §8 的连接

| 元洞察 | 角度 |
|---|---|
| §7 5 条统一处理原则 | **方法论层**：判断 framework 的元原则 |
| §8 Language as substrate, judgment as substance | **能力轴**：跨**语言**的迁移性 |
| **§9 Framework as Inversion** | **角色轴**：跨**业务/框架角色**的迁移性 |

→ §7 / §8 / §9 三者构成 framework 构建者的**完整元认知**：
- **§7**：怎么做判断（generative judgment）
- **§8**：判断在什么上做（substrate vs substance）
- **§9**：判断由谁来做（framework dev vs business dev）

这是 framing doc 走到 2026-05-13 的**最完整收口**。

### 9.8 一句话

> **Framework 的本质 = 抽象通用流程 + 留位业务接入点，通过 IoC (hook) 实现关注点分离**。最后业务代码和框架代码**整合**（编译时 / 加载时 / 运行时）成一个可运行的整体。
>
> 框架开发者 vs 业务开发者**天然是两拨人**——演进速度不同、关注点不同、人数比例 1:1000。但**今天你站在了框架开发者那一边**——这是 FDE 角色的隐藏维度（除了 T-shaped 应用/模型轴，还有"框架元理解"轴）。
>
> 跟客户对话时，从"怎么用框架"上升到"为什么这框架这样设计"——**这一档差异就是 FDE 真正的 leverage 来源之一**。

---

## 一句话沉淀

> **Phase 1 把 chambers 装好，Phase 2 让 heart beat 起来，Phase 3 把这颗心装上
> production 配套——不是因为我们要抄 RPC 框架，而是因为「LLM 是奇怪客户端」这件
> 事让 RPC 30 年学到的判断 framework 在这里全部成立，且必须重学一遍。**
>
> 「这一切都朝着工程的方向去了」——这个直觉就是 FDE 三个项目（RAG / Workflow /
> Deep Agent）+ harness 一起回答的同一个问题：**把 LLM 朴素调用 → 装上 production
> 工程配套 → 客户能放心给别人用**。
>
> ⭐ **三条元洞察构成 framework 构建者的完整 self-awareness**：
> - **§7**：怎么做判断（generative judgment，不是 prescriptive checklist）
> - **§8**：判断在什么上做（language as substrate, judgment as substance）
> - **§9**：判断由谁来做（framework dev vs business dev 天然两拨人；你今天站到了
>   framework 那一边——这是 FDE 角色的隐藏维度，让你跟客户对话能从"怎么用框架"
>   升到"为什么这框架这样设计"）
