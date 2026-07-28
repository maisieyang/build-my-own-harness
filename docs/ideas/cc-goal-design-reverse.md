 # 调研 — CC /goal 的设计与实现（逆向 + 官方文档双源核对）

> 2026-07-24。问题域来自 D48（REPL 内 session 级 /goal）规划期作者的疑问:
> "我看了 plan,其实有疑问的"——本地 plan 与 CC 的分歧点需要先把 CC 的
> 设计彻底查清再裁决。双源:①逆向 CC 2.1.218 arm64 二进制(strings 提取,
> 实现层);②官方文档 goal.md / hooks.md(设计口径层)。两源互相印证。
> 喂 Ch17 + D48 boundary doc 前置。

## 〇、问题域(这次调研为什么发生)

D48 plan v1 已按对话推演成形(四点光谱第 3 点补位:续跑式条件循环),
但它是在"CC /goal 的外部行为"认知下设计的——判官怎么被喂、续跑消息
以什么身份进对话、上限怎么设,这些内部机制当时靠合理推断。作者看完
plan 后提出疑问,要求先调研 CC 的真实设计。教训一条:**对标设计前,
参照系的机制层要先挖到实现粒度,推断会在恰好最关键的缝上出错**(本次
续跑注入语义即是——推断为注入用户消息,实为 hook feedback)。

## 一、实现层(逆向 2.1.218 二进制)

**核心:`/goal <condition>` = `sessionHooksRegistry.add(sessionId,
"Stop", "", {type:"prompt", prompt: condition})`——/goal 不是独立
子系统,是 hooks 系统的语法糖**;判官是通用 prompt-hook 求值器
(hooks 文档"Prompt Hook - Evaluates a condition with LLM")。

- 判官 prompt 原文:"Based on the conversation transcript above, has
  the following stopping condition been satisfied? **Answer based on
  transcript evidence only.** Condition: ..."
- 求值器输出:结构化工具调用强制单次("You MUST call this tool
  exactly once"),schema `{met: bool, reason: string}`;另有 JSON
  文本双路径 + schema 校验失败处理。
- 续跑注入前缀:`"Stop hook feedback: "`——判官 reason 以 hook
  feedback 身份进对话流(与 system-reminder 类字符串同区),**不是
  伪造用户消息**。
- 三值判决痕迹:"Prompt hook condition judged **impossible**"——
  判官可宣告条件不可达成(文档未记载,实现存在)。
- 状态:`activeGoal = {condition, iterations, setAt, tokensAtStart}`;
  goal_status 哨兵 attachment 写进消息流,`restoreGoalFromTranscript`
  从 transcript 重建——**不另设持久层,对话流是唯一事实源**。
- 上限:`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` env(未文档化兜底)+
  `stop_hook_active` 标志传给 hook 供自限。
- 清除别名:clear/stop/off/reset/none/cancel。
- 门槛:仅 trusted workspace;hooks 被策略禁用时不可用(显式报因)。

## 二、设计口径层(goal.md / hooks.md,v2.1.139+)

- **官方设计理由原文**:"completion is decided by **a fresh model
  rather than the one doing the work**"——运动员/裁判分离是明文初衷。
- 接口:条件 ≤4000 字符;裸 `/goal` 显示条件+耗时+turn 数+token
  花费+判官最近 reason;状态栏 `◎ /goal active`;达成后自动清除,
  transcript 记 achieved 条目(duration/turns/tokens)。
- 续跑:判"否"→"includes the reason **as guidance for the next
  turn**";hooks.md Stop block 语义:"Claude receives the reason...
  sees the reason in the conversation",turn 不算完成。
- 判官:默认小模型(Haiku),**不暴露覆盖**;输入=条件+完整对话;
  **判官无工具**——证据必须由运动员放进 transcript("跑一遍测试让
  结果落进 transcript 给判官读")。评估成本"typically negligible"。
- 上限:**文档无硬上限**;官方最佳实践=把界限写进条件("or stop
  after 20 turns",判官读对话数 turn)。
- 组合:"**A goal doesn't change permissions**"——goal 消 per-turn
  人工续接,auto mode 消 per-tool approve,互补(作者 dogfood 体感
  "还是要 approve"的机制解释);headless `-p "/goal ..."` 单次调用
  跑到完成;resume 恢复条件但重置计数。
- 条件书写学三要素:一个可测量终态 + 一个明说的验证方法("`npm
  test` exits 0")+ 必要约束("不改其它测试文件")。

Sources: https://code.claude.com/docs/en/goal.md ·
https://code.claude.com/docs/en/hooks.md · 二进制 2.1.218 strings。

## 三、对照表(CC vs D48 plan v1)+ 完全对齐 CC 的可行性评估

作者裁决(2026-07-24):**认同 CC 的设计,完全按 CC 对齐**。逐行
可行性与工作量(S≈1-2 文件,M≈3-5 文件):

| # | 设计点 | CC | D48 v1 | 对齐改法 | 可行性 | 工作量 |
|---|---|---|---|---|---|---|
| 1 | 续跑注入 | reason 作 hook feedback 进对话,不冒充用户,UI 不回显为用户输入 | pending_input 注入用户口吻消息(会回显 `>>> `) | 不走 pending_input 回显路径:反馈消息(`[goal checker] not met: <reason>` 框架)直接 append 进 history + 立即发起下一 turn;UI 显示 `(goal not met — continuing: <reason>)`。API 层仍是 user 角色消息(Anthropic API 只有 user/assistant,CC 的 system-reminder 底层同样是带标记的 user 消息——对齐的是**身份框定与 UI**,不是角色字段) | ✅ 无障碍 | S |
| 2 | 判官模型 | 小模型硬默认 | 主模型 | 增 settings 字段 `goal_judge_model: str \| None`(照抄 focus_state_model 惯例,settings.py:250);默认 None=主模型,用户可配小模型。⚠️ OH 无"per-provider 小模型"概念,硬默认小模型会踩 qwen-plus 类弱模型不可靠的坑——**建议默认主模型、可配降档**,与 CC 的差异声明为 provider 现实约束 | ✅ | S |
| 3 | 判官输入 | 消息数组 | 渲染文本 | 若完全照搬:新判官调用路径(summarize 吃 messages + 条件问句)。**代价真实**:放弃防注入定界符包裹 + verify_judge 元评估是按文本 transcript 格式校准的,换格式=换了一个未校准的判官。行为层两者等价(判官都只见对话内容)——**建议按"行为设计对齐、实现细节保留渲染文本"处理**;若坚持照搬,需重跑 verify_judge 校准 | ✅ 但有资产代价 | M(含判官变体+校准影响) |
| 4 | 上限 | 界限写进条件 + env 兜底,无硬常量 | 硬常量 10 | 常量改为 settings/env 兜底上限(`OPENHARNESS_GOAL__BLOCK_CAP` 风格,默认放宽如 25);`/goal` 设定时 echo 提示"建议在条件里写 bound(如 or stop after 20 turns)";渲染 transcript 标记 turn 边界让判官能数 turn | ✅ | S |
| 5 | 裸 /goal 状态 | 条件+耗时+turns+tokens+最近 reason | 只显示条件 | session 状态扩为 `{condition, iterations, set_at, tokens_at_start, last_reason}`;tokens 用 estimate_message_tokens 基线差值近似(CC 的 tokensAtStart 同构;若要精确 usage 账本则升 M) | ✅ | S |
| 6 | 达成记录/恢复 | transcript 哨兵条目,resume 重建 | echo+清除,不持久化 | goal 设定/达成/清除各 append 一条带标记的哨兵消息进 history(snapshot 自然持久化);`--resume` 时扫描恢复活跃 goal。⚠️ OH 无"不发给模型的 attachment"消息类型,哨兵会进模型上下文(无害,可视作显式状态声明);要过滤则需动 engine/messages,升 M | ✅ | M |
| 7 | 权限正交 | 明文不动权限 | 同构 | 无需改动 | — | 0 |
| 8 | 判官无工具 | 是 | 是 | 无需改动 | — | 0 |
| 9 | 架构分层(goal=Stop hook 语法糖) | hooks 系统上的 prompt-hook | REPL 内联循环 | 完全复刻需给 OH hooks 增加 Stop/TurnEnd 事件 + LLM prompt-hook 类型——行为不可见的内部分层,**列 v2 重构方向**,v1 不做 | ✅ 但面大 | L(v2) |
| 10 | 三值判决(impossible) | 实现有、文档无 | 二元 | 判官 schema 加第三值可跳出循环——动被校准的判官 schema,**列 v2**(与 #3 同理) | ✅ | M(v2) |

**总量评估**:v1 范围完全对齐(#1/#2/#4/#5 + #6)≈ 在原 T0-T4 之上
**新增约一个同等规模的工作段**(原计划约一个专注 session,对齐后约
1.5-2 个);#3 若坚持照搬另加 M 并触发判官重校准;#9/#10 留 v2。
无不可行项;两处资产代价(#3 校准资产、#2 弱模型风险)已标注。

## 四、留档判断

- 对标设计的方法论教训:参照系挖到实现粒度再定分歧,推断会在最关键
  的缝上错(续跑注入)。
- "完全对齐"要区分**行为层设计**(用户可见语义,全采纳)与**实现层
  选择**(判官输入格式、内部分层——OH 有被校准的资产时,行为等价
  即可,不为形似弃校准)。
- CC 把防跑飞的责任推给条件语言("or stop after 20 turns")而不设
  文档化硬上限——把安全参数交给自然语言接口,这个设计胆子比 OH 的
  fail-closed 品味大;OH 保留 env 兜底但放宽默认,取中间值。
