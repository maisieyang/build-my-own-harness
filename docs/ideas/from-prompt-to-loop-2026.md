# 从"我在打字"到"loop 在跑"：我用自己的 harness，撞明白了 loop engineering

> 写于 2026-06 · loop engineering 认知记录。
>
> 背景读物：
> [《Claude Code 不是凭空出现的》](./why-harness-2025.md)讲 agent harness
> 走到 2025-2026 的外部条件；本文接着问一个更贴近使用体验的问题：
> 为什么同样在用 Claude Code，有人在“跑 loop”，而我还在一句句交互？
>
> 这一篇不讲"loop engineering 是什么"——那有现成的好文章。这一篇是一份**认知记录**：
> 我一个正在亲手 build harness 的人，怎么拿自己每天的真实体验，去撞这波 2026 年 6 月
> 突然刷屏的"loop engineering"热点，撞出了"我到底卡在哪"。

---

## 一个让我别扭的事实

业界突然在传一句话——Claude Code 的作者 Boris Cherny 说：

> *"I don't prompt Claude anymore. I have loops that are running. They're the ones that
> are prompting Claude and figuring out what to do."*（我不再 prompt Claude 了，我跑的是
> loop，是 loop 在 prompt Claude、决定下一步做什么。）

我第一反应是别扭，因为有几件事对不上：

- **我们用的是同一个产品。** 他用 Claude Code，我也用 Claude Code。
- **我用过那个最新、最强的模型（Fable 5）。** 可我用它的时候，**还是交互式的**——打一句、盯着看、再打一句。
- **他说他写 task、写边界、写验收标准——这不就是我过去写 skill 的样子吗?** 他也还是在终端里打字。

那他凭什么"不 prompt 了"，我就还在 prompt?同一个产品、同一档模型、同样在写 task，
差别到底在哪?这篇就是我把这个别扭一层层捅破的过程。

---

## 第一站：三篇文章给了我什么，又没回答我什么

我先读了这波热点的三篇代表作：

- **explainx《What Is Loop Engineering?》** —— **框架文**。给了一句利落的定义（"设计那个
  替你 prompt agent 的系统，而不是自己一句句敲"）和五个零件：trigger / goal / actions /
  verification / memory。最该记的一句：*"Prompt engineering isn't dead—it's table
  stakes. Loop engineering is the next layer."*
- **Vovance《The Skill That's Replacing Prompting》** —— **情绪文**。卖的是身份之变：
  *"You're not the agent. You're not the prompt writer. You're the architect of the
  system that runs both."* 最扎心一句：*"You wanted to offload the work. Instead, you
  became the work."*（你本想把活外包出去，结果你变成了那个活。）
- **Addy Osmani《Agent Harness Engineering》** —— **地基文**，最硬。Viv Trivedy 那句
  *"Agent = Model + Harness. If you're not the model, you're the harness."*，加一条带硬数据的
  实证：同一个模型（Opus 4.6）装在 Claude Code 里 vs 装在定制 harness 里，Terminal Bench
  2.0 分数差一大截，**只改 harness 就能把一个 coding agent 从 Top 30 抬到 Top 5**。

读完三篇，我对"loop engineering 是什么"懂了。但我那个别扭**一点没解**——因为这三篇都在讲
"loop 是什么样"，没有一篇回答**"为什么同样的产品、同样的模型，他在跑 loop，我在交互"**。
这个问题，得自己撞。

> 一个值得记的元认知：这三篇里有两个**软数字**——explainx 那个无来源的"10–100× 杠杆"、
> Vovance 那个单来源的"98% 企业管 AI 成本"。只有 Osmani 的 Top 30→Top 5 是带硬实证的。
> 读热点文，随手标记"听着爽但没支撑"的数字，是基本功。

---

## 第二站：先把"prompt"这个词拆开

捅破的第一刀，是发现 Boris 那句话里的 **"prompt" 是动词，不是名词**。

- **名词 prompt** = 我写的那段指令（task + 边界 + 验收标准）。
- **动词 prompt** = 我**一轮一轮地敲、看、纠正、再敲**那个过程。

他说"I don't prompt anymore"，杀掉的是**那个动词**——一轮一轮的人肉来回。他**没说**他不写
指令了。他照样写 task、边界、验收标准，跟我写 skill 一模一样。**他杀掉了动词，保留了名词，
然后把名词搬进了一个会自己跑的容器里。**

所以"我也在打字"这件事根本不构成反驳：他**只打一次**，然后走开；我是**打一句、盯着、再打**，
盯一个小时。区别不在打不打字，在**打完之后谁在闭环**。

---

## 第三站：三把椅子（这一篇真正的收获）

一个能自己跑的 loop，有三个位子必须有人坐：**规划、验证、把关（权限）。**

**交互式使用 = 这三把椅子全是我在坐，而且每一轮都坐着。** 这才是"我用了最新模型还是交互式"
的真正原因——**不是模型把我困住，是我的角色把我困住。我一个人占着三个位子。**

把我自己干活的习惯对号入座，吓一跳：

| 回路里的位子 | 我平时怎么做的 | = 我坐在哪把椅子 |
|---|---|---|
| **规划** | "我先把活拆成小的，再一个个喂给它" | 规划椅——拆解在我脑子里 |
| **验证** | 我亲自跑测试、读输出、判断够不够好 | 验证椅——验收在我手上 |
| **把关** | "很多权限需要我点同意" | 把关椅——每个动作我点头 |

我一直以为"先拆小活儿再喂"是个**好习惯**。撞明白之后才知道，它恰恰是**交互式的特征**：
我把编排留在了自己脑子里。Boris 把编排**外包给了 harness**。

Boris 那句 "I have loops running" 的真正含义，就是：**他把这三把椅子全空出来了**，让 harness
去坐——

- **规划椅** → 给大目标让 agent 自拆（或给它一个 planning 步骤），不在自己脑子里预切。
- **验证椅** → 把验收标准接到 `pytest` / grader 上，机器判过没过。
- **把关椅** → 见下一站。

> **同一句"测试必须过"，在 skill 里和在 loop 里不是一回事。** 在 skill 里它是一句**愿望**，
> 模型读一下、尽力而为；在 loop 里它是一条 `while ! pytest`，**真在跑、真在拦**。我过去写
> skill，是把验收标准**说给模型听**；loop engineering，是把同一句话**接到一台真会执行它、
> 并据此决定重跑的机器上**。写法没变，变的是这段话接到了哪儿。

---

## 第四站：把关椅怎么腾——权限不是开关，是策略

我最尖的一个疑问是：**跟 AI 干活时一堆权限要我点同意，Boris 怎么做到全程无人审批?**
如果每个动作都要点头，他根本走不开。

撞明白的答案是：**他没有取消安全边界，他把边界从"每步问人"挪成了"一次圈地 + 沙箱兜底"。**

- **我现在（每步把关，人必须在场）**：要 Edit → 点同意 → 要跑测试 → 点同意……人一走，loop 卡在弹窗上死掉。
- **loop（一次圈地，人可以走）**：启动前定一次——"只能 Edit + 跑 pytest，别的不行，且整件事在
  一个隔离 worktree / 沙箱里跑"。**圈内任何动作不再问；只在要跨出围栏（不可逆动作）时才拦。**

三条配套，缺一不可：**允许清单（预授权一小撮工具）+ 沙箱/worktree 兜底（关住爆炸半径）+
只对不可逆动作留闸**。所以"全程不用审批"不是把安全关了，是把安全的**形态**换了——从"人盯着、
逐个放行"换成"先把围栏和沙箱定死，再放它在围栏里自由跑"。

这也解释了为什么 Vovance 把 **worktrees**、Osmani 把 **sandboxes** 单列为 harness 组件：
**它们正是让你能安全地撤掉每步审批的那个东西。**

> 而"权限不是问/不问的开关，而是一条可按场景调的策略"——正是我 `REFERENCE.md §3.5 安全边界`
> 那块的设计核心。我早就知道权限是 harness 组件，但我缺的认知是：它是个**可调的旋钮**
> （交互态：多问；loop 态：allowlist + 沙箱 + 只拦不可逆），不是固定的一堵墙。

---

## 第五站：那个"无头"的产品形态，已经在那了

撞到这里，我去查了 Claude Code 当前真实的"无头/自动化"形态（核对官方文档）。一句话先说结论：
**"无头"不是新功能，是"把交互式那层人去掉"之后剩下的形态**——同一个 Claude，脱掉 TUI。

文档确认了那个让我纠结的机制：**输出不是 TTY 时（即 `claude -p`），权限弹窗会被自动跳过。**
也就是说，无头形态**默认就没有"问人"这一步**，安全全靠前面那套圈地 flag：`--allowedTools`、
`--permission-mode acceptEdits`、`--max-turns N`（这个轮数上限，正是 Vovance 说的"别让 token
焚炉烧爆预算"的保险栓）。

第一方已经把每把椅子都做成了产品原语，排成一条光谱：

| 形态 | 是什么 | 替我坐了哪把椅子 |
|---|---|---|
| `claude -p "..."` | 无头单发，跑完吐结果退出 | 把关。**所有 loop 的原子** |
| Agent SDK（Python/TS） | 在我自己进程里跑 agent 循环，默认不问 | 全部——**Ralph-loop 引擎本体** |
| `/loop` | 会话内定时轮询 | 触发 |
| `/schedule` + Routines | 云上排期 agent（cron / GitHub 事件 / API 触发），每次开全新 session | 触发 + 把关 |
| GitHub Action | PR 一来自动 review / 修 / 实现，turn 间无审批 | 触发 + 把关 |
| `/batch` | 把大改动拆成独立单元，每个开一个 worktree agent 并行跑、各自开 PR | **规划 + 并行** |
| Managed Agents | 托管生产 agent，跑在隔离沙箱，带 cron 和凭证 | 全部，托管 |

两个对照让我彻底服气：

1. **`/batch` 就是我"先拆小活儿"——但搬进了 harness。** 我坐在规划椅上手动拆，`/batch`
   把规划椅也腾空了：拆解发生在 harness 里，不在我脑子里。这就是"我做的"和"loop 做的"那道线的产品级实例。
2. **Agent SDK 就是我正在造的东西的第一方版本。** 文档对它的描述——"在你自己进程里跑 agent
   循环、无 TTY、默认不问、预置 Read/Write/Edit/Bash 工具"——几乎就是 `build-my-own-harness`
   的产品说明书。我手搓的 `while stop_reason == "tool_use"` + 工具注册 + 权限策略，被官方打包成了它。

---

## 我现在在哪

- 我的 harness 现在的产品形态：**CLI 交互**。
- 我跟它干活时：**规划、验证、把关三把椅子，每一轮都是我在坐。**
- 我用最新模型也没变——因为**困住我的不是模型，是我占着三把椅子的角色。**

但这趟撞下来，我发现自己有一个别人没有的位置：**我正在亲手造这三把椅子。** 别人只能用
Claude Code 现成的 Agent SDK；我在 `build-my-own-harness` 里，可以**亲手把规划器、验证闸、
权限策略设计成"可被腾空"的样子**——这正是 loop engineering 的引擎本身。

> 回扣 Osmani 那句：harness 不会因为模型变强而消失，它会**搬家**——"每个 harness 组件都
> 编码了一条'模型自己做不到什么'的假设"。我的别扭从头到尾都没指向模型，它指向的是**我的
> harness 还停在"为人在场而设计"的那一档**。把它升级成"能腾空三把椅子"，就是我的下一步。

---

## 下一步

把这趟认知落成动手的活：对着 Agent SDK 这个第一方样板，逐条比我 `REFERENCE.md` 的九要素，
列出"要把我的 harness 从 CLI 交互升级成能跑无头 loop，还差哪几个模块、按什么顺序建"——
**规划器**（让模型自拆，而非我预拆）、**验证闸**（把验收接成可执行 gate）、**权限策略**
（从每步问，改成 allowlist + 沙箱 + 只拦不可逆）、以及最外层的**触发 + 重喂循环**。

这趟讨论最大的价值，不是教会我 loop engineering 是什么，是让我**真实地理解了这个需求长在
我项目的哪个位置**。现在我知道我在哪了，可以去实现它。

— 2026-06 认知记录
