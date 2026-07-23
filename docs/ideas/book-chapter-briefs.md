# 各章写作思路 — quick pass v0(2026-07-10)

> 章循环第 1-3 步的批量预跑。每章三行:**链** = 问题链骨架 / **魂** = 章的判断内核(不是教科书的部分)/ **难题** = 挑战级采访问题(作者时间只投这里;"无" = 素材齐或作者已确认会,写作时顺带确认即可)。Part V 未 build,占位。

## Part I 骨架

**Ch2 朴素循环**
- 链:单次调用(oh ask 形态)能说不能做 → 给工具 → 一次工具不够,要看结果再决定 → while 循环,退出 = 模型不再要工具。真实历史(Phase 1→2)本身就是这条链,只需压缩时间。
- 魂:LLM 自己当编排器,harness 不设状态机。战例:D7.1 amendment 主、append-only vs Rich Live 副。
- 难题:无(作者已确认会,含 4 个常规问题:第一次心脏跳动 / 本质认知来源 / 最小工具集 / 战例取舍)。

**Ch3 流式协议与 provider 抽象**
- 链:想换 provider → 格式差异 if/else 渗进循环每行 → 抽象缝定在"一个流式协议",格式转换/重试下沉到各 client,循环层零感知。
- 魂:抽象缝的位置选择——为什么缝在事件流,不在 request/response 层或 SDK 适配层。战例:qwen-plus 退化实录(大 prompt + 多工具 → tool-skip)+ D5.3 默认模型升级 → 挂 strong-model thesis。
- 难题:**"为强模型设计"的可反驳形式是什么?** 你把 qwen-plus 的失败归为暂态——什么证据会让你放弃这个 thesis?如果部署环境永远只有弱模型呢?章里要给这个 thesis 一个诚实的边界,不能只有信仰。

**Ch4 工具系统**
- 链:工具多了 → inline dispatch 每加一个改循环 → Pydantic schema + 显式注册;工具要不要管权限 → 只声明 is_read_only,把门留给权限层(Ch6 伏笔)。
- 魂:工具是手脚不是守门人(关注点分离)。战例:phase-14 anti-substitution——模型拿本地 grep 冒充 web 搜索 → "缺工具是产品 bug,不是超纲"。
- 难题:**工具粒度从哪来?** 什么该是一个独立工具、什么该是参数?(CC 的 Read/Grep/Glob 分立 vs 单个 fs 工具)你的 5 个 base tools 是怎么切的、什么时候会切错?

**Ch5 系统提示装配**
- 链:agent 有手脚但不知道"自己是谁、在哪、有什么" → 环境快照 + 工具描述 + CLAUDE.md 发现 → 纯函数装配,每轮请求携带。
- 魂:system prompt 是 context 的静态侧;写什么进去本质是常驻预算的分配。
- 难题:**context 预算分配的一手原则。** 什么值得常驻 system prompt、什么进 skill 懒加载、什么靠工具现查?这是全行业都在摸的问题(context engineering),这一章能不能给出你自己的分配法则——这是本书竞争力所在的章之一。

## Part II 不死

**Ch6 安全边界**
- 链:agent 会删文件读凭证 → 权限门(分层链,首个命中即决,敏感路径硬编码不可覆盖)→ hooks 给可编程确定性拦截 → 参数化工具门够用,Bash 是通用计算 → 门不够 → sandbox(7a 进程抽象 → 7b Docker → 7c gVisor)。
- 魂:两类"模型说了不算";最危险的事不靠用户配置兜底。素材富+(三个 substrate)。
- 难题:**安全默认值的哲学。** 哪些默认开、哪些 opt-in?sandbox opt-in、敏感路径硬拦不可配——这条分界线的原则是什么?(已有 opt-in calibration 原则,但 harness 场景的完整答案值得一次采访)

**Ch7 上下文与会话生命周期**
- 链:长 session 必撑满 → 免费的先来(清旧工具输出)→ 确定性截断 → 最后才花钱 LLM 摘要;session 会断 → 快照/续接/轮转 → 谁来写"我在干嘛"(LLM-authored task_focus_state)。
- 魂:压缩阶梯 = 成本意识;认知陷阱:切分不能断 tool_use/result 配对。
- 难题:**怎么知道压缩没有伤行为?** task_focus_state 把记忆主权交给模型,丢错了怎么发现?(往 Ch9 递问题——这可能就是 Part II→III 的必然性链原文)

**Ch8 持久记忆**
- 链:session 结束就忘 → 存哪(Markdown vault vs 向量库)→ 怎么找(启发式打分:metadata > 正文,重要度/频率/新鲜度)→ 谁来写(后台抽取/固化)。
- 魂:phase-16 架构 pivot——推倒自己的第一版对齐 CC pattern,全书最硬的"我错了"战例。对标 EverOS(local-first / Markdown SoT / hybrid 检索)。
- 难题:**pivot 的触发时刻。** 什么证据让你决定推倒重来?错误架构当时的症状是什么、为什么没有更早发现?"决定推倒"那天的成本账怎么算的?——这是全书最值得深挖的一次采访。

## Part III 度量

**Ch9 确定性测试的边界 + eval substrate**
- 链:改 prompt,pytest 全绿,行为裂化 → 确定性断言够不到概率行为 → eval = 测试学科的延伸(决策面 map + 三声明头 + pass bar)。
- 魂:eval ≈ software testing(作者锚点认知);coverage map 把"该测什么"变成可枚举对象。
- 难题:**决策面怎么枚举才不漏?** harness 的"概率决策面 map"方法论是什么、D41 的优先级 reshuffle 为什么发生——枚举法则比枚举结果更值钱。

**Ch10 LLM judge 与 cassette**
- 链:有些契约人都说不清 → judge 服务契约模糊性(不是万能尺);eval 又贵又慢又抖 → cassette 录放分离确定性与概率性。
- 魂:judge 的适用边界;M3 case study 是现成教学案例。
- 难题:**judge 的 judge 问题。** 你怎么校准 judge 本身、什么时候不信它?judge 判错过你一次吗——那次你怎么发现的?

## Part IV 生态

**Ch11 skills**
- 链:领域经验塞 system prompt 撑爆 → SKILL.md + catalog 懒加载 → 触发机制(slash 显式 vs LLM 自动,D38 synth envelope 的三消息信封)。
- 魂:SKILL.md 是可执行 spec——dogfood 观察#2(常识与 skill 冲突时 LLM 跟 skill 走)是铁证;业务专家能写。
- 难题:**懒加载的召回问题。** skill 多了,该触发的没触发怎么办?你现在的答案(description 质量?catalog 密度?)和你观察到的失败形态。

**Ch12 mcp**
- 链:内置工具永远不够 → 开放协议接外部系统 → 外部工具进同一个注册表(federated)。
- 魂:协议标准化的产业逻辑(I why-protocol-standardization 已成文一半)。
- 难题:**外部工具的信任边界**——MCP 工具进来,权限按什么算?(回连 Ch6;轻量,可写作时顺带)

**Ch13 plugins**
- 链:skill 散装没法卖 → 组织边界(版本/权限/marketplace)→ CC 格式 = 事实标准 → 零改动接入 → dogfood:自己的 plugin 跑在自己的基座上。
- 魂:基座 + 插件;FDE 工作流亲历。章内结构 = 读 → 造 → 沉淀。
- 难题:无——采访已完成,实录在 F 02-dogfood-run。全书素材最齐的一章。

## Part V 规模(未 build,占位)

**Ch14 tasks / Ch15 swarm / Ch16 coordinator**
- 预设链:单上下文装不下 → 后台子进程(单例+状态机)→ 并行要通信(文件 mailbox 原子写,崩溃可恢复)→ 编排要防失控(三重防护 = 结构性深度上限)。
- 难题:**推迟**——真正的问题链、战例、判断都要等 build;build 时按"将来是一章"的标准留痕。

## Part VI 真战场与全景

**Ch17 接触面**
- 链:runtime 强了,入口只有 REPL → headless(-p)与 TUI 共享同一 runtime(同 engine 不同 sink)→ JSON-lines 协议方向不对称。
- 魂:一套 runtime 喂多个接触面;headless 是 eval/benchmark 的驱动口(为 Ch18 递枪)。
- 难题:**TUI 的未走之路。** node-tui-next-step 记了下一步,后来为什么没走/走成什么样?tui-vs-web 的判断现在还成立吗?

**Ch18 benchmark in action**
- 链:尺+规模+入口齐 → 上 SWE-bench → 7 个节点的失败与策略调整(RUNLOG 全程)。
- 魂:数据飞轮 thesis(使用数据是矿不是尺,瓶颈在 labeling)。
- 难题:**benchmark 的局限的一手判断。** 分数说明什么、不说明什么?战役投入产出怎么算?如果重打一次,哪个节点的决策会变?

**Ch19 全景 + 前沿**(与 Ch1 同批,最后写)
- 链:同一棵树回看,每个节点读者亲手写过 → 开放问题(memory 语义检索 / 多 agent 经济学 / eval 的 oracle 问题)→ channels/bridge 速写。
- 难题:留终局——开放问题清单的取舍本身就是最后一次采访。

## 难题清单(作者时间的投放地图)

按预估挑战度排序:

1. **Ch8 pivot 触发时刻**(推倒重来的证据与成本账)★ 全书最深的一次采访
2. **Ch5 context 预算分配法则**(行业都在摸,一手答案 = 竞争力)
3. **Ch3 strong-model thesis 的可反驳形式**(thesis 的诚实边界)
4. **Ch9 决策面枚举方法论** / **Ch10 judge 的 judge**(度量部的两块硬骨头)
5. **Ch6 安全默认值哲学** / **Ch4 工具粒度法则**(设计判断题)
6. **Ch18 benchmark 局限** / **Ch7 压缩伤害检测** / **Ch11 懒加载召回**(实战复盘题)
7. **Ch17 TUI 未走之路**(轻)
8. Ch14-16:推迟到 build 后
