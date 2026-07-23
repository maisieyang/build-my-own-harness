# Ch9 素材 — Eval 的两根支柱:oracle 硬度 与 record/replay 运维

> 2026-07-22,从一次"用户自己推出 oracle 定义 + 自己推出 cassette"的对话
> 沉淀。book-outline Ch9(eval substrate)的一手骨架。两根支柱:①判对错的
> 手段(oracle);②怎么跑才不烧钱(record/replay)。全部有项目实物对应。

## 〇、开篇定义(用户两周实操凝出的,可直接当章首句)

> **eval = 把 dogfood 里人工判过的"什么算对",用尽可能硬的 oracle 冻成
> 可反复跑的代码。**

这句话比教科书"eval 评估 LLM 输出质量"准,因为它说清三件事:**从哪来**
(dogfood 的人工判)、**为什么要**(反复跑)、**怎么做**(硬 oracle 优先)。
用户原话链:"最直接的是 dogfood 人工判 → 为了反复跑沉淀下来 → 对应
=/包含/存在,或者给维度让 LLM 判"——五格全中,一格没漏。

## 一、支柱一:oracle = 判对错的裁判(难点全在"没标准答案时怎么造裁判")

**oracle** 来自软件测试:一个能告诉你"这次输出对不对"的裁判。普通单测的
oracle 就是手写的 `assert`(标准答案已知,`== 5`)。**eval 的难点:模型输出
往往没有唯一标准答案**,拿什么当裁判?——设计 oracle 就是 eval 的核心功夫。

### oracle 硬度阶梯(从硬到软,能硬绝不软)

| oracle | 怎么判对 | 项目实例 | 硬度 |
|---|---|---|---|
| **`=` 精确比较** | 输出 == 已知答案 | tool_choice:工具名 == "Grep" | 最硬 |
| **轨迹不变量** | 满足"不该违反的规则"(判"没做错"非"做对") | error_feedback:下一步 ≠ 原样重发被拒调用 | 硬 |
| **keyword/存在性** | 输出含不含某必须的东西 | B2:关键事实在不在摘要里 | 中 |
| **LLM-judge** | 让另一个模型当裁判 | memory:类型该归 feedback 还是 project | 软 |
| **人工** | 人看(仅 bootstrap / 校准 judge) | dogfood 的眉头 | 最软但最准 |

**"能硬绝不软"是纪律,不是偏好**:软 oracle(judge)自己是个没校准的模型,
用它打分 = 把不确定性叠两层。所以 memory eval 五维只有一维用 judge。

### 核心手法:把开放问题重述成能用硬 oracle 的形式

B2(compact 摘要)是教科书例子。摘要**没有标准答案**(措辞无穷),直觉只能
上软 judge。但重述:**别判"摘要好不好"(软),改判"该保留的关键事实保留了
没有"(硬 keyword)**。做法 = 待压缩对话里预埋 N 个事实("用 Tavily 不用
SerpAPI"),压缩后查这些字符串在不在。**不测生成质量,测信息保真——质量
没有标准答案,但"某事实在不在"有。** 换个问法,oracle 从软变硬。这就是
"种植事实回收"绕过"无标准答案"的原理。

## 二、支柱二:eval 怎么跑才不烧钱(record/replay = cassette / VCR 模式)

### eval ≠ pytest(最关键的破除)

| | pytest | eval |
|---|---|---|
| 跑一次成本 | ~0(纯本地) | **真调 LLM = 花钱 + 有方差** |
| 确定性 | 是 | **否**(模型每次可能不同) |
| 该每次全量? | 该(免费) | **不该**(贵、慢) |

pytest 能"改一行跑全量"因为免费；eval 不能照搬。用户直觉"eval 费 token
不能没节制跑"完全对。

### 破局:两种跑法,成本天差地别(用户自己推出的 "记录 copy 下次用 copy")

| 跑法 | 调 LLM? | 成本 | 何时用 |
|---|---|---|---|
| **live / record** | 真调 | 花钱慢有方差 | 建 eval、改被测 prompt、定期体检 |
| **replay** | **不调**,读上次 cassette | **0,秒级,零方差** | 每次 commit、CI、回归 |

**record 一次,replay 无数次。** 项目里 `OPENHARNESS_EVAL_MODE=live/record/
replay` 三模式即此。用户从"费 token"约束反推出这个机制——它是 HTTP 测试
的经典 **VCR/cassette 模式**,用户不知其名而推出其形(同"从上下文有限推出
Tool Search、从无人推出 sandbox"的能力)。

### "什么时候跑什么"运维表

| 场景 | 跑什么 | 花钱 |
|---|---|---|
| 每次 commit / CI | **replay 全量**(回归门,当 pytest 用) | ❌ |
| 改了某 eval 的被测 prompt | **record 那一个** + N=4 画像 | ✅ 少 |
| 建新 eval | record + N=4 画像 | ✅ |
| 定期体检(模型/provider 可能漂) | 挑几个 live 重录,看 bar 还守不守 | ✅ 可控 |
| 改引擎/权限但没碰 prompt | 只 replay | ❌ |

### 圈定规则:靠"被测对象"而非"改了哪个文件"

- 改 skill 目录 prompt → 只重录 skill_trigger
- 改权限拒绝消息 → 只重录 error_feedback(case 种了拒绝消息)
- 改引擎循环不碰 prompt → 一个都不重录,只 replay 确认没回归

**这就是 eval 坚持"被测对象=生产件原样"的运维回报:"改了什么 → 该重录
哪个"变成可推导。** F8 footgun(replay 绑错模型)也在这条链上——被测对象
没变就不该花钱重录。

## 三、CI 的语义边界(诚实,别过度声称)

**CI 只跑 replay,永不 live**(零成本零方差)。所以 **CI 绿 = "scorer/dataset/
prompt 三者一致性没被改坏"(回归),≠ "模型行为没变"(那要人工重录+画像)。**

## 四、留白:cassette 会过期(章末抛给读者的真问题)

模型升级、provider 改默认(见 RUNLOG 节点8 思考模式),存的录音就和"今天
的模型真实行为"对不上。**replay 一直绿,绿的是"和三个月前录音一致",不是
"和今天模型一致"。怎么知道 cassette 该重录了?** 当前方案没答——属于 B3
(judge 校准)+ "定期体检"那一格。想清它,eval 运维才闭环。这是 Ch9 结尾
留给读者(和作者自己)的开放问题,比给个假答案诚实。

## 五、章 through-line

Ch9 两支柱(oracle 硬度 / record-replay)+ 上一层的 dogfood-eval 关系,构成
"eval 不是测量装置,是改进引擎"的完整论证。和 Ch5/Ch7 共享全书主线:
**从第一性约束推出机制(oracle 硬度从"软 judge 会叠错"推出;record/replay
从"调 LLM 花钱"推出),而非照抄工具。**
