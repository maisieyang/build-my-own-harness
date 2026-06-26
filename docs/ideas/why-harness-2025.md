# Claude Code 不是凭空出现的：agent harness 流行的底层条件

> 写于 2026-04-27
> 这是我在 [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness) 项目期间的思考记录。

---

## 引子

2024 年起，一类新的 AI 产品集中冒了出来。它们都不是"AI 助手"那种聊天界面的衍生品，
而是带工具、有记忆、能调度子任务的"代理"：

- **2024 年先引爆**：Cognition 的 **Devin**（3 月，第一个"autonomous software
  engineer"，端到端返回 PR）、紧随其后的开源响应 **OpenHands**、**SWE-agent**、
  **Cline**
- **2025 年第一方跟上**：Anthropic 的 **Claude Code**、**Manus** 的全自主代理、
  OpenAI 的 **Codex**（CLI 开源，云端 agent 闭源）
- 一路还有 **Aider**、**Continue**、**OpenHarness** 等开源项目

有意思的是：这批产品冒出来后，**很长一段时间没有统一的名字**——大家各叫各的
（coding agent / autonomous engineer / scaffold）。直到 **2026 年初**，业界才
收敛出一个词来命名这一整类东西：**agent harness**——LLM 是大脑，harness 提供
手（工具）、眼（搜索/观察）、记忆（持久化）和安全边界（权限/沙箱）。


为什么是 2024-2025 这两年集中出现？为什么不是 2022 年 ChatGPT 火的时候，或者
2023 年 GPT-4 出来的时候？

我的判断是：这**不是某一个突破，而是 8 个力量同时跨过阈值**。本文逐一拆解.

---

## Layer 1：底层模型能力的三个拐点

要说 harness 能不能存在，**先看模型有没有这个底子**。三个拐点缺一不可。

### 1. 长上下文窗口：从 4K 到 200K，从结构不可能到结构可能

2022 年 GPT-3.5 是 4K context。假设一次工具调用平均吃 600 token（命令 + 输出 + 模型推理），
那一个会话最多塞 6 次工具调用就爆了——还没爬到任何复杂任务。

2024 年：

- Claude 3 Sonnet：**200K**
- GPT-4 Turbo：**128K**
- Gemini 1.5 Pro：**1M**（带 cache）

200K 让 100+ 次工具调用变成日常。一个 Claude Code 会话从 plan 到写 PR 经过
50-100 次 read/edit/grep/bash/test 调用，这在 4K 时代是物理上不可能的。

**没有长上下文，harness 不是"还没火"，而是结构上不存在。** 这是最底层的前提。


### 2. 工具调用从"能用"到"可靠"的复利质变

光有上下文还不够——模型得能**正确**调用工具。这里先说清：没有一个干净的公开基准能给出权威数字——"成功率"随定义剧烈变化（单次调用的**格式合法性**可到九成以上，而端到端**任务**成功率在难基准上连前沿模型都不到一半）。下面两个是**示意量级**，真正的论点不在精度，而在复利：

- 2023 GPT-4 早期：单步 tool calling ~70% 量级
- 2024 Claude 3.5 Sonnet：进到 95%+ 量级

哪怕只差这个量级，放到一个 50 步的任务上，**复利下来差距就是天文级**：

| 单步成功率 | 50 步全成功率 |
|---------|------------|
| 70% | 0.7^50 ≈ **0.0000018%**（等于不可用） |
| 95% | 0.95^50 ≈ **7.7%**（加局部重试就能用） |
| 99% | 0.99^50 ≈ **60.5%**（接近实用） |

这条曲线是**凸的**——99% 比 95% 好的程度，远大于 95% 比 90% 好的程度。
2024-2025 模型刚好挤进了"50 步任务可用"的阈值。


### 3. METR 任务长度曲线：把"长任务"机器化

METR 2025 年初的 benchmark 显示：**AI 能完成的任务长度（以人类专家所需时间衡量）
每 7 个月翻倍**。

- 2024 年中：模型能稳定完成 **~30 分钟** 的人类任务
- 2025 年中：**几小时**
- 这条曲线如果延续，2026 中：**一个工作日**

harness 的本质就是**把长任务机器化**。如果模型只能稳定做 30 秒的任务，
你不需要工具循环、计划、子代理这一套——直接 chat completion 就够。

正是因为模型能稳定做"长任务"了，harness 才有意义。

---

## Layer 2：经济阈值

技术准备好了，下一个问题：**跑得起吗？**

### 4. 单 token 成本 10x 下降，跨过"日常使用"阈值

2023 年 GPT-4 是 $30/M input, $60/M output。一个 100 步 harness 任务
（假设平均每步 5K input + 1K output token）：

```
100 步 × (5K × $30/1M + 1K × $60/1M) = 100 × $0.21 = $21/次
```

每次 $21——**这是奢侈品**。VC 烧得起，普通开发者跑不起。

2024-2025：

- Claude 3.5 Haiku：$0.80/M input, $4/M output
- Claude 3.5 Sonnet：$3/M input, $15/M output
- GPT-4o-mini：$0.15/M input, $0.60/M output

同一个 100 步任务（同上假设），用 Sonnet 约 $3，用 Haiku 约 $0.80。
**从奢侈品到日用品**——开发者敢跑几十次试错，公司 CI 里每个 PR 跑一次也烧得起。

这个经济阈值跨过去之后，**技术上能做的事，市场上才真正能用**。
这不是技术进步，是商业拐点。

---

## Layer 3：生态扣扳机

底层能力 + 经济条件都到位之后，需要"扣动扳机"才能让范畴爆发。
这一层是**最具决定性的瞬间**——前两层准备好了，下面这两个力量哪天发生不重要，
但发生了，整个范畴就站住了。

### 5. 第一方厂商下场：Claude Code 和 Codex 合法化范畴

2023 年的 agent 实验属于草根 vs 学院：LangChain、AutoGPT、BabyAGI、
各种博士 demo。它们都被打上 **"Toy"** 的标签——可以玩，不能托付真活。

2024-2025：

- 2025 Q1：Anthropic 推出 **Claude Code**（2 月研究预览，5 月 GA）
- 2025：OpenAI 推出 **Codex**（CLI 开源 + 云端 agent）
- 各家紧跟上线官方 IDE 集成

第一方下场的意义有三个：

1. **设定参考标准**——大家不再吵"agent 框架该怎么做"，而是看 Claude Code 怎么做
2. **合法化范畴**——不再是"我自己折腾的玩具"，而是"OpenAI 都在做的事"
3. **挤出空间**——LangChain 等"做框架"的位置被压缩，新创业公司转向"做应用"

这一脚下去，**harness 从科研话题变成商品**。

### 6. MCP：agent 工具生态的 USB-C 时刻？

Anthropic 在 2024 年 11 月发布了 **MCP（Model Context Protocol）**——一个让
agent 和工具之间解耦的开放协议。

类比硬件的 USB-C：在它出现之前，每个 agent 框架要么自己造工具集，要么写 N
个 adapter 接其他框架。MCP 之后，工具厂商写一个 server，所有 MCP 兼容的
harness 都能用。

到 2026 年初，公开 MCP server 已达**上万个**（2025 年底 Anthropic 称
>10,000；GitHub、Slack、Notion、各种数据库）。Claude Code、Cline、Continue
都原生支持，OpenAI 也在 2025 年采纳了 MCP——它没有分裂成竞争协议，反而收敛成
事实标准。

---

## Layer 4：后置叙事 vs 真正原因

最后两个力，我想**单独拎出来**——因为它们更像"harness 流行**之后**才被合理化
的叙事"，而不是"促成流行的原因"。

### 7. 用户心智从"对话"切到"委托"

故事是这样讲的：

> 2022-2023 年 ChatGPT 让 AI = 聊天框。2024 年开发者喊累——"我不要聊天，
> 我要它直接干活"。harness 提供"agent 替我做完一件事"的产品形态，正中需求。

听起来很顺，但反过来想：**如果 harness 不能用（前 6 个力量没准备好），
用户会"想要委托"吗？**

不会。用户的心智永远跟着**能用的产品**走。Cursor 上线之前，你不会喊"我要一个
理解整个 codebase 的编辑器"——因为这种产品根本不存在，你不会幻想它。

所以"用户心智切换"**是结果不是原因**。它真实发生了，但因果反过来。

### 8. 模型 API 商品化 → harness 作为价值层

故事是：

> DeepSeek、Qwen、Llama 让 chat completion 接近免费，价值从"模型 API"
> 转移到"应用层调度"。harness 作为应用层核心抽象，承接价值。
> 类比 CPU 不值钱了，操作系统才是真正的价值。

这个类比很有诱惑力，但同样要小心：**价值层的转移，是商业上的资产负债表逻辑，
不是"用户为什么需要 harness"的逻辑**。

如果只剩 OpenAI 一家（model API 没有商品化），harness 一样会流行——因为
能力到位了，需求摆在那里。

---

> 区分"原因"和"合理化"很重要，因为这影响**预测下一步**。如果你以为 harness
> 流行是因为"用户想委托"，那你会去做更多对话式产品；但如果你看清楚是
> 底层能力 + 经济条件先到，你会去关注**下一个能力跃迁**会让什么变可能。

---

## 结论

8 个力共振——其中 3 个**底层模型能力（#1 #2 #3）**+ 1 个**经济阈值（#4）**
+ 2 个**生态扣扳机（#5 #6）**是真正促成 harness 流行的原因；最后 2 个（#7 #8）
是流行**之后**被讲成的叙事。

未来 12-18 个月，几个值得盯住的：

- **METR 曲线会延续吗**：这是最大的不确定。如果 agent 能力增长放缓到一年
  翻倍而不是七个月翻倍，整个 harness 范畴的天花板会被锁住。
- **第二代 harness**：第一代 harness（Claude Code 风格）是 stop_reason
  驱动的工具循环；第二代会不会出现完全不同的范式（如基于 RL 训练的端到端
  agent，让"循环"这层抽象消失）。

---

## 后记（2026-06）：第二代 harness 的形状，初见

写完一个多月后，上面"第二代 harness"那条预测开始有了答案——**但带一个我没料到的反转。**

2026 年 6 月，业界冒出一个新词：**loop engineering（回路工程）**。演化链又走了一站：
`prompt → context → harness → loop`。说得最直白的是 Claude Code 的作者 Boris Cherny：
*"I don't prompt Claude anymore. I have loops running that prompt Claude and figuring out
what to do. My job is to write loops."*（我不再 prompt Claude 了——我跑的是一套自己去
prompt Claude、自己决定下一步做什么的 loop，我的工作是写 loop。）

主张是：当模型能自主跑几分钟到几小时、还能从自己的错误里恢复，最高杠杆的活就不再是
"打磨一句 prompt"，而是设计那个**自己决定 prompt 什么、何时 prompt、结果合不合格**的
回路——trigger → goal → actions → verify → 重复到目标达成。prompt engineering 没死，
只是降成了 table stakes；loop 成了新的上层。

**反转在这里：我当时猜"循环这层抽象会消失"——现实是它没消失，反而从隐式的工程细节，
浮成了人类亲手设计的头号对象。** 第一代 harness 里，`while stop_reason == "tool_use"`
是写在框架内部、你基本不碰的一行；第二代里，这行循环本身成了要反复打磨的产品。循环
没有沉进模型，而是升到了台面。

而 harness 这层并没有像"过时论"说的那样被淘汰。Addy Osmani 那篇《Agent Harness
Engineering》里有句话很准：**"Agent = Model + Harness。你不是模型，你就是 harness。"**
同一个模型装在 Claude Code 里和装在定制 harness 里，Terminal Bench 2.0 分数差一大截——
光换 harness 就能把一个 coding agent 从 Top 30 抬到 Top 5。harness 没有过时，它变成了
地基；loop 是盖在地基上的新楼。这反过来印证了本文 Layer 3：harness 是承载这一切的
substrate。

**什么把这一切推过阈值？还是模型。** 2026 年 5 月 Opus 4.8、6 月 Anthropic 在 Opus 之上
又出了 **Fable 5 / Mythos 5**——能跑整夜自主 coding run、单次请求常以分钟计。这正是
本文 §3 那条 METR 曲线推到了"一个工作日以上"的样子。一个值得记下的脚注：Fable 5 在
发布三天后的 **6 月 12 日被美国政府以出口管制指令叫停**——理由是一个"越狱"，而按
Anthropic 的说法，那个所谓越狱差不多就是"让模型读一个代码库、修掉里面的软件缺陷"。
换句话说，被当成国家安全风险点名的，恰恰是一个 coding harness 本来就在干的事。截至本文
更新的 6 月 23 日，两个模型仍下线（claude-fable-5 的 API 仍返回错误）——Anthropic 高管
称有信心"未来几天"恢复，但尚未发生。

> 来源：[Loop engineering（explainx）](https://explainx.ai/blog/what-is-loop-engineering-ai-agents-2026)
> · [Boris Cherny / 角色之变（Medium）](https://medium.com/@vovance/loop-engineering-the-skill-thats-replacing-prompting-d429b000489c)
> · [Agent Harness Engineering（Addy Osmani）](https://addyosmani.com/blog/agent-harness-engineering/)
> · [Fable/Mythos 停用声明（Anthropic）](https://www.anthropic.com/news/fable-mythos-access)
> · [CNBC 报道](https://www.cnbc.com/2026/06/12/anthropic-disables-access-to-fable-5-and-mythos-5-to-comply-with-government-directive.html)

— 2026-06 补记
