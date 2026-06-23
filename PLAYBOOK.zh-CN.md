# PLAYBOOK — 一个 harness、一个金融 plugin,以及它俩背后的方法

> 一个开发者 + Claude Code,7 周,三个 repo:从零造一个生产级 agent **harness**、把方法**编码成可
> 复用的 skill**、再把一个**垂直行业的 plugin** 带进金融。这是这三件事背后的工作模型和信念——
> 不是一份写 prompt 的指南。**人守在契约层,agent 驱动实现,几条硬纪律保证速度是诚实的。**

---

## 1. 我到底信什么

我不靠读来学,我靠重建来学——而且我把它变成了一套**方法,不是习惯**。7 周里我把同一个动作在
两个高度上各跑了一遍:从零重建了一个生产级 agent **harness**,又从零重建了一个**垂直行业的 plugin**。

做完这两件事,我挣到的信念是:其实只有**一个东西——模型能调用的一份能力——穿着三种封装**:

> **tool · skill · plugin**
> - **tool** = 这份能力**常驻** —— LLM 的 syscall(在这个 harness 里就是 `BaseTool`)。
> - **skill** = 这份能力**懒加载** —— body 被按需调出的专家上下文(这里是*通过*一个 tool 交付:`LoadSkill`),它本身**不是** tool。
> - **plugin** = 这份能力**打包待发** —— 一个 skill 外面裹上 manifest、版本、权限面、marketplace 条目:它能被版本化、授权、售卖的那层封装。

同一份能力,三种封装——第三种就是工程变成**产品**的地方。这层封装是横向 LLM 平台进入高 ACV 垂直
的方式;在我看来,也是 Anthropic `model + harness + plugin` 那步棋背后的道理。
大多数人能把 plugin 说成"一种扩展 agent 的方式"。很少有人能讲清它到底是什么:能力从"只是代码"变成
"你能版本化、能授权、能卖的东西"的那层封装。我能讲清,因为我**既写了 loader、又造了 plugin**,
看清了技术 artifact 到哪里结束、产品 wrapper 从哪里开始。

三条我敢 defend 的观点,每条都有兜底它的 repo:

- **想吃透一个领域,就重建它最好的参照。** —— 我跑了两遍(harness、finance)。
- **harness 必须薄;模型才是产品。** —— [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness)。
- **进垂直,护城河不是模型——是 plugin 作为你能版本化、授权、售卖的那个单位。** —— [finance-skills](https://github.com/maisieyang/finance-skills)。

---

## 2. 这条弧线 —— 一套方法,两个高度,三个 repo

```
   读(study)                  造(build)                   沉淀(distill)
     │                          │                            │
 平台层  OpenHarness      →  build-my-own-harness    →  本 PLAYBOOK
 垂直层  Anthropic         →  mybank-credit-risk      →  finance-skills/PLAYBOOK
         financial-services
```

同一个形状,两个高度:研究最好的参照,从零重建,蒸馏出原则。**选对参照是一半的赌注**——它得是
行业标杆、你用过到有"品味"、而且还活着。出来三个 repo,它们正好对上这三种封装:

- **tool** → [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness) —— 基座:
  从零重建的生产级 harness(参照:[HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness))。
- **skill** → [my-skills](https://github.com/maisieyang/my-skills) —— 方法本身,被编码成可复用的
  skill(fork 自 agent-skills;我只编码基石没有的)。正是它让这个循环**可复现,而不是靠运气**。
- **plugin** → [finance-skills](https://github.com/maisieyang/finance-skills) —— 垂直层:同一个动作
  跑进金融(参照:Anthropic 开源的 `financial-services`;我从零造的:[`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk))。

---

## 3. 垂直那一章 —— 用 plugin 验证那条理论

harness 回答了"一个横向平台怎么搭出来"。它留了个问题:**这个平台怎么*进入*一个行业——而 plugin
到底是什么?** 在我看来,Anthropic 那步棋是 `model + harness + plugin`,瞄准高 ACV 垂直(金融 / 法律 / 医疗)。
我想验证它——用我唯一信的方式:**自己造一个**。

于是我把同一个动作往上跑了一个高度。我研究了 Anthropic 开源的 `financial-services` 设计,然后从零
造了 [`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk)——
一个中国银行个人消费贷征信核查的 plugin:一个 agent、一份**写到深**的 `SKILL.md`(装着十年老兵从不
写下来的判断)外加几份更薄的、一个共享 connectors plugin、显式的 tradeoff。完整的垂直 playbook 在
[finance-skills](https://github.com/maisieyang/finance-skills) 里,这里不重复。

我**造它挣到**的,是第三种封装:**plugin 底下还是同一份能力**——它值钱的不是机制,是外面那层 wrapper。
plugin 是一个能力变得**可版本化、可授权、可售卖**的那层封装:工程变成产品的地方,也是为什么一个横向
平台能**不重写内核就够到一个垂直行业**。

而且我把闭环跑到了真正关键的地方——**接缝处**。我教会自己的 harness 加载 Claude-Code 格式的 plugin
(一个双格式 `PluginLoader`),把金融 plugin 丢进 `~/.openharness/plugins/`,在自己的 runtime 上触发了
`/credit-report-reviewer__parse-credit-report`。plugin 加载了、skill 触发了,而模型做了**对的事**:它去
要征信数据源、而不是凭空编一个(当时没接 bureau MCP)。所以 **layer 1 真的加载并 dispatch 了一个
layer-3 plugin**——托管路径在我自己的 harness 上端到端跑通了。(诚实地界定范围:这证明的是 plugin
**机制**,不是一份完成的征信核查;缺口写在 [`learnings/phase-19.md`](./learnings/phase-19.md)。)

*(一个意外的发现,但诚实起见还是说:做这个垂直也让我看清,FDE 的日常——把方法论从领域专家脑子里
挖出来——不是我的重心。我是个平台型的人。我能知道这点,正因为我把另一件事也做了。)*

---

> 这份 PLAYBOOK 剩下的部分,是上面那套方法的细节——*我具体怎么 build*,在 harness 上展示(常驻
> 那层,纪律最严的地方)。

## 4. 工作模型:人守契约,AI 驱动实现

让 AI-first 开发真正 work 的,是一条干净的所有权分界:

- **人守在契约层**——scope、接口、trade-off、验收标准,以及一切不可逆操作的拍板。**造什么、为什么造。**
- **agent 驱动实现**——拆解任务、写代码、写测试、迭代到绿。**怎么造。**

这不是个人偏好,是 2026 年行业落定的位置。Anthropic 自己把这套 operating model 概括为
**delegate, review, own**:agent 做第一遍执行、脚手架、实现、测试和文档,工程师 review 正确性与风险,
并保留对架构、trade-off 和结果的所有权。杠杆来自守住这条线——既不把契约 delegate 出去,也不去越俎
代庖盯实现。

两侧各自的失败模式:

- **把契约让出去**,你得到一段流畅、却在解决错误问题的代码。
- **把实现抢过来**(给 agent 派逐文件的 sub-task),你既扔掉了它最大的优势——它读的是当下真实的代码,
  你的 plan 只是猜测——又把自己从架构师降级成了工单员。

---

## 5. 模块循环

一次重建大到没法预先设计完。它以循环的方式跑,每个模块一轮,按依赖顺序:

```
  reverse-spec  ───────────────────────────────►  REFERENCE.md
  (一次,开工前)                                   (冻结的认知地图:§1–§4 + §5 模块拆分)
       │
       │  之后,逐模块——按 §5 钉死的依赖顺序:
       ▼
  ┌──────────────┐      ┌───────────────────────┐
  │     设计      │ ──►  │         实现           │ ──► commit
  │ interview-me  │      │   solo 写码循环         │
  │   + plan      │      │   (§6:TDD 到绿)        │
  └──────────────┘      └───────────────────────┘
```

**先建参照系(`reverse-spec`)。** 动手前,把对标项目逆向成 `REFERENCE.md`——一张**认知地图,不是
工程合同**。它回答"这个领域里一个专业系统由哪些核心要素构成、每个解决什么问题",通过三个镜头:带
注解的**目录树**、**数据流**(一条输入怎么从进到出)、以及**核心要素概念地图**(从数据流里提炼,**不是**
照抄目录名)。最后 §5 把这些要素拆成一串**有序的 build 模块**,按依赖排:先建能让一条输入端到端跑通
的最小骨架,再往外加层。`REFERENCE.md` 是冻结的——它是你动手时对着的底图,防止重建悄悄做成玩具。

**逐模块设计(`interview-me` + `plan`)。** 想清楚模块的角色、核心要素、你对 trade-off 的立场——再拆成
capability 级的任务清单 + 验收标准。plan 停在 *capability* 高度,绝不下沉到 sub-task:agent 拆得比 plan
文档更准,因为它看的是当下的代码。plan 文件**留档不删**——在一次漫长、发散的重建里,它是"叉出去探讨
完还能回来"的锚。

**实现**走 §6 的写码循环。

---

## 6. 让速度诚实的几条纪律

没有这几条,速度只是在攒债。每一条都在这里,是因为它拦的那种失败,下游任何东西——CI 也好,覆盖率门
也好——都拦不住。

**TDD 是脊梁。测试就是 spec。** 先写测试,**亲眼看它变红**,再写代码到绿。一个你没见过它失败的绿,是
假绿。测试挂了,你改代码——**绝不弱化断言、绝不改测试去凑绿**。在"快点过"的压力下,这是最容易开始飘的
一条,所以也是守得最死的一条。

**Review 在 commit 边界,不在之后。** 测试全绿 ≠ 验收。`git commit` 之前,把 diff 对着验收标准逐条走查。
这是**唯一**能 catch 三件事的机制:一条被悄悄漏掉的验收标准、一个写得太松以致真有 bug 也不报红的测试、
一笔混进 commit 的无关副作用改动。把这步 delegate 出去,你就不在契约层了——你在祈祷。

**复用优先于重造——薄层那条线。** 你为"基石暂时做不到的事"写的每一个 workaround,在它能做到的那一刻
就变成死重。编码任何东西之前,先问基础是不是已经提供了。装配现有的,只造缺的。(这跟"harness 必须薄"
是同一条信念,用在"项目本身怎么造"上——也正是 `my-skills` 编码的东西。)

**trail 就是记忆。** solo 项目真正的风险不是没人帮你,是**过去的你不再帮你**——三周前的决策,今天就忘。
append-only 的三个 trail(`decisions/`、`tasks/`、`learnings/`)解决它。重点不是谁会从头读到尾,而是任何
时候冒出一个新想法,你立刻知道它该归到哪个文件夹——正是这种零摩擦的归档,让一个 solo 项目能持续保持
节奏。

---

## 7. 它确实成立的证据

这套方法造出了全部三级——而且还在造。这不是一次冲刺、ship 一下就停了;它是**一个人、持续数周的自循环
迭代,代码、方法论、文档一起长**。版本号在这里是次要的——真正的里程碑不是某个 tag,是整条弧线一起成型
了。可核查的部分:

- **弧线是真的,不是讲出来的**——三个 ship 出去的 repo(harness / skills / 垂直),而且垂直 plugin 真的
  在 harness 上**加载并 dispatch** 了(§3——托管路径,不是一份完成的征信核查)。方法在两个截然不同的高度上
  都泛化了;这是"它是方法、不是一次性运气"最强的证据。
- **持续的 solo 迭代**——~7 周,光 harness 就 20 个子系统(engine、tools、hooks、permissions、
  observability、MCP、skills、sub-agents、sandbox、compaction、memory……),300+ commits。数字只是给个
  大概印象;重点是它在自己的循环里一直跑下去,靠一个人的意志力。
- **质量门全程守住**,在 CI 上强制、不在本地:`src/` 全量 `mypy --strict`、`ruff` lint + format clean、
  **≥95% 覆盖率门**,Python 3.10 和 3.11。
- **完整的推理 trail 留存**——每个 trade-off 在 [`decisions/`](./decisions),每篇回顾在
  [`learnings/`](./learnings),plan/execute 痕迹在 [`tasks/`](./tasks)。不只是**造了什么**,而是**每个
  trade-off 为什么这么定**。

为什么 §6 那几条纪律不是可选项——行业是花了代价才学到的。Anthropic 的
[2026 年 4 月 Claude Code 复盘](https://www.anthropic.com/engineering/april-23-postmortem)记录了三个纯
*harness 层* 的改动(没动模型)让质量悄悄劣化了约 6 周;重度 dogfooding 没拦住。补救措施是:**每一次触碰
模型行为的改动都跑完整评估**。LangChain 的
[harness engineering 实验](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering)
是最干净的对照:模型固定,纯靠 harness 改动把一个 agent 从 52.8% 提到 66.5%——其中一个听起来很合理的改动
("全程开最高 reasoning")被**测量**证明反而更差。能泛化的教训:当一个系统的行为一半是代码、一半是概率
模型,"看起来没问题"和"测试照样过",都不等于"它没有劣化"。验证必须是刻意的。(这个项目已 ship 一套 eval
substrate、两个 consumer;把它变成对*每一次*概率改动都跑的、有纪律的回归基线,仍在进行中——见 §8。)

---

## 8. 诚实的边界

这套模型**不**适用在哪,直说:

- **只适合 solo。** 整套东西假设契约由一个人独握。一旦你有多个 stakeholder、reviewer 或角色边界(PM、
  架构师、合规),你需要的是这套刻意省掉的、更重的协调机器。
- **需要有对标物可重建。** "靠重建来学"预设了一个值得逆向的强对标。对真正全新、没有同类可研究的问题,
  `reverse-spec` 这步无从下口——那是真 R&D,不是重建。
- **概率行为这一层仍在成形。** 确定性测试证明不了一次 prompt 或 memory 改动没有劣化涌现行为(见 §7)。
  一套有纪律的概率行为回归基线,是我**仍在实践中摸索**的东西,不是一套我会当成定论交给你的方法。

---

## 指针

弧线,三个 repo:

- **tool** → [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness)(你在这)—— 基座 harness
- **skill** → [my-skills](https://github.com/maisieyang/my-skills) —— 方法被编码成可复用 skill
- **plugin** → [finance-skills](https://github.com/maisieyang/finance-skills) —— 同一个动作跑进金融垂直

仓内:

- [`README.zh-CN.md`](./README.zh-CN.md) —— 项目入口与架构
- [`REFERENCE.md`](./REFERENCE.md) —— 对标项目的冻结认知地图
- [`decisions/`](./decisions) · [`learnings/`](./learnings) · [`tasks/`](./tasks) —— 推理 trail
- [PLAYBOOK-PM.zh-CN.md](./PLAYBOOK-PM.zh-CN.md) —— 同一个项目的产品视角
