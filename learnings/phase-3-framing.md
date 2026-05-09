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

## 一句话沉淀

> **Phase 1 把 chambers 装好，Phase 2 让 heart beat 起来，Phase 3 把这颗心装上
> production 配套——不是因为我们要抄 RPC 框架，而是因为「LLM 是奇怪客户端」这件
> 事让 RPC 30 年学到的判断 framework 在这里全部成立，且必须重学一遍。**
>
> 「这一切都朝着工程的方向去了」——这个直觉就是 FDE 三个项目（RAG / Workflow /
> Deep Agent）+ harness 一起回答的同一个问题：**把 LLM 朴素调用 → 装上 production
> 工程配套 → 客户能放心给别人用**。
