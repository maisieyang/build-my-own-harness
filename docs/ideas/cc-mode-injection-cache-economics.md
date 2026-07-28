# 调研 — 模式切换不重组 prompt:CC 的"前缀冻结 + 消息注入"与缓存经济学

> 2026-07-24,D48 落地当日的追问。问题域来自作者假说:"/plan、default、
> /goal 每个模式 system prompt 和 tools 都不一样,`/` 会引起 system 的
> 重新组装,公用的部分是会话。"逆向验证结论:**方向对、机制反**。
> 双源:CC 2.1.218 二进制 strings + 本会话亲身运行时(作者当日在同一
> session 里切过 plan mode、设过 /goal,注入格式为一手观察)。
> 喂 Ch5(prompt 组装)× Ch17(接触面)交叉;上承
> [cc-goal-design-reverse.md](./cc-goal-design-reverse.md)。

## 一、发现:前缀冻结,姿态走消息流

| | 作者假说 | CC 实际 |
|---|---|---|
| 公用部分 | 会话 | **system prompt + 工具表 + 会话**(三者都是缓存资产) |
| 每模式不同 | system prompt + tools | **注入的 meta 消息**(system-reminder)+ 权限层姿态 +(goal)hook |
| `/` 命令做什么 | 触发重组 | 追加消息 + 拨权限/hook 开关,**前缀一个字节不动** |

实现证据(strings 行号锚定 2.1.218):

1. **plan 模式 = 注入的 system-reminder**(132198):"Plan mode is
   active. ... you MUST NOT make any edits ... This supercedes any other
   instructions you have received."——以消息形式进对话,system prompt
   不动。退出同理(338981:"## Exited Plan Mode ...")。作者当日在
   session 内亲收同款文本,一手验证。
2. **渲染器代码本体**(425419):`output_style` → "X output style is
   active" / `critical_system_reminder` / `plan_mode_exit` ——全部走
   同一 attachment 渲染管线,统一 `isMeta: true`,作为对话内消息下发。
   **模式、输出风格、goal 指令,同一机制:往会话尾部追加带标记的
   meta 消息。**
3. **工具表不随模式换**:plan 模式下 Edit/Write 的 schema 仍在工具表
   (作者 plan mode 中亲见),钳制在权限层(调用被拒)+ 指令层;
   /goal 更彻底——它是 Stop hook,连权限都不碰。

## 二、为什么:缓存经济学决定注入位置

Anthropic prompt caching 按**前缀**命中;system prompt + 工具定义 =
每次请求的最长公共前缀。`ephemeral_1h_input_tokens`(144777)证明缓存
计量在核心计费路径上。推论:

> **重组 system prompt = 整条对话的缓存前缀作废**,下一 turn 全价重付
> 几万 token;往对话尾部**追加**一条 meta 消息,只付增量。

所以 CC 的架构纪律是:前缀(system prompt + 工具表)尽最大可能字节
稳定,一切运行时姿态变化从对话尾部进。**模式不是"换了一个 agent",
是"同一个 agent 收到了一条改变行为的信"。**system-reminder 机制看着
像 UI 细节,实际是一个成本架构决策。

顺带解释了两件旧观察:

- 为什么 CC 文档说 plan mode "纯权限切换、无 prompt 注入"
  (cc-goal-design-reverse §二)——注入有,但不在 system prompt 层,
  文档口径与实现层各说了一半;
- 为什么 /goal 的指令、hook feedback、日期变更提醒长得都像一类东西
  ——它们就是一类东西(meta 消息管线)。

## 三、对 OH 的照妖镜 + v2 方向

**OH 的 D47/D48 恰好实现了作者假说的方案**:`turn_system_prompt` 每
turn 拼接 PLAN_MODE_PROMPT_SECTION / goal section——就是"重组"。当前
不算错:OH 本来就每 turn 重建 system prompt(memory 注入同构),qwen
系隐式缓存也不是显式管理的资产。但埋着一个 v2 方向:

- **当 provider 的前缀缓存成为显式成本杠杆时,姿态注入应从 system
  prompt 搬进消息流**——OH 已有消息注入先例(goal kickoff/feedback
  的 `[goal ...]` 框架、goal 哨兵),plan 姿态同样可走该通道;
- 搬迁的前置是"OH 是否吃到前缀缓存"——先量测(qwen 隐式缓存命中率),
  不为形似先搬(与 D48.3"不为对齐弃校准"同款判断)。

## 四、论点候选(一句话 thesis)

- **姿态的注入位置不是风格选择,是缓存经济学的函数**:前缀缓存越贵,
  行为差异越该从对话尾部进。
- 模式切换的两种实现哲学:"换 agent"(重组前缀)vs"给同一个 agent
  写信"(追加消息)——CC 选后者,选择的驱动力是账单不是美学。
- 逆向方法论:文档口径("无 prompt 注入")与实现层(有,但在消息层)
  可以各对一半——**层错了,结论就反了**;验证要钉到具体注入位置。
