# Eval 手艺 journal —— stage-by-stage lab notebook

> 写于 2026-06-02 起 · 中文 · 持续 6 个月
>
> 配套读物：
> - 方法论理论：[docs/ideas/eval-mentor-playbook.md](./eval-mentor-playbook.md)
> - 决策方案对比：[docs/ideas/eval-first-principles.md](./eval-first-principles.md)
> - 项目方法论：[CLAUDE.md](../../CLAUDE.md)

---

## 这本 journal 是什么 / 不是什么

**是什么**：你在 OpenHarness 上做 eval 这 6 个月的 **lab notebook**。每
个 stage 三段（Before / During / After），即时记，**不是事后回忆**。

**不是什么**：
- 不是 retro（retro 是 stage 完工后归档；journal 是过程中的"心电图"）
- 不是 spec（spec 给 "做什么"；journal 记 "做的时候我在想什么"）
- 不是 playbook（playbook 是理论；journal 是手艺）

**为什么要这本 journal**：playbook 跟你说"专家会归类 failure mode" ——
那是理论。**真实情况是你跑完 spike 的瞬间脑子里冒出来的"咦这个 case
LLM 答的好像还行但 next_step 怪怪的"** —— 这种感觉不写下来 30 分钟
就忘。一年后回头看你以为自己是顿悟，其实你只是丢了 80% 的过程
细节。

---

## 写作 protocol —— 三段格式

每个 stage 三段，**顺序不能换**：

### Before（开始前 5-10 分钟写）

回答 5 件事：

1. **目标**：这个 stage 我要交付的 capability 是什么？一句话
2. **Scope / Anti-scope**：明确做什么 / 明确不做什么
3. **已 lock 的 ratification**：链接到 chat / boundary doc / playbook
4. **预期我会观察到什么**：写下你**猜测**这个 stage 跑完会看到的 3-5 件事
5. **完工后我要能回答的题**：playbook 里那个 stage 的 self-test 题贴
   过来

**关键**：第 4 件事是 **prediction**。**预言写下来，spike 完打脸了你才
学得到东西**。不写预言、跑完 "嗯还行" 一带而过 → 没学到。

### During（实操过程即时记）

只记两类东西：

- **意外**："咦这个跟我 Before 第 4 条预测的不一样"
- **临时决策**："我本来打算 X，但实操时发现 Y，临时改成 Z"

**不记**：流水账（"我打开了 IDE，我跑了命令" —— 这些 git log 里有）。

### After（完工后 30 分钟内写）

回答 4 件事：

1. **Self-test 答案**（playbook 那个 stage 的题）
2. **Before § 4 预言 vs 实际**：哪几条对了？哪几条打脸？打脸的为什么？
3. **下一个 stage 我应该带着什么 dataset / scorer / model 改动一起开**
4. **如果重做这个 stage 我会怎么改 process**（meta-learning）

---

## Stage 0 — focus_state spike (5 case × 2 scorer × LIVE)

> 时间：2026-06-02 开
> 文件：[scripts/spike_focus_state_eval.py](../../scripts/spike_focus_state_eval.py)
> 状态：Before by mentor 写好；During / After 等 user 跑完填

### Before  (by Claude as mentor, 2026-06-02)

**目标**：拿到第一个 focus_state eval 结果，培养 "焦点状态推断准不准"
的第一手体感。**这一步的产出不是代码，是 understanding**。

**Scope**（in）：
- `scripts/spike_focus_state_eval.py` 单文件
- 5 hand-picked case（明确 / 模糊 / tool-only / multi-step / adversarial 各 1）
- 2 inline programmatic scorer（`parse_ok` + `goal_keyword_match`）
- LIVE 真打 qwen-plus（共 5 次 API call ~ 0.05 USD）

**Anti-scope**（明确不做）：
- 不抽象 Dataset / Scorer Protocol（Stage 1 才做）
- 不上 LLM-as-judge scorer（spike 全程程序化够用）
- 不上 cassette mode（spike LIVE 直接跑）
- 不动 `src/` / `tests/` / `services/focus_state.py` 一行（严格 0 diff）
- 不写 CLI 子命令、不进 pyproject deps

**已 lock 的 ratification**（来自 chat）：
- spike 走 `scripts/`（不进 src/ / tests/）
- `services/focus_state.py` 严格只读
- model 默认 qwen-plus（不 qwen-max，self-preference bias 不在 spike 阶段考虑）
- case 来源：我先猜 5 个，Stage 2 再用真实 session log 替换
- 输出格式：stdout text，不 JSON 不文件
- 不开 Phase 16 正式 boundary doc / plan（spike 不算 phase）

**预期你会观察到的 5 件事**（**这是 prediction，跑完打脸是好事**）：

1. **case-01（明确）大概率 pass 两个 scorer** —— 如果它 fail，说明
   FOCUS_STATE_SYSTEM_PROMPT 有 fundamental 问题，不只是 dataset 问题
2. **case-02（模糊）的 next_step 比 goal 更有意思** —— LLM 大概率会
   抓到 "cache" 这个 topic（goal 部分 pass），但 next_step 应该写
   "等用户给更多信息"。我赌 50% 概率 LLM 会编造 "fix race condition"
   或 "investigate cache invalidation" 这种比 input 信号还具体的细
   节 —— 那就是 fabrication，说明 prompt 缺少 "don't make up details"
   指引
3. **case-03（tool-only）会暴露 prompt 没 cover 这个 shape** —— 我赌
   60% 概率 LLM 把 tool 名字（"Grep"）当作 goal 的一部分。这是
   `_build_user_message` 在 `focus_state.py:107` 那段
   `if hasattr(block, 'name'): snippet = f"[tool] {block.name}"` 的
   后果，LLM 看到 `[tool] Grep` 字面，prompt 没指引它去翻译成 user-side
   语言
4. **case-04（multi-step）有 30% 概率 LLM 把最近一步动作当 goal** ——
   "查找 User class 的定义" vs 高层 "修 test_user.py"。如果 fail，是
   prompt 里"prior_focus_state"参数能解决的题（continuity hint）
5. **case-05（adversarial）next_step 会缺少推动力** —— 我赌 70% 概率
   LLM 写出 "completed" / "nothing to do" / None。这是因为 prompt 完
   全没暗示"用户 done 不等于真的 done"。如果跑完果然这样，**Stage 2
   要写 5+ 个类似 adversarial case，prompt 改动里要加 "if user signals
   done, consider what verification step is missing"**

**完工后你要能回答的题**（playbook §八 Milestone 1 self-test）：

- Q1. 哪 1-2 个 case 出乎你意料？（"产品 surprise" 在哪）
- Q2. 哪 1-2 个 case 启发 Stage 2 dataset 应该补什么 edge？
- Q3. 4 个 scorer（parse + schema + goal_judge + next_step_judge）够 / 不够 / 还要加什么 dim？

**Mentor 提示**：跑 spike 时**不要直接看 score，先把 5 个 LLM raw
output 完整读一遍**。score 是 derived signal，raw output 是原始信号。
专家先读原始信号建直觉，再看 derived 信号 calibrate。

---

### During  (你跑 spike 过程中即时填)

<!-- 跑 spike 的时候在这里记你**意外**和**临时决策**。例子格式：

- **意外**：case-02 LLM 居然返回 `goal=null next_step=null`，跟我
  Before § 4 预测的"会编造细节"相反 —— prompt 实际有兜底
- **临时决策**：本来打算只看 stdout，但 case-03 的 raw output 太
  奇怪，临时把 `result.goal` print 到 5000 字符不截断
- **意外**：5 次 API call 中 1 次 timed out (qwen-plus 偶尔会)，
  retry 1 次成功 —— summarize 的 timeout=15s 在 spike 阶段刚好够

不要写流水账。30 字以内描述一件事。
-->

(留空 — 跑完即时填)

---

### After  (跑完 30 分钟内填)

<!-- 回答 4 件事：

#### Q1. Self-test 答案
- A1（出乎意料）：case-XX 因为...
- A2（dataset edge）：case-XX 暴露了...需要 Stage 2 补 N 个 case 在...
- A3（scorer 维度）：4 scorer 够 / 不够 因为...

#### Q2. Before § 4 预言 vs 实际打脸表
| 预言 | 实际 | 打脸 / 命中 | 学到 |
|---|---|---|---|
| case-01 pass | ... | ✓/✗ | ... |
| case-02 编造细节 | ... | ✓/✗ | ... |
| case-03 tool 名字当 goal | ... | ✓/✗ | ... |
| case-04 低层动作当 goal | ... | ✓/✗ | ... |
| case-05 next_step 缺推动力 | ... | ✓/✗ | ... |

#### Q3. Stage 1 我应该带着什么改动一起开
（不只是 substrate 设计，还包括：spike 暴露的 prompt 问题要不要顺
手改？dataset 要不要先扩到 10 case？scorer 要不要先加第 3 个？）

#### Q4. 如果重做 Stage 0 我会改 process 什么
（meta-learning。比如："我会先 raw read 5 个 output 再看 score" 或者
"5 个 case 不够，下次直接 10 case"。这些经验 carry 到 Stage 1 怎么 design） -->

(留空 — 跑完填)

---

## Stage 1 — Eval substrate (Dataset/Scorer Protocol + Runner)

> 时间：TBD（Stage 0 After 写完后开）
> 状态：模板预占位

### Before

(Stage 0 After 写完之后才填这里。**关键 rule**：Stage 0 After 没写
完不开 Stage 1 Before —— 否则 prediction 跟实际经验脱节，journal 退化
成日记)

待填项 reminder：
- 目标 / Scope / Anti-scope / 已 lock 的 ratification
- 5 件你预期会观察到的事
- self-test 题（playbook §八 Milestone 2 题贴过来）

### During
(留空)

### After
(留空)

---

## Stage 2 — Dataset 扩到 70 case

> 时间：TBD
> 状态：模板预占位

### Before / During / After
（待 Stage 1 完工后填模板）

**特别提醒**（mentor）：Stage 2 是 craft 真正 stretch 的地方。30
representative + 30 edge + 10 adversarial 不要拿 LLM 一晚上 generate
完 —— 你会失去 dataset 的"为什么是这 70 个"判断力。**Stage 2 的
Before § 4 prediction 应该列 "我猜 dataset 跑下来 representative
case pass rate ~ 90%, edge case ~ 60%, adversarial ~ 30%"。跑完打脸
你才 calibrate 自己对产品判断的精度**。

---

## Stage 3 — 4 个 scorer 全 implement (含 LLM-judge)

> 时间：TBD
> 状态：模板预占位

**特别提醒**（mentor）：Stage 3 第一次写 LLM-judge prompt 是个 milestone
moment。**写完 judge prompt 后第一件事不是跑全集，是用 dataset 里
3 个 borderline case 跑 judge 5 次**（同 input 跑 5 次），看 judge
答案稳不稳。**不稳就说明 rubric 太模糊**，回去改 rubric，不要 ship
不稳的 judge 进 substrate。

---

## Stage 4 — Cassette mode (record / replay / live)

> 时间：TBD
> 状态：模板预占位

**特别提醒**（mentor）：playbook §五 5.4 那个 "replay 模式的隐藏坑"
—— scorer 改了之后 replay 给出的 score 不是产品真实 score。Stage 4
的 Before § 4 要把"我打算怎么防这个坑"写下来。**不写防御方案，cassette
机制半年后会让你 silently misled**。

---

## Stage 5 — `oh eval` CLI 子命令

> 时间：TBD
> 状态：模板预占位

**特别提醒**（mentor）：CLI 子命令是工程 boilerplate，但 Stage 5 的
**真正 milestone 是第一次 calibration loop 闭环** —— 改 prompt → 跑
record → 比 score → review failure mode → 改 prompt v2 → 再跑。
**Stage 5 After § 3 要记 "我这个 close-loop 一次性走通了多少分钟，
中间卡在哪几步"** —— 这个时间是 6 个月后你判断 "Eval craft 是不是
internalize 了" 的硬指标。

---

## 后续 Milestone（playbook §八 对照）

按 playbook §八 路径，Stage 0-5 完成 ≈ Milestone 1 + 部分 Milestone 2。
进入 **Milestone 2**（三个 service 装齐）后，本 journal 新开 Stage 6+：

- Stage 6 — extract.py 装上 eval suite（复用 Stage 1 substrate 验证 compounding）
- Stage 7 — compact L4 装上 eval suite（最大 input，cassette 文件最大）
- Stage 8 — 三 service eval 都 ship 后第一次 prompt 改动 → 看 multi-service
  cross-impact（改 summarize() 的 timeout 影响 3 个 consumer 的 score 没?）

**Milestone 3**（calibration loop + judge agreement）journal Stage 9+：

- Stage 9 — 月度 judge agreement check (30 case 人审 vs LLM-judge)
- Stage 10 — 第一次 retire dataset case（连续 3 月 100% pass）
- Stage 11 — 第一份 eval-driven prompt-change retro（learnings/eval-quarter-1.md）

**Milestone 4**（跨越业余 / 专家分水岭）journal 不再 stage-by-stage，
合并写一篇博客文章 / 内部分享 PDF。

---

## Mentor 给你的 5 句话（每个 stage 开始前回读一遍）

1. **Before § 4 prediction 是 journal 的灵魂**。不写预言 = 在做手艺
   而不积累手艺。
2. **During 只记意外和临时决策**。流水账 git log 已经有了。
3. **After 必须 30 分钟内写完**。超过 1 小时记忆已经偏差 30%+。
4. **Stage N After 没写完不开 Stage N+1 Before**。强约束，不破例。
5. **journal 不是 ship 给别人看的**。是给一年后的你自己看的 ——
   一年后你想搞清楚"我当初为什么这么设计 scorer"时，**没有 journal
   就只能瞎猜**。
