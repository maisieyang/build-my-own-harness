# Build a Harness in Action — 组织草稿

> 状态:draft(2026-07-09)。把 REFERENCE.md §5 的模块序试映射成章节,记录映射中暴露的断链与决策。
> 前置共识(已对齐):叙事用构建序、树做首尾锚点;每章固定模板;问题域当骨架、解法当血肉分层标注。

## 0. 好书的定义(全书裁判)

1. **树长出来的判据**:读者读到第 N 章能预测第 N+1 章解决什么——章节间靠"上一章 checkpoint 暴露的问题"续接。
2. **每章三件套**:问题域 + 解法思路 + **被拒绝的替代方案**(判断力,不只是知识)。
3. **时效分层**:稳定问题域写死(context 有限/模型概率性/权限边界),具体解法标注"这是 2026 年的答案"。
4. **可运行主线**:每章结束,同一个 codebase 多一个能力、能跑。不是每章一个孤立 demo。
5. **问题链叙事**:朴素方案 → 跑起来暴露什么 → 修复。设计是被问题逼出来的。
6. **有立场**:每章为某个 thesis 提供证据(见 §4)。
7. **失败实录**:RUNLOG 材料入章(战场实录栏目),尤其前沿部分。
8. **读者转变**:从"会调 LLM API 的工程师"到"能独立设计实现生产级 harness、能对新 harness 设计做判断的人"。不服务这个转变的内容删。

## 1. 映射中暴露的三个发现

### 发现 1:§5 缺了两个东西,书必须有

- **`prompts/`(系统提示装配 / context 静态侧)**:§2.0 目录树里有、真实 build 历史里有(learnings/09-prompts.md),但 §3 九要素没把它分给任何要素、§5 没有它的模块——REFERENCE.md 的覆盖核对实际漏了它。而 context engineering 是行业最热的 harness 话题,书里必须是一等公民章。*(顺手事项:REFERENCE.md 该补这个 gap,另议——它整份冻结,怎么补要单独决策。)*
- **eval(横切盲区)**:CLAUDE.md 明说确定性 GREEN 覆盖不到 prompt/memory/概率行为这层;§5 没有 eval 模块,但 decisions 31-35 + 40-41 + swebench 战役 RUNLOG 是全仓库最差异化的材料。书里升格为独立 Part,必然性来自 Part II 结尾:"改了 prompt,测试全绿,行为却裂化了——确定性测试够不到"。

### 发现 2:依赖序 ≠ 必然性序(Part I 要倒装)

§5 的顺序(provider 抽象 → loop → tools → CLI)是工程依赖序:先建被消费的底座。但读者需要必然性:**第一章就该用最朴素的形态跑通端到端**——直接拿 anthropic SDK 手写 while 循环 + 一个 inline 工具 + print 输出。然后问题链逼出抽象:想换 provider 要改循环 → 流式协议章;工具多了 registry 混乱 → 工具系统章。in Action 的契约(读完第一个 build 章手里有能跑的 agent)靠这个倒装兑现。

### 发现 3:弱必然性模块的处理(运行代码逼不出的,换手段逼)

| 模块(§5#) | 问题 | 处理 |
|---|---|---|
| auth 订阅桥(14) | 教学价值低 | 附录 |
| TUI+state(15) | React/Ink 是大岔路;认知点只是 JSON-lines 协议 + headless/TUI 共享 runtime | 压缩进"接触面"一章,不教前端 |
| channels/bridge(17) | 未必有完整 build 材料 | 终章前沿速写 |
| plugins(9) | "组织边界"靠跑代码逼不出必然性 | 用 finance-skills dogfood 故事逼(vertical substrate thesis) |
| sandbox(16) | 单独成章太薄 | 并入安全章问题链第二拍:参数化工具调用权限门够用,直到 Bash 给出通用计算能力 → 门不够 → sandbox |

## 2. 章节草稿(§5 十七模块 + 两个新增 → 6 Part ≈ 19 章)

**每章模板**:地图定位(点亮节点)→ 问题域 → 朴素方案与问题链 → 设计 + 被拒绝的替代 → build checkpoint(能跑)→ 战场实录 → 前沿标注。

### Ch1 地图
harness 是什么、九个硬问题(§3 的表)、最小认知树、全书 thesis 预告。树不求丰满,求"知道自己在哪"。

### Part I 骨架 —— 最小可跑的 agent
- **Ch2 朴素循环**:SDK 直连 + while 循环 + 1 个 inline 工具 + REPL。✅ 端到端跑通(§5 #2+#4 朴素形态)
- **Ch3 流式协议与 provider 抽象**(#1):问题链=想换 provider;脏活下沉,循环层零感知
- **Ch4 工具系统**(#3):Pydantic schema + 显式注册;工具不持有权限策略(为 Ch6 埋钩子)
- **Ch5 系统提示装配**(§5 外新增,prompts/):模型不知道"它在哪、能做什么";环境快照、CLAUDE.md 发现、context 静态侧

### Part II 不死 —— 核心横切
- **Ch6 安全边界**(#5+#16 并入):权限门(首个命中即决、敏感路径硬编码)→ hooks(可编程确定性拦截 vs CLAUDE.md 软指引)→ 问题链第二拍:Bash = 通用计算 → sandbox
- **Ch7 上下文与会话生命周期**(#6,范围扩自素材映射):压缩阶梯(免费→确定性→花钱)+ 会话快照/续接/轮转(phases 12-13 的素材在此落家);认知陷阱:切分不能断 tool_use/tool_result 配对
- **Ch8 持久记忆**(#7):Markdown vault + 启发式检索;被拒绝的替代=向量库(零依赖/人可读的 trade-off)

### Part III 度量 —— 你怎么知道它变好了(§5 外新增)
- **Ch9 确定性测试的边界 + eval substrate**(decisions 31, 35):eval ≈ software testing 的延伸
- **Ch10 LLM judge 与 cassette 回放**(decisions 32-34):judge 服务于契约模糊性

### Part IV 生态 —— 不改核心加能力
- **Ch11 skills**(#8):Markdown 经验 + catalog 懒加载;业务专家能写
- **Ch12 mcp**(#10):外部工具进注册表;开放协议
- **Ch13 plugins**(#9):组织边界;finance-skills dogfood 故事收尾(vertical substrate thesis)

*(书序 skills→mcp→plugins,与 §5 的 8→9→10 不同:mcp 必然性先于"组织边界"到来。)*

### Part V 规模 —— 多 Agent
- **Ch14 tasks**(#11):后台子进程,单例+状态机
- **Ch15 swarm**(#12):文件 mailbox(原子写)+ worktree 隔离;被拒绝的替代=内存 IPC
- **Ch16 coordinator**(#13):编排 + `<task-notification>` 异步回;认知陷阱:子 agent 三重防护(结构性深度上限)

### Part VI 真战场与全景
- **Ch17 接触面**(#4 完整形态 + #15 压缩):RuntimeBundle 共享、`-p` headless(为 Ch18 战役提供驱动入口)、JSON-lines 协议方向不对称
- **Ch18 benchmark in action**:swebench 适配(decision 40)+ 战役 RUNLOG 全程实录——全书最硬的战场实录章;必然性=尺(Part III)+ 规模(Part V)+ 入口(Ch17)齐备,才打得起真战役
- **Ch19 回到全景树 + 前沿**:同一棵树,每个节点读者亲手写过;开放问题=memory 语义检索、多 agent 经济学、eval 的 oracle 问题与数据飞轮;channels/bridge(#17)速写
- **附录**:auth 订阅桥(#14)、provider 兼容矩阵、环境搭建

**覆盖核对**:§5 全部 17 模块落位(1→Ch3, 2→Ch2, 3→Ch4, 4→Ch2/Ch17, 5→Ch6, 6→Ch7, 7→Ch8, 8→Ch11, 9→Ch13, 10→Ch12, 11→Ch14, 12→Ch15, 13→Ch16, 14→附录, 15→Ch17, 16→Ch6, 17→Ch19)+ 新增 prompts(Ch5)、eval(Part III)、benchmark(Ch18)。

## 3. Part 间的必然性链(树长出来的机制)

- Part I 结尾:agent 能跑了,但它会 `rm -rf`、长对话撑爆、重启就忘 → Part II 三章各认领一个
- Part II 结尾:改了 prompt/memory,pytest 全绿但行为裂化 → Part III
- Part III 结尾:有尺了,想加领域能力但不想碰核心 → Part IV
- Part IV 结尾:单上下文装不下大战役 → Part V
- Part V 结尾:runtime 强了,入口还只有 REPL → Ch17 接触面
- Ch17 结尾:尺(Part III)、规模(Part V)、入口(Ch17)齐了,拉全 harness 上真战场 → Ch18 swebench 战役 → Ch19 全景收束

## 4. Thesis 挂载点

| Thesis | 挂哪章 |
|---|---|
| harness 为强模型设计,当前模型失败是暂态 | Ch2/Ch3(循环不设状态机,LLM 当编排器;qwen-plus 实录作反面材料) |
| 两类"模型说了不算"(提议+拦截 vs 无提议资格) | Ch6 + Ch17 |
| eval ≈ software testing + judge 服务契约模糊性 | Ch9/Ch10 |
| harness = 数据飞轮传感器(使用数据是矿不是尺) | Ch18 + Ch19 |
| OH = vertical plugin substrate,不是 Claude Code 平替 | Ch14 |

## 5. 素材映射(2026-07-09 逐章核完)

D = decisions/,L = learnings/,I = docs/ideas/,W = `~/2026/aa/harness/writing/`(独立文章仓库,成文层),F = `~/2026/aa/harness/finance-skills/`(垂直 plugin dogfood 仓库)。

| 章 | 素材 | 密度 |
|---|---|---|
| Ch1 地图 | L openharness-first-principles;I from-prompt-to-loop-2026;W why-harness-2025(I 同名稿的成文版,模型能力曲线推 prompt→context→harness→loop 过阈值)、anthropic-product-logic §1(Claude Code 起源史,"build for the model in six months" 一手时间线,也是 Ch2/Ch3 strong-model thesis 的源材料);REFERENCE.md | 富+ |
| Ch2 朴素循环 | L 05/08/04;D 05(代码按 re-staging 决策新写) | 中 |
| Ch3 协议与 provider | L 02/03、phase-3-framing(LLM 调用 ↔ RPC 同构);D 02/03/04;qwen-plus 实录 | 富 |
| Ch4 工具系统 | L 06/07、phase-5b(slash commands)、phase-14(anti-substitution 战例);D 07/14/29 | 富 |
| Ch5 系统提示装配 | L 09;D 29(prompt guard 战例) | 中 |
| Ch6 安全边界 | L phase-3/10、phase-7a/7b/7c;D 15/16/21/23 —— sandbox 三个 substrate(进程/Docker/gVisor) | 富+ |
| Ch7 上下文与会话生命周期 | L phase-4/11/12/13;D 10/26/27/28 | 富 |
| Ch8 持久记忆 | L phase-10/11/16/17;D 25/36/37;I memory-first-principles —— phase-16 架构 pivot 是顶级战例 | 富+ |
| Ch9 eval substrate | D 31/35/41;I eval-first-principles、eval-craft-journal、M3 case study | 富 |
| Ch10 judge + cassette | D 32/33/34;I eval-mentor-playbook、blog-prompt-eval 系列 | 富 |
| Ch11 skills | L phase-5c/18/19;D 12/38/39(CC skill 接入两个 milestone) | 富 |
| Ch12 mcp | L phase-5;D 11;I why-protocol-standardization | 中 |
| Ch13 plugins | L phase-5d/5e/5f;D 17/18/20/24;W anthropic-product-logic(Model + Harness + Plugin 分层因果链 = thesis 成文载体);F mybank-credit-risk(完整垂直 plugin MVP:agent + 4 skills + 共享 connectors plugin + cookbook + 私有 marketplace)、01-tradeoffs(5 条架构决策,成品格式)、financial-services(Anthropic 官方仓 vendored,行业对照)、PLAYBOOK(跨行业 5-Phase 方法论)、02-dogfood-run(run 实录:判断层,2026-07-09 访谈落盘) | 富+ |
| Ch14 tasks | 仅 L phase-6(sub-agent)+ D 13 可作前身 | **薄(未 build)** |
| Ch15 swarm | 无 | **零(未 build)** |
| Ch16 coordinator | 无 | **零(未 build)** |
| Ch17 接触面 | L 04/10、phase-6plus、phase-15;D 22/30;I tui-vs-web、node-tui-next-step | 富 |
| Ch18 benchmark | D 40;benchmarks/swebench RUNLOG;L eval-flywheel-framing | 富 |
| Ch19 全景 + 前沿 | L phase-7(16+1 phase meta-retro)、working-with-ai-2026-06、talk;I eval-flywheel-framing | 富 |
| 附录 | D 00/01/06/08/09(env、脚手架、settings、boundary-contract);F PLAYBOOK(候选:"垂直行业落地 playbook"附录) | 富 |

**映射发现**:
1. **Part V 是全书唯一素材空洞**(模块 #11-13 未 build)→ 见 §8 未决问题 2
2. phases 12-13(会话快照/续接/轮转)在原大纲无家 → Ch7 范围已扩为"上下文与会话生命周期"(§2 已改)
3. sandbox 素材富到可能撑独立章(三个 substrate 的问题链),暂维持并入 Ch6,写作时再定
4. Ch13 的仓库外依赖基本消除(2026-07-09 核完 finance-skills 仓库):thesis 成文载体(W anthropic-product-logic)+ 设计层全套素材(F,见上表)都在。**三个衍生收获**:①F 的"读(官方仓)→ 造(mybank MVP)→ 沉淀(PLAYBOOK)"三步学习路径可直接作 Ch13 的章内结构;②01-tradeoffs 的六段格式(在权衡什么/两端/选择/为何/强制放弃/重议条件)是"被拒绝的替代"栏目的成品模板,**候选升格为全书统一格式**;③PLAYBOOK 是附录候选。最后的缺口 dogfood run 实录已于 2026-07-09 访谈落盘 → `F mybank-credit-risk/02-dogfood-run.md`(判断层:应用倒逼基座 / FDE 工作流亲历 / 零改动安装 = 接入事实标准 / "通"的诚实账——方法论链全通,数据虚构);机制层本就在 OH 仓 L phase-18/19 §1。**Ch13 素材闭环**
5. 意外发现一层**元素材**(boundary-contract 方法论、TDD micro-cycle、working-with-ai):可作前言或特色附录"这个 harness 是怎么和 AI 协作建成的"——2026 年语境下可能是卖点章
- **反哺约束**:以后每个模块留痕按"将来是一章"的标准写——问题链、被拒绝的替代、战场实录三样齐

## 6. 代码主线(已决 2026-07-09:re-staging)

**决策:既不全量重写,也不直接用现有仓库——从现有仓库重新编排出一条教学线。**

两个极端各自的否决理由:
- **全量重写(Crafting Interpreters 模式)**:①harness 解法半衰期 ~18 个月,按"每行代码入书"的纪律写完即过期(解释器是稳定领域,该模式的前提不成立);②否定差异化——战场实录(RUNLOG/decisions/qwen-plus/swebench)全部锚在真实仓库的真实历史,洁净重写线配不上这些故事;③solo + 12-24 月窗口,把写书变成第二个 build 项目,shipping 风险不可控。
- **纯用现有仓库**:①叙事需要的朴素/中间态从未存在(仓库按依赖序建,Ch2 朴素循环缺货);②git 历史是 phase 序,checkpoint 与章节切面对不上;③生产形态超每章代码预算(eval/ 3436 行、services/ ~3000 行);④包名与上游同名,作书的代码线身份混淆。

**Re-staging 形态**:
- 教学仓库 = 书的 companion repo,**每章一个 tag,CI 跑每个 checkpoint 的测试**,按书的版次冻结(不与主仓库同步——消掉双仓库维护风险;主仓库继续活,新版次重新 re-staging)
- 代码两类来源:**新写的只有朴素形态和少数中间态**(朴素的定义就是小,成本以天计);其余章从主仓库**搬运+裁剪**
- **裁剪规则:教学线实现问题域的最小诚实解,生产丰满度留给主仓库**(2 个 provider 不是 22 个、5 个工具不是 44 个);书内写"完整实现见 build-my-own-harness"
- 战场实录指向结构:章内代码是教学线,战例引用主仓库 decisions/RUNLOG——"真实版本在这里翻过车"可考古,比书内代码翻车更可信
- 裁完的模块带着裁完的测试走(主仓库 45K 行测试兜底),本身是书里 TDD 论点的展品

**成本与风险**:≈ 全量重写的 1/3(大头是裁剪不是创作);中途价值不归零(教学线本身是 portfolio 硬货)。剩余风险 = 裁剪时过度抛光,护栏 = 每章代码硬预算(暂定新增 ≤400 行,超了就是裁得不够狠)。

## 7. Part III 位置(已决 2026-07-09:维持在生态前,benchmark 章后置)

- **维持理由**:"改 prompt 后确定性测试够不到"在 Part II 结尾真实发生;若度量拖到 Part V 之后,读者中间四五章在没有尺的状态下改概率性行为,必然性链断。
- **让步**:原 Ch11 benchmark in action 后置为 Ch18(Part VI)——它依赖多 Agent 战役语境。后置反而补全必然性:尺(Part III)+ 规模(Part V)+ headless 入口(Ch17)齐备才打得起真战役,且全书以最硬的战场实录章收束进全景。

## 8. 未决问题

1. **REFERENCE.md 的 prompts/ 覆盖缺口怎么补**:它整份冻结,需单独决策。与书的结构无关,可随时单独处理;属主仓库文档完整性问题。
2. **Part V(多 Agent)素材空洞怎么处理**:模块 #11-13 未 build。选项 A=先 build 三模块再写 Part V;选项 B=v1 收窄,Part V 降为前沿速写。倾向 A——多 Agent 是 §5 标"认知增量最高"的 tier,砍掉伤书的完整性,且这三个模块本来就在主线 build 序上;写作不被它阻塞(Ch1-13、17-19 素材已齐,可与 build 并行推进)。
