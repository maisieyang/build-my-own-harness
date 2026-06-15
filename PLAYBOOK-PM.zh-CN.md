# PLAYBOOK-PM — 把 harness 当产品看

> [PLAYBOOK.zh-CN.md](./PLAYBOOK.zh-CN.md) 的一篇短伴读。那一份讲工程方法;这一份讲 OpenHarness 上
> 那几个**不是技术、而是产品**的拍板。一个对结果负责的工程师,不管有没有人给他这个头衔,都会做这些决策。
> 塑造了这个项目的 6 条:

| # | 决策 | 背后的产品张力 |
|---|---|---|
| 1 | **Provider-agnostic 是不变量,不是 feature。** 同一套 loop、tools、权限模型跑任何 OpenAI-compatible 端点——靠契约,不是事后栓个 adapter。 | 锁定 + 更简单的代码库 vs. 可移植。选可移植,顺带把 harness 变成一台*对照实验仪器*:固定它、换模型、把差异归因到模型。这个重新定义本身才是产品,不只是"灵活"。 |
| 2 | **薄核优先于编排。** 没有 graph builder、没有 workflow DSL——一个 streaming tool loop + 递归 sub-agent + 动态 skill。 | 现在多塞看得见的功能 vs. 保持薄。赌注:为今天的模型打补丁的脚手架,会随模型越来越擅长长程规划而老化成死重。押"六个月后的模型",是一个关于"价值会落在哪"的产品判断。 |
| 3 | **Scope discipline——刻意说不。** Tier-0/1 加一个深做的扩展就是一套完整 harness;sandbox 分层、更多 provider、完整 summarization 压缩都是**延后,不是砍掉**。 | 功能完整 vs. 可交付、可读的核。这里最难的产品肌肉,是拒绝那些"很好论证"的活——每一次延后都写下"什么条件会重新激活它",让"不"保持诚实,而不是被遗忘。 |
| 4 | **skill 是可执行 spec,不是文档。** 能力以"模型逐条执行"的形态交付,不是它扫一眼的散文。 | 描述行为的文档 vs. 产生行为的契约。把扩展面当可执行 spec,正是为什么一个非 Anthropic 的模型能逐条 follow 带编号的硬拒绝规则、并在输出里逐字引用规则号——伴随仓库 [finance-skills](https://github.com/maisieyang/finance-skills) 就是证据。 |
| 5 | **差异化错误,默认模式无 traceback。** config error、401、429、loop-limit 各给一句不同的、人能读的提示。 | 开发省事(直接 raise)vs. 终端用户体验。Python traceback 是最省事 ship 的东西,也是最难读的东西;**决定错误长什么样,本身就是产品的一部分**,不是事后补。 |
| 6 | **eval substrate 当信任特性。** 概率行为的回归基线被当成一个产品属性——"它不会悄悄劣化"——而不只是内部 QA。 | 你能*感觉到*的行为 vs. 你能*辩护*的行为。对一个一半代码、一半模型的系统,"不会劣化"是用户应当能依赖的东西,这让验证层成为一个 feature,而不是开销。*(仍在建设中——见 PLAYBOOK §5。)* |

---

**这说明我怎么想:** 对工程师来说,product sense 不是另戴一顶帽子——它是在你**亲手写代码的同时**,
决定*不造什么*、*价值六个月后会落在哪*、以及*用户在错误边界上真正体验到什么*。这 6 条是在键盘前做的,
不是在规划会上。

> 完整的工程工作模型 → [PLAYBOOK.zh-CN.md](./PLAYBOOK.zh-CN.md)。
