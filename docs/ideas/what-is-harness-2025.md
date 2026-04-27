# LangGraph、Manus、Claude Code 不是三种东西：harness 是同一个抽象的三种形状

> 写于 2026-04-27 · 中文版 · 系列第二篇（第一篇是
> [《Claude Code 不是凭空出现的：agent harness 流行的底层条件》](./why-harness-2025.md)）
>
> 这是我在 [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness)
> 项目期间的思考。上一篇讲"为什么是 2024-2025"——外部条件；这一篇讲
> "什么是 harness"——内部抽象。

---

## 引子

我做过 LangGraph 的工作流编排，知道 Manus 怎么用文件系统做长任务，现在正用
Python 从零写一个 Claude Code 风格的 harness。过去三年里，我把这三件事看成
"Agentic AI 的三个流派"——好像它们是不同物种的研究对象。

直到最近，我意识到一件事：

**它们是同一种动物，只是穿着不同的衣服。**

LangGraph 不是"workflow 流派"，Manus 不是"多智能体流派"，Claude Code 不是
"工具循环流派"——它们都在回答**同一组工程问题**，只是各自给出了不同的回答。
而这个共同的回答空间，就是 **harness**。

这篇文章是这个统一视角的拆解。如果你和过去的我一样，把它们当成不同的东西在
学、在选型、在比较，这篇可能会改变你看代码的方式。

---

## 真正的问题是什么

把 frameworks 暂时放下，看 **LLM 单独存在时的三个根本缺陷**：

**1. 它不能做事。** GPT-4 / Claude 收到 prompt 后只能输出 token——它不能去
读文件、不能 grep 代码库、不能调 API、不能执行命令。它是个**纯函数**：输入
文本，输出文本。

**2. 它不能记忆。** Context window 之外的一切都是黑洞。你跟它聊到第 100K
token，它彻底忘了第 1 token 说过什么。它没有"上次会话"的概念，也没有"上周
的工作进展"的概念。

**3. 它不能感知环境。** 除了 prompt 直接给它的字符串，它对世界一无所知。
文件系统是什么样、当前 git 分支在哪、cron job 跑没跑——它都不知道。

而真实任务里，**做事、记忆、感知**就是全部。"我帮你修这个 bug" = 做事；
"上次我们聊到一半的方案" = 记忆；"现在 CI 红了吗" = 感知。

Harness 就是把这三个能力**装到 LLM 这个大脑外面**：

- 给它**手**——一组工具，让它能 read / write / exec
- 给它**记忆**——某种持久化机制，跨 turn、跨会话保留状态
- 给它**眼睛**——把环境（文件 / 进程 / 网络）暴露成它能消费的格式

加上一个**安全边界**（权限/沙箱/审计），保证它不会把你的 home 目录
`rm -rf`。

**这就是 harness 的定义——不依赖任何具体实现。**

---

## 三条路其实在回答同一组问题

把 LangGraph、Manus、Claude Code 三条路并列着看，它们都在回答 4 个问题。

### 问题 1：怎么让 AI 行动？（动作的表达）

- **LangGraph**：图里的节点本身就是动作。你定义一个节点叫 `search_db`，
  graph 走到这个节点就会执行
- **Manus**：派 worker。Planner 决定"需要做 search"，spawn 一个 search
  worker，给它工具集
- **Claude Code**：JSON schema 注册到 `ToolRegistry`。LLM 自己输出
  `{"type": "tool_use", "name": "Read", "input": {...}}`，harness dispatch

### 问题 2：怎么让 AI 记忆？（context 管理）

- **LangGraph**：`State` 对象跨节点传递。你显式定义
  `State(messages, user_info, retrieved_docs, ...)`，每个节点读写
- **Manus**：文件系统就是主存。LLM 的 messages 保持精简，"知识"全写进文件，
  下一步用文件名 reference
- **Claude Code**：messages 列表 + 三级压缩。短期保留完整 messages，超过阈
  值用 microcompact 砍工具输出，再超过用 LLM 摘要

### 问题 3：怎么让 AI 感知环境？（observation）

- **LangGraph**：通过 State 把环境信息流转给下个节点（节点之间显式传递）
- **Manus**：让 worker 主动读文件、调命令、看输出——文件系统就是世界
- **Claude Code**：工具结果被送回模型，模型从 tool_result 里"看到"世界

### 问题 4：谁来控制流转？（who drives the loop）

- **LangGraph**：**代码控制**——图的边定义"走完节点 A 之后去节点 B"，确定
  性最强
- **Manus**：**Planner 控制**——一个高层 LLM 决定派谁干什么
- **Claude Code**：**LLM 自驱**——`while stop_reason == "tool_use"` 循环，
  LLM 自己决定是否继续

### 把这 4 个回答收成表格

| 问题 | LangGraph | Manus | Claude Code |
|------|-----------|-------|-------------|
| **行动** | 节点 | Worker | Tool registry |
| **记忆** | State 对象 | 文件系统 | messages + 压缩 |
| **感知** | State 流转 | 文件系统 | tool_result |
| **流转** | 图（代码） | Planner LLM | LLM 自驱 |

这四列**形状完全不同**，但**列与列之间不平行——它们在并行回答同一组问题**。

注意一件事：你**不能**拿这张表做"哪种更好"的判断——这是技术决策的多目标优化：

- 流转想要**确定性** → LangGraph
- 任务**超长、跨会话** → Manus 的文件系统主存
- 模型**够强、任务开放** → Claude Code 的 LLM 自驱

它们是**同一空间里的不同点**，不是不同空间。

---

## 这个统一视角带来的 3 个实战收益

### 收益 1：选型不再是"流派之争"，而是"问题特征匹配"

过去你可能听过这种争论："LangChain 那种 framework 已经过时了，应该用
Claude Code 风格的 agent 自驱"——这种话有问题，因为它把**实现风格**当成
**代表性优势**。

正确的问法是：**你的任务长什么样？**

- SOP 明确 + 步骤可枚举 → LangGraph 类的图就是最优解
- SOP 模糊 + 任务开放 → Tool-Loop 是最优解
- 任务横跨小时/天 → 必须考虑 Manus 的文件系统主存

每条路都在某个区域是最优。**说"X 比 Y 好"的人，要么没真做过其中一个，
要么没看清楚问题域。**

### 收益 2：学习不再是"学 N 个框架"，而是"学 harness 词汇表"

如果你把 LangGraph 当**框架**学，你会记住"graph / node / edge / State"——
这些是 LangGraph 的术语。

如果你把它当 **harness 实现**学，你会记住"它的 control flow 是图，记忆是
State 对象，感知靠 State 流转"——这些是 harness 的**通用词汇**。同样的词汇
能套到 Manus 和 Claude Code 上。

学第三个框架的时间会从"几周"压缩到"几小时"——因为你已经知道**它在回答哪
几个问题**，只剩"它的具体回答是什么"要查。

### 收益 3：预测——未来抽象会进一步统一

如果上面这个 mental model 是对的，那有一个具有 prediction value 的判断是：

**未来 12-24 个月，这几条路之间会互相借鉴。** 具体可能看到：

- **LangGraph** 加入"Tool-Loop 节点"——一个节点本身就是 stop_reason 驱动
  的循环
- **Claude Code** 加入"显式 State"——不再只用 messages 列表，给 cost /
  permissions / tool_metadata 一个独立 dataclass
- **Manus 风格的"文件系统主存"** 会成为通用 pattern——任何长任务 harness
  都需要

这些都是已经有早期信号的趋势。如果你**早**有统一视角，看到这些信号你不会
觉得"哎，框架越来越像"——你会知道**它们本来就是同一物种，向中间收敛是必然**。

---

## 把这个 lens 用到我自己的 build 上

我现在用 Python 从零写
[build-my-own-harness](https://github.com/maisieyang/build-my-own-harness)。
**我选了 Path B**（Tool-Loop / Claude Code 风格）。

但有了这个统一视角，我**清楚自己在借鉴什么、放弃什么**：

- 我从 **LangGraph** 借"显式 State"的思路：除了 messages 列表，会给
  permissions / tool_metadata / cost_tracker 一个独立 dataclass，便于
  快照/回放/调试
- 我从 **Manus** 借"文件系统主存"的思路：上下文压缩不只做 LLM 摘要，还要
  支持"卸载到文件，按需读"
- 我对 **Claude Code** 风格的 `stop_reason` 循环忠诚——因为我的目标场景
  （个人开发任务）是"模型够强、任务开放、单会话内能做完"，这正好是 Path B
  的舒适区

如果一年后，我的 harness 想拓展到"团队级、跨会话、长周期任务"，我会**显式
从 Tool-Loop 演化到 Hybrid**（外层 workflow + 内层 agent）——而不是"重写一
个新 framework"。

这就是 harness lens 的力量：**它让你看清楚你在哪、可以往哪走、为什么往那走。**

---

如果你也在做 agent 相关的工作，希望这篇文章能让你**重新看你已经熟悉的
LangGraph、Manus、Claude Code**——它们不是三种东西，是同一个抽象的三种形状。

---

## 系列其它文章

- 第一篇：[《Claude Code 不是凭空出现的：agent harness 流行的底层条件》](./why-harness-2025.md)
  ——讲外部条件（为什么是 2024-2025）
- 这是第二篇——讲内部抽象（什么是 harness）

英文版准备中。
