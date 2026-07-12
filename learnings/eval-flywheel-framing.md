# Eval / Oracle / 数据飞轮 — 从 SWE-bench 到组织环路的认知地图

> 写于 2026-07-08。起源:SWE-bench Lite adapter(decisions/40)动工的同时,
> 从"跑这个 benchmark 到底在干嘛"出发的一条第一性探讨线,一路推到
> oracle、数据飞轮、post-training 和闭环公司的组织结构。
>
> 跟 [`training-stack-framing.md`](./training-stack-framing.md) 配套:那份回答
> "模型怎么生产出来",本份回答"**怎么知道系统好不好、以及'知道好不好'这件事
> 如何驱动模型生产**"——即那份文档 post-training 一节在 RLVR/agentic RL
> 时代的延伸,加上组织学视角。

---

## 1. 核心概念:oracle

**Test oracle = 判定"这个输出对不对"的依据。** 测试难的不是跑程序,是"跑完了谁说这算对"。

谱系(可靠性递减、覆盖面递增):

| 层级 | 例子 | 性质 |
|---|---|---|
| 确定性 oracle | 隐藏测试、数学答案、编译通过 | 机器判、不可争议、不可作弊 |
| 部分 oracle | 不变量("排序后长度不变") | 抓得住一类错,证不了全对 |
| 人类判断 | 偏好排序、验收 | 全覆盖但贵、慢、可被讨喜输出骗 |
| LLM-judge | 软 oracle | 服务 contract 模糊场景(接 eval 锚点) |

这个概念是整条线的枢纽,它在四个位置反复出现:

1. **SWE-bench 是好尺**,因为每题自带确定性 oracle(FAIL_TO_PASS);
2. **使用数据是矿不是尺**,因为对话没有 oracle,labeling = 人工补 oracle;
3. **RLVR 的 reward 就是 oracle**——哪里有确定性 oracle,哪里就能规模化 RL。
   推论:模型在 coding/数学上进步最快,不是领域更重要,是 **oracle 密度最高**。
   能力前沿的推进速度跟着 oracle 可得性走;
4. **防火墙 = oracle 必须对被测者隐藏**,泄漏的 oracle 从判卷器变成作弊面。

---

## 2. SWE-bench 作为尺:问题链

- TDD 全绿只证明确定性机器不坏;harness 的价值是"真任务端到端解不解得出"——
  prompt/上下文/loop/模型互相作用的涌现性质,即 CLAUDE.md 里的横切盲区。
- 朴素方案 dogfood:不可重复、无 ground truth、样本量 1、改动后无法比较。
- 修复需要三件事同时成立:真实分布 + 自动 oracle + 冻结题集 → 恰好 = SWE-bench。
  Lite = 300 题(django 114 / sympy 77,两家占近 2/3——分数很大程度是
  "在大型成熟 Python 仓库上的表现")。

**每题契约**:输入 = repo@base_commit + issue 原文;输出 = git diff;
判分 = docker 里 FAIL_TO_PASS 全过 + PASS_TO_PASS 不许挂。agent 全程看不到隐藏测试。

- PASS_TO_PASS 存在的原因:没有它,最优作弊解是把挂的测试断言改弱 / 把函数
  stub 成硬编码。**评一把尺好不好,先想怎么作弊拿高分。**
- 测量对象:模型固定时,分差 = harness 贡献。mini-swe-agent(只给 bash)是
  "裸奔 baseline"——低于它意味着 harness 在帮倒忙(prompt 稀释注意力 /
  权限门断掉验证回路 / compaction 压丢关键记忆)。
- agent 的自我验证 = 从 issue 自己复现 bug → 修 → 复现过 + 原测试套件还绿。
  这条回路的每一环(执行、权限、上下文预算、loop 策略)全是 harness 供给的
  ——这就是"SWE-bench 测的是 harness 不只是模型"的机制。

题目的典型形态(astropy-12907 实例):issue 结尾是 "This feels like a bug to me,
but I might be missing something?",gold patch **只改一行**(`= 1` → `= right`)。
定位难、改动小——考的是找到那一行。

---

## 3. Adapter 工作链:每个组件防一种"分数不可信 / 失败不可归因"

朴素方案(20 行 shell:喂 issue、存 diff)每暴露一个问题,长出一个组件:

| 组件 | 防什么 |
|---|---|
| instance 建模 + 本地缓存 | 数据缺字段延迟爆炸;冻结集才可重复 |
| **hidden-test 防火墙** | dataset 每行自带答案(FAIL_TO_PASS/gold patch/hints_text);泄漏 = 静默虚高 |
| prompt builder | issue ≠ 任务指令("最小修复、不动测试") |
| workspace(bare 缓存 + fresh clone) | 网络成本;跨题残留污染 patch |
| patch = `add -A && diff --cached` | 裸 diff 看不见 untracked 新文件 |
| `:(exclude).openharness` pathspec | harness 运行时产物混进 patch(多提) |
| subprocess 驱动 `oh -p` | 被测物 = shipped 产品,不是库的重新拼装;附赠隔离 |
| 批次幂等 + 双轨输出 | 中途挂不重烧;predictions=尺,records=显微镜 |
| sb-cli 云端判分 | 自建 docker 评测 = 引入"我的判卷和别人不一样"的噪声 |

两个走过机制的点,值得单独记:

**裸 diff 的系统性偏差**(Q6):`git diff` 的语义是"已跟踪文件的工作区改动",
untracked 新文件不可见。用裸 diff,所有"修复需要新建文件"的题产出
"调用了不存在的函数"的 patch——能 apply、运行时 NameError、必然判负。
偏差与修复形态相关,不是随机噪声,这就是"系统性"。

**防火墙的探测器设计**(Q8):哨兵字符串(`TEST-F2P-SENTINEL`)注入假 instance,
断言不出现在 prompt / argv / env。为什么用哨兵:断言真数据会误报(测试名可能
合法出现在 issue 里),断言字段名会漏报(只嵌内容不带标签就永远绿 = 哑探测器)。
为什么这条测试必须亲眼见 RED:泄漏在生产中零症状、分数反而更好看,
这条测试是**唯一防线**——唯一防线上的警报器必须先被证明会响
(故意注入一次泄漏,看断言炸掉)。纵深:字段私有形态存储、不进 `repr`,
prompt 面 + 进程边界面各一道闸。

直白版防火墙:**题库自带标准答案,判卷前别让考生看到**;工程版的增量全在
"怎么让'请不要'变成'不可能'"。

---

## 4. 本质抽象:给不可求导的系统建立受控实验能力

- **组合不保局部质量**:每步局部指标全绿,涌现的端到端行为照样可以坏
  (memory 检得准但挤爆上下文;工具描述好但数量稀释注意力)。坏住在接口和
  相互作用里 → 端到端分数是唯一 ground truth;但它是标量,自己不能归因 →
  **尺(分数)与显微镜(trace/records)必须配对**,缺一个就在盲测或盲改。
- **model × harness 矩阵,每格一个决策问题**:model 固定动 harness = 自己的
  净贡献(负号也是信息);harness 固定换 model = 选型 + 对"design for strong
  model"哲学的可证伪检验(换强模型分数不涨 → 瓶颈在 harness);两个都动找
  等分线 = 产品经济学(便宜模型 + 好 harness 打平贵模型 = 毛利)。
- **有限差分**:模型权重可求导,harness 不可求导。改一个变量读一次分差 =
  偏导数的数值估计。整套 benchmark 基础设施都是为了让这个读数便宜、可信、可重复。
- 收束:把 harness 工程从手艺变成实验科学。
  "What I cannot create, I do not understand" 的对偶命题:
  **What I cannot measure, I cannot improve。**

---

## 5. 数据飞轮:使用数据是矿不是尺

- 公开 benchmark 不可逆地衰变(题目渗进训练语料;Verified/Live 变体就是
  对抗污染的产物)。产品使用数据反过来:真实分布、污染免疫、**完整轨迹**
  (工具调用/报错/恢复——agentic RL 的原料,终态答案反而信息量最低)。
- 但对话天生没有 oracle,只有隐式代理信号(接受/拒绝/打断/重写/commit 后
  测试挂没挂)。**飞轮瓶颈不是数据量,是 labeling(= 人工补 oracle)。**
- **Dogfood 的精确位置:labeling 成本 ≈ 0 的那部分矿**——用户就是开发者,
  failure 和归因同一个人当场完成。但它分布偏、不可重复、无跨版本可比性,
  恰是 benchmark 的强项 → 两者是**高频粗测 + 低频精测的仪器配对**,不是新旧关系。
- 仪器栈按"成本 × 频率"分层:dogfood 每天 / 内部 eval 每次改动(偏导数读数)/
  公开 benchmark 每次模型发布。**与本仓库 D40 的分层同构**:单测每 commit、
  子集迭代期、300 全量里程碑——组织和 repo 服从同一条成本曲线。
- records.jsonl 双轨(D40.8)= 同一环路的微缩版,执行器是 harness 不是模型权重。
- 插件层推论(接 OH substrate 论题):每个垂直部署生成领域私有 eval 数据,
  通用模型飞轮归实验室,**领域判分数据飞轮归 harness 部署者**。

---

## 6. Post-training 问题链(training-stack-framing 的 RLVR 延伸)

pretraining → base model = 知识渊博的**续写机器**,有能力没行为。

1. **SFT**:模仿精选(指令→回答)样例。问题:上限是示范者;"哪个更好"是判断,无法示范。
2. **RLHF**:人排序 → reward model → 优化。问题:人类偏好是可作弊代理
   (谄媚、冗长);30 轮 agent 轨迹人类排不了序。
3. **RLVR / agentic RL(主战场)**:哪里有 oracle,哪里用 oracle 当 reward。
   模型在带工具的环境里真干任务,oracle 判分,reward 沿轨迹传回。

词汇 ↔ 实物映射:

| RL 术语 | 亲手摸过的实物 |
|---|---|
| environment | harness(工具、权限、workspace) |
| policy | 模型权重 |
| reward | FAIL_TO_PASS 判卷结果 |
| trajectory | records/trace 里的 N turns |

**SWE-bench adapter 在结构上就是一个 RL 训练环境,只差"把 reward 传回去改权重"
这一步。** 这就是"产品即 RL 环境"、以及 harness 工程师与 RL 研究员边界模糊的机制:
设计工具集/任务/判分,是同一件事的两个名字。

---

## 7. 组织学:环路怎么画成公司

环路:harness 开发 → trace → labeling → eval → 训练 → 发布 → 回到 harness。
按"谁拥有哪段"分三类:

1. **闭环公司**(Anthropic+Claude Code、OpenAI+Codex):整条环路体内。
   实验室下场做 agent 产品的第一性理由 = 把传感器焊回自己身上。
   三器官:harness 团队(小,杠杆高——一个小团队的轨迹喂整个模型组织)/
   post-training 研究(产品即 RL 环境,产品团队是训练管线的一部分)/
   共享 infra+数据+eval 平台(工业化大头)。
2. **只有模型**:环路开着,trace 落在下游产品手里。
3. **只有产品**(Cursor、本仓库的 oh):有 trace 没权重,只能做有限差分。

**规模化阶段拼执行力的翻译**:配方半公开后,瓶颈从 idea 变成**环路转速**
——每周付得起多少次偏导数测量。infra 降低单次成本,执行文化提高次数,
乘积是飞轮转速,转速是复利变量。

**Claude Code 快的因素**(按重要性):① 和模型同体(用未发布 checkpoint,
harness↔模型共同演化,竞品发布日才拿到);② dogfood 自指结构(用 Claude Code
开发 Claude Code、写码的是 Claude——产品变好→开发变快,复利直接作用于迭代速度);
③ 发布通道零摩擦(CLI,一天可转完整圈);④ 执行文化(乘数不是加数)。

**Cursor 案例(闭不了环的终局之一,2026-06 实证)**:它并非没有模型——在自己
数据最密的环段闭了小环:Tab 补全的接受/拒绝 = 毫秒级免费 label(labeling 瓶颈
天然不存在)→ 自训 Tab 模型业界最好;Composer = RL on 自家 harness。闭不上的
只有前沿通用能力(pretraining 级算力 + 研究组织),于是被上游挤毛利、发布日
才拿新模型。想继续往下闭环时撞上算力墙 → **2026-06-16 SpaceX(2 月已并 xAI)
以 $60B 全股票收购 Anysphere**,史上最大 VC-backed 收购;报道明确并购动机
含 Colossus 算力接入。终局的两面:harness-only 的宿命不是死,是**作为闭环的
关键零件被高价拼进去**——$60B 说明交互面 + 分发 + 带免费 label 的数据流是
"模型之外最稀缺的资产"。

---

## 8. 给 harness 建 eval:金字塔与 L2 缺口(2026-07-08 补)

### 8.1 业界现状:eval 不会等到它的 pytest 时刻

测试框架能收敛(xUnit/pytest),因为断言领域无关(`assert x == y` 处处同义);
eval 的核心是 oracle,oracle 领域特定 → 业界收敛成**分工**而非统一框架:

- **商品层(可买可抄)**:runner / trace 存储 / 回归看板 / 标注界面。
  Braintrust(dataset-first,$800M)、LangSmith、Arize Phoenix(OTel 原生)、
  Inspect AI(UK AISI)、DeepEval(pytest 风格);promptfoo 2026-03 被 OpenAI 收购。
  成熟团队两件套:轻框架做 CI 门 + 平台做标注/看板。
- **手工层(永远自己来)**:dataset 策展 + oracle/scorer 设计。这 20% 决定全部价值,
  买不到——它就是你对自己产品的判断本身。

结论:**水管买现成,判断永远手写。** 本仓库连水管都自建
(`src/openharness/eval/` runner/cassette/scorers)——学习项目成立;
但护城河在 dataset.yaml 和 scorers 里,不在 runner 里。

### 8.2 Eval 金字塔(与测试金字塔严格同构)

| 层 | 对应测试层 | 本仓库现状 | 节奏 |
|---|---|---|---|
| L1 组件级概率 eval | 单测 | ✅ `evals/focus_state`、`memory_decision` | 改到决策面每改必跑(cassette 回放≈免费) |
| L2 任务级端到端 eval | 集成测试 | ❌ **缺口** | 改 prompt/loop 时跑(真模型、小集) |
| L3 外部 benchmark | 发版验收 | 🔨 SWE-bench adapter(D40) | 里程碑跑 |

**L2 缺口的定义**:10–30 个策展的完整任务,跑在自己控制的迷你仓库里,带硬 oracle
("修完后该测试要过"、"最终文件含 X"、"≤ N turns 收束")。

**必须有 L2 的理由 = §4 组合不保局部质量**:focus_state 推断再准,它和
compaction / loop 策略的相互作用坏了,L1 看不见、L3 太贵太粗。
且 L2 的题目分布由自己定义——oh 的真实用途(skill 执行、memory 读写、多轮任务),
不是 django 修 bug;**对 OH substrate 命题而言 L2 比 L3 更贴产品**。

### 8.3 六个设计因素

1. **Oracle 硬度优先**:每 case 先问最硬 oracle 是什么;确定性判据(测试过没过 /
   文件含不含 / 工具调没调 / 轮数超没超)优先,LLM-judge 只留给 contract 模糊的面
   (M3 锚点;focus_state 已实践:keyword scorer 在前,judge 兜模糊 capability)。
2. **覆盖按决策面算,不按 case 数算**:概率层 = system prompt / 工具描述 /
   compaction / memory 检索 / loop 停止策略,每面至少一组探针。
   dataset_card 的 "Not yet covered" 节 = 决策面 map 意识的实物。
3. **噪声是一等公民**:N×模型稳定性画像(focus_state Day 1 已做);
   75% 稳定的 case 不能设 100% gate;pass bar 是统计量(N 次 ≥ k);
   子集大小按要探测的效应量倒推;±1 case 波动不解读。
4. **成本结构分层**:L1 靠 cassette 免费重跑;L2/L3 轨迹会分叉、没法 cassette,
   成本控制靠子集 + 节奏。
5. **Gate 语义 ≠ 测试 gate**:测试 100% 或红;eval gate = 阈值 + 增量
   ("不低于上次 −1 case")。触发节奏绑改动面(eval skill 的触发条件即此)。
6. **失败 → case 飞轮**:TDD "bug → 回归测试" 的概率层对应物——每个归因过的
   失败 transcript 沉成一个 L1/L2 case。SWE-bench records 归因出
   "compaction 丢上下文" → L1 加 compaction case;"过早宣布完成" → L2 加任务。
   **dataset 靠现实生长,不靠想象编题。**

### 8.4 SWE-bench 在方案中的位置

L3 塔尖。给 L1/L2 给不了的三样:外部可比性(与 Claude Code / mini-swe-agent
同尺)、公信力(分数可写进 README/简历,自建 eval 分数不能)、全链路覆盖
(没想到要测的相互作用也在内)。做不了的恰是 L1/L2 的存在理由:太贵不能日常迭代、
标量不能归因、分布不是自己的。**L3 是低频校准的外部标尺,不是日常方向盘。**

---

## 9. 一句话索引

- Oracle = "谁说这算对"的那个"谁";eval / RLVR / 飞轮的瓶颈都是
  "oracle 从哪来、多硬、藏没藏好"。
- Benchmark = 有确定性 oracle 的冻结题集;跑它 = 给概率层一把改动后会移动的尺。
- 防火墙 = oracle 对被测者隐藏的结构性保证;唯一防线的探测器必须先见 RED。
- 尺 + 显微镜配对:端到端分数判"变没变",trace 判"为什么"。
- 使用数据 = 矿;labeling = 人工补 oracle;dogfood = labeling 免费的矿。
- 产品即 RL 环境;adapter ≈ RL 环境 − 权重更新。
- 组织 = 环路画成人的形状;规模化阶段的执行力 = 环路转速。
- Eval 不收敛成 pytest:断言领域无关、oracle 领域特定;水管买现成,判断永远手写。
- Eval 金字塔:L1 组件(已有)/ **L2 任务级(缺口,下一块)** / L3 benchmark(塔尖标尺)。
- 失败 → case 飞轮 = "bug → 回归测试" 的概率层对应物;dataset 靠现实生长。
