# 为什么 harness 要标准化和 LLM 的交互协议：trade-off 思考

> 写于 2026-05-06 · 中文版
>
> 配套读物：
>
> - 决策记录：[decisions/02-protocols.md](../../decisions/02-protocols.md)（实现层）
> - 模块复盘：[learnings/02-protocols.md](../../learnings/02-protocols.md)（"框架视角的内化"）
> - 策略基础：[docs/ideas/why-harness-2025.md](./why-harness-2025.md)
>
> 这篇不是讨论"我们的协议怎么实现的"（那是 decisions/02），
> 而是讨论 **"为什么 harness 必须有一层协议，以及为这一层付的代价"**——
> 站在企业落地视角的 trade-off 思考。

---

## 1. 问题：harness 面对的是一个分裂的 LLM 市场

今天的 LLM API 实质上分两派：

- **OpenAI 系**：OpenAI 自家、Qwen（DashScope）、DeepSeek、Moonshot、Together、
  绝大多数中国云厂商、绝大多数开源 inference server（vLLM / TGI / Ollama）
- **Anthropic 系**：Claude（自家 SDK）、Bedrock 上 Claude、Vertex 上 Claude

两派的 wire format 在最基础的字段上就分叉：

| 维度 | OpenAI 系 | Anthropic 系 |
|------|-----------|-------------|
| 消息字段 | `messages: [{role, content: str \| list}]` | `messages: [{role, content: list[Block]}]` |
| 系统提示 | `messages[0].role = "system"` | 顶层 `system: str` 字段 |
| 工具调用 | `assistant.tool_calls: [{id, function}]` | `content: [{type:"tool_use", id, name, input}]` |
| 工具结果 | `role: "tool"` 消息 | `content: [{type:"tool_result", tool_use_id, content}]` |
| 流式事件 | `data: {...}\n\n`（增量 delta） | 多种 event 类型（`message_start` / `content_block_delta` / ...） |
| 错误模型 | HTTP 状态码 + JSON body | HTTP 状态码 + 不同的 error JSON shape |

**没有 harness 的代码**直接把 SDK 调用嵌在业务逻辑里——一旦想换 Provider，
所有上层代码都要改。**有 harness 的代码**把 SDK 隔在一层翻译之后——上层只见
harness 自己的协议。

---

## 2. HTTP 类比：为什么"协议层"是正确的心智模型

我对协议的第一个直觉来自 HTTP——这个类比意外地准。

| HTTP 世界 | LLM harness 世界 |
|----------|----------------|
| HTTP 协议规范了请求/响应格式 | harness 协议规范了 message / tool / stream event 格式 |
| 浏览器只认 HTTP，不关心后端是 Nginx 还是 Apache | 上层模块（engine/CLI/UI）只认 harness 协议，不关心 Provider 是 Qwen 还是 Claude |
| 换 web server 不用改前端代码 | 换 LLM 不用改 engine/CLI 代码 |
| HTTPS 是 HTTP 之上加的隔离层（TLS） | tool use / 多模态 是协议之上的扩展层 |
| `Host:` header 选具体后端 | `OPENHARNESS_BASE_URL` 选具体 Provider |
| 协议稳定，实现可换 | 协议稳定，实现可换 |

最关键的一条："**约定优先于配置**"（convention over configuration）。
有协议的世界，集成两个新东西不需要重新约定一遍——只要双方都说同一种话。

---

## 3. 标准化的收益（为什么值得做）

### 3.1 模型切换零代码摩擦

- `OPENHARNESS_BASE_URL=...` 从 DashScope 改成 OpenAI 自家 endpoint——上层零改动
- `OPENHARNESS_MODEL=qwen-plus` 改成 `qwen-max`——上层零改动
- 同一个 `oh ask "hi"` 命令，背后跑 5 种不同模型，CLI 代码不动一行

### 3.2 失败模式同构

- 协议层和配置层共用同一个验证引擎（pydantic v2）
- `ValidationError` 是唯一的"输入坏了"异常
- API 层的错误也走同一个 `OpenHarnessApiError` 层级
- **错误统一面是 harness 长期维护性的关键**——上层只需要一种 catch 模式

### 3.3 上层模块免疫 wire 变化

- engine 不依赖 OpenAI SDK 的字段名
- CLI 不依赖 Anthropic SDK 的事件类型
- 压缩 / 持久化 / 观测各模块都消费同一组 harness 类型

这意味着：当 OpenAI 明天改了 `tool_calls` 的 schema（已经发生过两次），
**只有 wire 翻译那一层要改**。其他所有代码不动。

### 3.4 按场景按成本选模型

企业落地最大的省钱杠杆。同一个 harness 里：

- 简单分类任务 → `qwen-turbo`（便宜）
- 复杂推理 → `qwen-max` 或 `claude-opus`（贵但准）
- 工具调用密集 → 选某个特别擅长 function calling 的模型
- 中文长文本 → 选国产模型（更便宜的 token 价 + 中文优化）

**没有协议层，模型混用的代价是上层多套 if-else 分发——很快变屎山。**
有协议层，路由发生在一层薄薄的"按场景选 base_url + model"逻辑里。

### 3.5 企业最大的成本不是开发，是迁移

LLM 市场未来 2 年会继续洗牌——价格下降、新玩家（DeepSeek 类型）、
监管要求换模型（数据留中国）、合作伙伴换平台。

**没有 ACL 的代码到第二个 Provider 时，重写成本通常超过新写一遍。**
协议层是把这个迁移成本摊薄成"加一个 wire 翻译适配器"的工程纪律。

---

## 4. 标准化的代价（trade-off 必须诚实）

### 4.1 多了一层翻译代码要维护

- `to_openai_request()` / `_StreamAssembler` 这类函数本身有 bug 风险
- 调试时多一跳：harness 协议 → wire 翻译 → SDK 调用 → 真实网络
- 翻译层是**新增的失败点**，不是免费的

**回应**：翻译代码是纯函数（输入协议对象，输出 dict），可以单元测试到 100%
覆盖。比业务逻辑容易守。`decisions/04-api-client-implementation.md` 把这一层
显式分离正是为此。

### 4.2 协议演进的双向维护负担

如果未来加一个新 capability（比如 tool use），协议层要同时支持：

- OpenAI 的 `tool_calls`（assistant message 上的字段）
- Anthropic 的 `tool_use` content block（content list 里的 block）

两种 wire 翻译都要写，两边都要测——**协议层不是免费的"通用"**。

**回应**：这是真实成本，但它**取代**了"每加一个 Provider 改所有上层"的成本。
比例上仍然划算——加一种 capability 是 O(N_providers)，但加一个 Provider 是 O(1) 翻译层。

### 4.3 "最低公约数"风险

如果某 Provider 有独特能力（OpenAI 的 `logprobs`、Anthropic 的 prompt caching、
Gemini 的多模态深度）——协议层如果做太"中性"，会**丢掉这些能力**。

**回应**：分两路应对：

1. **协议层留扩展点**：`metadata: dict[str, Any]` 字段做 escape hatch，特殊
   能力可以走非标准路径
2. **承认协议层不是 union**：某些能力就是不通用，让需要它的上层显式选 Provider，
   不强求协议层吸收

这就是为什么协议层的目标是"**80% 通用 + 20% 显式 escape**"，不是"100% 抽象"。

### 4.4 SDK 升级的好处不是自动到手的

- OpenAI SDK 出了新 streaming 改进 → 我们得手动同步翻译层
- Anthropic SDK 加了新 content block 类型 → 我们得在协议层评估要不要支持

**回应**：这是慢一拍，但**慢一拍是好事**——我们决定要不要采用，而不是被动接受。
对企业 harness，可控的演进节奏 > 跟随上游每个改动。

### 4.5 抽象本身有理解成本

新人看代码：先学 harness 协议，再看 wire 翻译，再到 Provider SDK——三层。
直接 SDK 调用的代码只有一层。

**回应**：单 Provider 时这是真成本。但 harness 的存在前提就是"会有第二个
Provider"——抽象成本在第一个 Provider 之前已经认了。**愿意接受这个成本，
就是愿意做 harness；不愿意接受，做单 Provider 集成就够了**。

---

## 5. 这套思考会在哪里被压力测试

### 5.1 第一次：tool use（Phase 2）

OpenAI / Anthropic 的工具调用 wire 差异比 message 还大：

- OpenAI：tool 调用是 `assistant.tool_calls[]`（message 上的字段）；
  tool 结果是 `role: "tool"` 的独立 message
- Anthropic：tool 调用是 `content` 里的 `tool_use` block；
  tool 结果是 `content` 里的 `tool_result` block

我们今天的 `protocols/` 已经选了 Anthropic 的 content-block 模型（
`ToolUseBlock` / `ToolResultBlock` 都已定义）。**这是一个 bet**——bet
content-block 模型更通用、更容易把 OpenAI 的 flat 结构翻译进来；反过来
（从 flat 抽取 block）会更难。如果错了，写
`decisions/<NN>-protocol-revision.md` 解释。

### 5.2 第二次：多模态（Phase 3+）

image / audio / video，三家（OpenAI / Anthropic / Gemini）的 wire shape
差异远大于文本。`ImageBlock` 已经预留口子，但具体怎么承载 audio / video，
今天没决定。**这是协议层的下一个真正考验**。

### 5.3 第三次：流式事件粒度

我们今天把 stream events 收敛成 3 类（TextDelta / MessageComplete / Retry）——
这是个**粗粒度**选择。如果未来上层需要"工具调用的增量进度"或者"reasoning
trace 的实时输出"，这个粒度会不够。`learnings/02-protocols.md` 已经标了
revisit trigger。

---

## 6. 一句话总结

**协议层不是为了"优雅"，是为了"未来还能改"。**

LLM 市场两年内会继续洗牌。今天写的代码，明年很可能要换 Provider、换模型、
换部署形态。协议层是把"那时候要改多少代码"压缩到一个可控的常数——
这是企业 harness 区别于 demo 代码的核心工程承诺。

代价是：多一层翻译要维护、抽象有理解成本、不能自动享受 SDK 升级。
这些代价在第一个 Provider 时全是负担、零收益——**愿意先付这个负债，是
做 harness 的入场券**。

---

## 7. 这一层和"harness 是 stateful 的"如何衔接

最后一个跨层观察：

- LLM API 本身是 **stateless 函数调用**——给一组 messages，返回一组事件
- harness 的存在意义之一是把这些 stateless 调用**编织成 stateful 会话**
- 协议层定义的是"**一次调用**的输入/输出形状"
- 编织（loop / context / memory）发生在协议层**之上**

这意味着：

- LLM 不会自己"loop"——是 harness 在 loop，每次重新组装 messages 喂回去
- context / memory 必须属于 harness 层——LLM 根本没地方放
- 协议层稳定 = harness 编织逻辑可以独立演进，不被 wire 变化拖累

**协议是 harness 内核的"一进一出"原语；loop 和 memory 是用这个原语编出来的更高层结构。**
两者是分层关系，不是平级关系——这也是 Phase 1 先做协议、Phase 2 再做 loop 的根本原因。
