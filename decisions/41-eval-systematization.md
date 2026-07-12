# Decision 41 — Eval 系统化:枚举校验 + 金字塔接线 + 优先级 reshuffle

> Created 2026-07-08 · 中文
>
> 本 doc 按 [D35.10](./35-eval-coverage-map.md) 的演进规则行事:枚举扩展与
> 优先级 reshuffle 不在 D35 原地改,开新 doc 引用之。
>
> 配套读物:
> - 底图:[`35-eval-coverage-map.md`](./35-eval-coverage-map.md)(7 决策面枚举 + 三声明头 + substrate 不变量)
> - 概念框架:[`learnings/eval-flywheel-framing.md`](../learnings/eval-flywheel-framing.md)(oracle 谱系 §1、eval 金字塔 §8)
> - L3 标尺:[`40-swebench-adapter-boundary.md`](./40-swebench-adapter-boundary.md)
> - 代码证据:2026-07-08 全库决策面扫描(本 doc §一,file:line 均已核对)

---

## 〇、Why now

三个驱动同时到位:

1. **开发者的真实疑问**(覆盖牵引):TDD 测试完备,但"怎么证明这个决策面
   符合预期"在 tool / skill / permission 等面上没有答案——写码时的疑问就是
   eval 的需求清单。
2. **D40 落地补齐了 L3**:D35 时代 #7 E2E 只有 dogfood;现在 SWE-bench
   adapter 给了外部标尺,金字塔三层第一次齐了两层,中间的空缺(L2)和
   L1 的未覆盖面变得可定位。
3. **一个月的代码演进**:D35.1 枚举是 2026-06-04 从当时代码反推的;此后
   loop-runtime(verify gate / decompose / repair)、swebench、memory pivot
   收口都动了决策面版图,需要校验。

判据沿用 D35 §1.1,今天探讨中凝出的等价表述一并记录:
**"凡是经过 LLM 输出翻译的用 eval;确定性代码用 TDD。"**
推论:同一个模块同时住着 TDD 面和 eval 面(permission 的拦截逻辑是 TDD,
被拒后的模型行为是 eval)——覆盖计数单位是决策面,不是模块。

---

## 一、枚举校验(2026-07-08 代码证据)

全库扫描确认:**D35.1 的 7 面分类法成立,无面需要 deprecate**;但 #1 需要
显式细分,#7 有新 consumer,另有一族横切契约需要登记。证据表(★ = 本次
新识别、D35 时代不存在或未见的点):

| D35 面 | 子面 | 消费什么 | 关键 ref | eval 现状 |
|---|---|---|---|---|
| #1 secondary pass | B1 focus_state 推断 | 单发 JSON `{goal, next_step}` | `services/focus_state.py:137` | ✅ evals/focus_state |
| | ★B2 L4 compact 摘要 | `<summary>` 9 槽,**成为模型对全部前史的记忆** | `services/compact.py:358` | ❌ |
| | ★B3 semantic verify judge | 单行 `{"reason","score"}`,驱动 verify gate | `verification/semantic_gate.py:79` | ❌ |
| | ★B4 goal decomposer | JSON 数组 sub-goals | `services/decompose.py:48` | ❌ |
| #2 tool 选择+input | A3 tool 名查找 | LLM 给的 name 字符串,miss → is_error 回喂 | `engine/query.py:512` | ❌ |
| | A4 input 校验 | LLM 给的参数 dict,Pydantic 拒 → is_error 回喂 | `engine/query.py:517` | ❌ |
| | A1 tool-call JSON 组装形态 | 流式拼出的 arguments 串,截断 → 中止 turn | `api/translation.py:319` | ❌(观察项) |
| #3 agentic 主循环 | A2 stop_reason → 停止判断 | `!= "tool_use"` 即视为任务完成 | `engine/query.py:378` | ❌(L3 间接) |
| | ★A5 permission 被拒后行为 | `"permission denied: ..."` is_error 回喂,期望换方案不重试 | `engine/query.py:521-537` | ❌ |
| | A6 tool error 再消费 | is_error result + "most errors are recoverable" 契约 | `engine/query.py:571-591` | ❌ |
| | A7 max-turns / 截断后行为 | 带洞历史的再消费 | `engine/query.py:279-349` | ❌(观察项) |
| #4 inline 决策 | C3 memory WRITE | 是否 Write + frontmatter + Edit 索引两步契约 | `prompts/memory.py:37-157` | ✅ evals/memory_decision |
| | ★C4 memory READ | 扫索引自选 Read(Phase 16 后 harness 不再代排) | `prompts/memory.py:132`,`memory/relevance.py:1-14` 已退役 | ❌(partial) |
| #5 plugin/skill | C1 LoadSkill 触发 | 模型自决调不调、调哪个 slug;miss → 目录 is_error | `tools/load_skill.py:51-82` | ❌ |
| #6 sub-agent | C2 SpawnAgent 委派+收割 | LLM 写的子任务 prompt;子 agent 最终文本 | `tools/spawn_agent.py:114-197` | ❌ |
| #7 E2E | swebench L3 | 全栈端到端,patch 由 git diff 提取,**不引入新解析面** | `swebench/runner.py` | 🔨 D40 |

**边界确认(维持 TDD 侧,非决策面)**:slash 命令/skill 触发是用户键入
`/name` 的确定性匹配(`commands/expand.py:51`)——#5 的 eval 对象只是
LoadSkill 的模型自决路径;hook 链是确定性调度(`hooks/executor.py`);
permission 规则求值本身确定(`permissions/checker.py:141`),只有 A5 的
"拒绝消息被模型消费后的行为"属 eval。

### D41.1 — #1 细分为 B1-B4 四个独立 capability(ratify)

**Chosen**:D35.1 #1 "secondary LLM pass" 从单行细分为 B1-B4 四个子面,
各自独立 dataset + scorer + capability claim。D35.7 反模式 #5("同一 eval
回答多个 claim")直接适用:focus_state eval 不得被扩去测 B2-B4。

**Rationale**:四者 prompt 不同、输出 schema 不同、失效模式不同
(B2 静默劣化 vs B3/B4 fail-closed)。尤其 B2:摘要坏了会**静默成为模型
的全部记忆**,是 fail-open 的——与 B3/B4 的 fail-closed 风险等级不同。

### D41.2 — 系统 prompt 行为契约登记为"case 源",不设第 8 面

**Chosen**:扫描识别出 11 条 prompt 行为契约(错误自适应、不越权探索、
no-internet 反替代、memory 两步写、trust-observation-over-memory、
verify-before-done、ask-don't-explore、judge 抗注入等,refs 见
`prompts/system.py:79-260`、`prompts/memory.py:37-143`)。**不新增
"prompt 契约遵循"决策面**——每条契约锚定到它显形的既有面上,作为该面
dataset 的 case 来源(错误自适应 → A5/A6;no-internet 反替代 → #2;
memory 契约 → #4;verify-before-done → 已在 focus_state T7)。

**Rationale**:独立的"prompt 契约 eval"就是 D35.7 反模式 #1(monolithic)
换个名字;契约只能在具体决策面上被违反,归面存放才有清晰 capability claim。

---

## 二、金字塔接线(今日概念 → D35 底图)

### D41.3 — 三层节奏 ratify

| 层 | 内容 | 节奏 | 成本 |
|---|---|---|---|
| L1 | 单决策面探针(cassette 可回放) | 改到该面每改必跑;回放可进 CI | ≈0(回放)/低(重录) |
| L2 | 自有分布端到端小任务集(≤10 起步,迷你仓库+硬 oracle) | 改 prompt / loop 策略时 | 中 |
| L3 | SWE-bench(D40) | 里程碑 / 发版 | 高 |

D35.5 "按主路径驱动"原则保留;金字塔给它加的是**层间分工**:L1 归因、
L2 相互作用、L3 外部可比。#3/#6 这类多轮面在 L1 只测"给定上下文的单步
决策",真轨迹归 L2。

### D41.4 — Oracle 硬度纪律(每 case 先问最硬 oracle)

阶梯:`=` 判 → 轨迹不变量 → keyword/rubric → LLM-judge → 人工
(人工只做 bootstrap 和 judge 校准,不做常设 gate)。本次各面的最硬 oracle:

- #2 tool 选择:`=`(期望 tool 名);input 构造:字段级 `=` / Pydantic 过
- #5 skill 触发:`=`(LoadSkill 调没调、slug 对不对)
- A5 被拒后行为:**轨迹不变量**——下一个 call ≠ 原样重试;同一被拒 call
  不重复 ≥2 次;未过早放弃(有后续动作)
- A6 错误恢复:不变量(同上)+ rubric 兜"恢复得优不优雅"
- B2 compact:**种植事实回收**——在待压历史里埋 N 个关键事实,判摘要
  含不含(`=`/keyword 可判!)+ judge 兜连贯性
- B3 judge 的 meta-eval:**金标转写**——人工标好 verdict 的 transcript 集,
  判 judge 输出与金标一致率(`=` 可判;含抗注入对抗样本)

### D41.5 — 参照模型与 gate 语义

**Chosen**:每份 dataset_card 三声明头(D35.3)增补第 4 项声明:
**Reference policy** = 参照模型(当前:qwen-max 或 DeepSeek 最强档,
每 eval 锁定一个并写明)。pass bar 是统计量(N 次通过 ≥ k,按稳定性画像
定,禁止给 <100% 稳定的 case 设 100% 门)。**弱模型上的红 = 信息,不是
gate 红**(design-for-strong-model 的 eval 操作化)。跨模型比较仍受 D35.8
约束,不因本 doc 放松。

### D41.6 — 失败 → case 飞轮(dataset 生长规则)

**Chosen**:dataset 靠现实生长:每个归因过的失败(dogfood、SWE-bench
records.jsonl、L2 失败)必须沉成对应面的一个 case,归入该面 dataset 并在
dataset_card 标注来源。禁止凭想象批量编题扩量。

---

## 三、优先级 reshuffle(D35.5 → D41)

### D41.7 — 新优先级表(ratify)

| Priority | 面 / 子面 | 层 | 最硬 oracle | 变动理由 |
|---|---|---|---|---|
| **P0** | #2 tool 选择 + input 构造(A3/A4) | L1 | `=` | D35.5 原 P1;开发者主诉求 + oracle 最硬最便宜,先立范式 |
| **P0** | #5 skill 触发(C1) | L1 | `=` | **D35.5 原 P3 → P0**:触发条件已到——skills 已是真实用户路径(finance-skills dogfood 2026-06-07,OH substrate 命题的主菜) |
| **P1** | A5 被拒后行为 + A6 错误恢复(#3 单步探针) | L1 | 不变量 | 扫描定级"prime uncovered";系统 prompt 契约 #1 的显形面 |
| **P1** | B2 compact L4 摘要 | L1 | 种植事实回收 | ★新识别;fail-open、load-bearing(模型的全部记忆),风险最高的未覆盖面 |
| **P2** | B3 semantic judge meta-eval | L1 | 金标一致率 | verify gate 的裁判自己没被校准过;金标集顺带可做 judge 通用校准 |
| **P2** | #7 L2 任务集(≤10) | L2 | 任务级硬 oracle | **开题等 SWE-bench 小批失败归因**(D41.6),不凭空编 |
| P3 | #6 sub-agent(C2)、C4 memory READ、B4 decomposer、A1/A2/A7 | — | — | 维持等触发;A2 停止判断先靠 L2/L3 间接观察,若归因显示是主要失败形态再升级 |

**Rationale(#5 的 P3→P0 是最大变动,单独说明)**:D35.5 给 #5 定 P3 的
理由是"等真有用户路径再说"。触发条件已满足:finance-skills dogfood 验证
了 OH 作为 SKILL.md substrate 的定位,skill 触发是这个命题的第一决策面;
且其 oracle 是 `=` 判,建设成本与 #2 同级。这是 D35.10 规则 3 的标准
reshuffle,非推翻 D35.5 的方法论。

### D41.8 — Anti-scope

1. ❌ 不为 A1(JSON 组装形态)、A7(截断后行为)单建 eval——先作为
   L2/L3 records 的观察字段,有归因证据再立项
2. ❌ 不做 cross-model 比较(D35.8 前置未满足)
3. ❌ 不在本轮扩 substrate(`src/openharness/eval/`)——D35.6 不变量:
   新面只加 consumer 目录 + Scorer 实现
4. ❌ 不承诺 P3 项会建(同 D35 anti-scope)

---

## 四、Acceptance(P0 首个落地,范式基准)

P0 第一个 eval(`evals/tool_choice/`,建议名)必须:

1. dataset_card 以**四声明头**开头(D35.3 三项 + D41.5 Reference policy)
2. capability claim 显式引用本 doc "#2 / A3+A4"
3. 复用 substrate(`from openharness.eval import ...`,零新 framework)
4. dataset ≥8 case,覆盖:正确 tool 选择 / 近义 tool 辨析(Grep vs Bash-grep)
   / 参数字段构造 / 未知 tool 自纠(A3 error 回喂后行为)
5. scorer 以 `=` 判为主;先跑 N≥4 稳定性画像再定 pass bar
6. 全仓质量门不破(pytest / mypy --strict / ruff)

#5 skill 触发 eval(`evals/skill_trigger/`)同规格跟进,two P0 完成后
回本 doc 勾验收,再开 P1。

---

## 五、References

- D35 — 底图(枚举 / 三声明 / substrate 不变量 / 演进规则)
- D31-D34 — substrate 五阶段
- D40 — SWE-bench adapter(L3)
- `learnings/eval-flywheel-framing.md` — oracle 谱系与金字塔的概念推导
- 2026-07-08 决策面扫描 — 本 doc §一证据表的来源
