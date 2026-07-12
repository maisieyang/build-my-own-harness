# skill_trigger Eval Dataset Card

> D41 P0 第二个落地 · 2026-07-12 · four-declaration header per D35.3 + D41.5

## Declarations(四声明头)

**1. Capability claim**:这份 eval 测试**当前 production 目录呈现
(`build_system_prompt` 的 `## Available Skills` 段 + `LoadSkillTool`
工具描述)**在决策面 **#5(LoadSkill 触发;D41 §一 C1)**上的单步决策
质量:该调时调、不该调时不调、slug 精确。目录本身是 fixture(4 个合成
skill,呼应 finance-skills dogfood 形态——#5 P3→P0 的 reshuffle 依据)。

**不为之设计**:
- skill body 加载后的执行质量(那是 body 内容的事,不是触发决策)
- 跨 model 强弱比较(D35.8 前置未满足)
- 多轮轨迹质量(TS4 只测种植错误后的**单步**自纠)

**2. Input spec**:合成对话(单条用户消息为主;TS4 为种植 LoadSkill
未知-slug is_error 目录回喂的三消息形态,错误文本 = `load_skill.py`
真实格式)。N=9,覆盖 5 个 capability(TS1 trigger ×2 / TS2
discrimination ×2 / TS3 restraint ×2 / TS4 self-correction / TS5
slug-fidelity ×2)。restraint 语义与 tool_choice TC5 不同:**允许调
其他工具,只断言不调 LoadSkill**。扩量走 D41.6 飞轮。

**3. Judgment spec**:全部确定性 scorer,零 LLM-judge(本面最硬 oracle
是 `=` 判:调没调 × slug 对不对):
- `trigger_decision`(binary)— 期望有 slug 时:tool_uses 中存在
  LoadSkill;期望 null 时:不存在(其他工具 vacuous)
- `slug_selection`(binary)— 首个 LoadSkill call 的 `name` == 期望
  slug,**精确匹配**(下划线化/大小写漂移均为失败——目录 case-sensitive,
  错 slug 会被工具弹回);restraint case vacuous pass

**4. Reference policy**:参照模型 **qwen-max**(与 tool_choice 一致)。
弱模型上的红 = 信息,不是 gate 红(design-for-strong-model,D41.5)。

## Capability coverage

| Capability | Cases | 测什么 |
|---|---|---|
| trigger | TS1 ×2 | 任务正面命中描述时触发 |
| discrimination | TS2 ×2 | 相邻金融 skill 按描述辨析(含表面词与正确 slug 相反的反向探针) |
| restraint | TS3 ×2 | 无匹配 skill 时不触发(含 '贷款' 字眼在场的近失误探针) |
| self-correction | TS4 | 未知 slug 的目录 is_error 回喂后单步选出精确 slug |
| slug-fidelity | TS5 ×2 | 连字符 slug 精确性 |

## Pass bar(ratify 2026-07-12,依 D41.5)

- **Gate:参照模型 qwen-max 上 `cases all-dims-pass ≥ 6/9`**,且 6 个
  稳定绿 case(TS1-credit / TS2-credit-not-loan / TS3 ×2 / TS4 /
  TS5-sql)**必须全绿**——任一破绿即回归。
- 依据(N=4 稳定性画像,2026-07-12):6 case 4/4 绿;TS2-loan-not-credit
  2/4 抖动;TS1-release-notes 与 TS5-exact-slug-hyphens **4/4 稳定红**。
  禁止给 <100% 稳定的 case 设 100% 门,bar 落在稳定绿地板。

## Known reds(已知红基线 = 改进 backlog,非 oracle 缺陷)

两个 4/4 确定性红是**真实触发缺陷**,契约(目录段 "call LoadSkill to
expand" + 工具描述 "Use when the user's task matches")明确覆盖这些
case,模型行为违约:

1. **委派吸引子**(TS1-release-notes):多步任务时模型每次都选
   SpawnAgent 而非先 LoadSkill——skill 触发输给子 agent 委派。
2. **直答吸引子**(TS5-exact-slug-hyphens):窄任务时模型自认能答,
   零工具直接开答。

改进方向在**被测对象侧**(目录段措辞,如 "check skills before
delegating or answering")——prompt 改动后重跑画像,红转绿则 ratchet
bar(6/9 → 8/9),这正是 eval 作为改进闭环的用法。TS2 抖动(2/4)记录
在案,不计入 bar。

## 措辞迭代记录(Sprint 1,2026-07-12 — 负结果,按预立规则回滚)

按 `tasks/sprints-2026-07-plan.md` 预立规则(≤2 版;任一原稳定绿破绿即
回滚)执行两次目录段引导语实验,**两版均触发回归红线,全部回滚**:

| 版 | 措辞要点 | 画像(N=4) | 增益 | 回归 |
|---|---|---|---|---|
| A(attempt1) | "call LoadSkill with that exact name FIRST — before answering / before delegating" | 8,7,9,9 /9 | 委派吸引子 4/4 治愈;TS2 抖动止 | **TS5-sql 破绿 1/4**:模型把 skill 名当工具名直接调(`calls: sql-query-optimizer`) |
| B(attempt2) | A + 显式消歧 "skills are loaded only via LoadSkill; skill names are not callable tools" | 8,9,8,8 /9 | 同 A | **同款破绿 1/4**——消歧无效 |

**科学结论**:
1. 委派吸引子**可由措辞根治**(两版均 4/4)——缺陷在引导缺失,不在模型
   能力;直答吸引子(TS5-hyphens)部分改善(4/4 红 → 2/4 抖)
2. 但引导语**诱发新缺陷**:skill-名-当-工具-调用(两版均 ~1/4)。突出
   skill 的存在感会增强幻觉工具的引力——修复引入了新失效模式,净交换
   被回归红线拒绝
3. 缓解剂量:生产多轮环境里该失误会被引擎的 tool-not-found 回喂纠正
   (A3 恢复路径),单步 eval 的判罚比真实严重度偏重——**规则校准候选**:
   "破绿"或应定义为稳定破(≥2/4)而非单次;留给用户裁决
4. 修复方向候选(未试):不动 prompt,在 registry 侧给未知工具名做
   最近邻提示("did you mean LoadSkill(name=...)?")——A3 错误消息改进,
   归 Sprint 3 A6 地界

bar 维持 6/9 不变;Known reds 不变;attempt 文件保留于 results/。

## Cassettes & results

- `cassettes/qwen-max/infer/` — 9 case 回放基线(record 轮 7/9,TS2
  恰为绿;回放与 record 输出一致性已验证)
- `results/qwen-max-run{1..4}.txt` — N=4 画像原始输出
- 复跑:`OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_skill_trigger_eval.py`
