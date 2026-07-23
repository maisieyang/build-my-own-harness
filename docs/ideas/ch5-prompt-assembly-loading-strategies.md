# Ch5 素材 — Prompt 组装:三种加载范式与"有界全放、无界放指针"

> 2026-07-22,从一次"带用户从 tools 怎么加载 → 读完整 prompt"的对话沉淀。
> book-outline Ch5(prompts)的一手骨架。全部有 file:line / 真实渲染实证。
> 入口钩子(叙事用):用户一句"我让它写文件,不是该出 Write 吗,为什么还
> 调 Bash?"→ 拉出整条 prompt 组装 + 上下文管理的线。

## 一、完整 prompt 长什么样:五段式 + recency 排序 + 可插拔

一个真实渲染的 system prompt(`build_system_prompt`,中等配置 ~7800 字符):

```
[开场白]              你是谁 + 三条行为基调(错误可恢复 / 别过度探索 / 匹配意图)
## Tools              能力清单
## Available Skills   领域知识目录
## Environment        OS/shell/cwd/python
## Memory             规则(静态)+ 索引(动态)
```

**三个整体特征**:

1. **顺序是递进,不是随便排**:我是谁 → 我能做什么 → 我在哪 → 我记得什么。
   从通用能力走到具体语境。
2. **recency bias 定位**:memory 放**最后**(最靠近用户消息)是刻意的——
   LLM 越靠后越重视,把最 query-specific 的上下文(记忆)放离问题最近处。
3. **可插拔组装**:同一个 `build_system_prompt`,靠 kwargs 决定哪些段进。
   `## Project Instructions`(CLAUDE.md)/`## Web Access`/skill/memory 段
   都是可选;所有可选 kwarg 不传时,输出与最早 Phase 2 prompt **字节一致**
   (向后兼容不变量)。"完整 prompt"是个动态概念,不是一个固定字符串。
4. **它也在塑造人格**:"read the message and adapt, most errors are
   recoverable"(不慌会恢复)/ "don't pre-emptively explore for greetings"
   (有分寸)/ "trust observation over stale memory"(不迷信自己记忆)——
   这些不是功能,是性情。Prompt 既配能力也定分寸。

## 二、三种加载范式(核心):有界全放、无界放指针

| 段 | prompt 里放什么 | 大块内容在哪 | 按需加载动作 | 策略 |
|---|---|---|---|---|
| **Tools** | name + **完整 desc**(+ schema 走 API) | 无——全在 prompt | 不需要 | **全量展开** |
| **Skills** | name + **一行 desc**(目录) | body(几百行)在文件 | `LoadSkill(name)` | **目录懒加载** |
| **Memory** | 规则模板 + **索引**(每条一行 hook) | 正文(几百字)在 md | `Read(file)` | **索引懒加载** |

**分野的原理是一句话**:
- **有界的东西全放**(tools:数量你自己定,~6-15,全展开成本可控)
- **无界的东西只放指针**(skills / memory:数量和体积都可能爆,只放"报菜名"
  的目录/索引,大块内容按需拉)

**后两种本质是同一招**:skill 的"目录" = memory 的"索引",LoadSkill = Read。
都是"prompt 里放 name+description 供判断相关性,判断相关了才拉正文"。

**体积观察(反直觉但重要)**:实测 Memory 段(5034 字符)比 Tools 段(1645)
还大 3 倍——因为 memory 规则模板本身长。但那是**静态固定成本**,不随记忆
增多膨胀;真正增长的是底下索引区(线性小增长,每条一行)。所以 memory
"看着大"但"增长可控":记忆涨到几百条,开局增量只是几百行短索引,正文永不
进开局。**索引懒加载 + 索引自身有 200 行上限 = 双保险。**

## 三、description 是三种范式的共同命门

无论哪种策略,进 prompt 的**一定有 description**——因为它是"要不要拉后面
那坨大东西"的**唯一依据**:
- tools:desc 决定"选不选这个工具"
- skills:desc 决定"要不要 LoadSkill 展开 body"
- memory:索引 hook(即 description)决定"要不要 Read 正文"

**推论**:description 是 eval 的被测对象(改它直接影响决策质量,skill_trigger
的 TS2 描述辨析、tool_choice 的选择维度都在测它);**name 不是**——name 是
机器标识符(调用/查找按它精确匹配),改一个字调用就断,而 desc 改一个字
只是"选择倾向"变了。**name 回答"叫什么"(机器用),desc 回答"干什么、何时
用、有什么坑"(模型读)。**

## 四、CC Tool Search 对照:同一约束逼出同一答案(2026-07-22 查证)

用户预测:"无界的东西必须懒加载,tools 一旦无界(MCP)必然向 skills 的
策略靠拢"。CC 官方文档字面证实——**Tool Search**:

- **不是按来源分档**(内置全量 / MCP 检索是错的直觉)。官方原文:"Tool search
  applies to **all registered tools**, whether from remote MCP or custom SDK
  servers"。**整池一刀切,阈值是总量而非来源。**
- **切换点是"总量占上下文百分比"**:`auto` = 工具定义合计 > 10% 上下文 →
  全体检索;< 10% → 全体全量。
- **机制 = skill 的 LoadSkill 搬到 tools**:平时只留 name+desc 摘要,schema
  被 withhold;模型需要时**搜索**目录 → 加载最多 5 个最相关的 schema。
- **两个动因**(第二个更狠):①上下文效率(50 工具 ≈ 10-20K token);
  ②**选择准确率**——"degrades with more than 30-50 tools loaded at once"。
  就算 token 够,工具太多模型也选花眼。按来源分档治不了这个,只有按总量
  整池切才治本。

**给本项目的清醒结论**:
- 6 个工具全量是对的(CC 同规模也全量,<~10 工具检索反而多一次往返更慢)。
- 将来接 MCP(OH = 领域插件 substrate,迟早接),别学"内置全量+MCP检索"
  的直觉分法,要"整池全量 or 整池检索,由总量决定"。
- 真正逼你切换的可能不是 token,是那条 **30-50 工具的选择准确率悬崖**——
  这条 tool_choice eval 将来能实测:工具数往上加,准确率何时开始掉,那个
  拐点就是该上检索的时候。**不用猜,用自己的 eval 量。**
- 姿态:代码里留注释标记"工具注入=全量,规模化时此处引入 search",知道
  天花板在哪,不提前修(同 F14/F11 的 backlog 纪律)。

## 五、章 through-line(和 Ch7 共享)

这一整条线和 Ch7(授权 vs 隔离)、权限那轮是同一种叙事:**几乎每个子系统,
都能从第一性约束推出 SOTA 的设计,而不是照抄**——上下文有限 vs 内容无界
逼出三范式;有人 vs 无人逼出 ASK vs sandbox。**"我没看 CC 文档就从代码/约束
推出了同一套设计",比"我参考了 CC"强得多,证明的是对本质的把握。** 这是
全书最硬的一条主线,Ch5 是它的一个满配案例。
