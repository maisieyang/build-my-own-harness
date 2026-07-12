# Eval 周计划(2026-07-13 起)— 七面循环 × 两轨并进

> 用户定调:eval 值得一周持续投入。路径 = 按 D41 的 7 个决策面逐面走
> **四拍循环**:①设计 dogfood → ②作者亲手跑(体感/认知)→ ③设计或
> review eval → ④沉降书稿章节素材。同时开第二轨:投简历。
> 分工沿用三拍协议:dogfood 包由 Claude 设计,作者跑;eval 实现 Claude
> 写,作者 review;书稿成文归作者,Claude 出素材包。

## 两轨规则

- **轨 A(求职)优先**:每天固定时段投递/材料(时长你定);与轨 B 冲突时 B 让路
- **轨 B(七面循环)**:每面的产出三件套 = dogfood 实验记录(learnings/)
  + eval 动作(新建/review/case 沉降)+ **章节素材包**(一手案例/数据/
  结论的 bullet 清单,存 docs/ideas/ 或章节笔记;**成文不排进本周**——
  写作节奏归书线自己)
- 已有纪律全部沿用:oracle 硬度阶梯、N=4 画像、bar=稳定绿地板、
  预立规则(含"稳定破 ≥2/4")、飞轮(体感与 dataset 对不上 → 沉 case)

## 逐日安排

### Day 0(半天)— 收尾 + 两轨点火

- [ ] **D43 收口**(悬账):2 个旧 help 测试改到 D43.3 契约 + 全仓门 + commit/push
- [ ] 简历粘贴 eval bullet(已备好,中英双版)+ 投递批次 #1
- [ ] 本计划 commit

### Day 1 — 面 #2 工具选择 + 面 #5 skill 触发(已覆盖,review 型)

- dogfood:真实任务里观察工具选择与 skill 触发(安装 2-3 个真 skill,
  含 finance-skills 的),重点找 tool_choice/skill_trigger dataset
  **没覆盖的形态**(委派吸引子治愈后的边界、TS5 直答残留的真实频率)
- eval 动作:对照体感 review 两份 dataset;差异 → 飞轮沉 case
- 书素材:Ch9(决策面枚举的由来)+ Ch6(skills);skill_trigger 的
  ratchet 全史(误杀→校准→复活)是 Ch9 的现成案例段

### Day 2 — 面 #3 错误反馈消费(已覆盖,体感价值最高)

- dogfood:**亲手撞墙**——headless 下故意触发权限拒绝/未知工具/参数错,
  看模型下一步(你已在实验 5 撞过一次,这次系统地撞全四种错误形态)
- eval 动作:review error_feedback 的 9 case;14182 抖动 case 的真实
  复现率;F4 条件对是否需要多轮版本(记 L2 需求)
- 书素材:Ch9/Ch10;"原料全真装配全错"与"门不信自评"两个论点的案例链

### Day 3 — 面 #1 Secondary pass(重点日:B2 新建,P1 顶端)

- dogfood:长对话(30+ 轮)亲手 `/compact`,读摘要——**丢了什么你最
  在意的事实?**(种植事实回收 oracle 的人肉预演);顺带 `--goal-condition`
  亲手用一次(B3 judge 的体感,只记 case 源不建)
- eval 动作:**新建 B2 compact 摘要 eval**——fail-open 最高风险面;
  oracle = 种植事实回收(埋 N 个关键事实,判摘要含不含,`=`/keyword 可判)
  + 按 D41 §四规格全套(四声明/N=4 画像/bar)
- 书素材:Ch10(摘要是模型的全部记忆——fail-open 风险的叙事)

### Day 4 — 面 #4 memory + 面 #6 sub-agent(一 review 一零覆盖)

- dogfood:①memory 读路径——存 5+ 条记忆后新会话问相关问题,看它挑
  哪条 Read(C4);②SpawnAgent——给一个适合委派的任务,看子任务 prompt
  写得如何、收割是否忠实(C2,全库唯一零覆盖面)
- eval 动作:C4/C2 各做**立项判断**(当天拍板:够痛就立,不痛记 case 源
  等触发——预立判据:dogfood 里出现 ≥1 个真实失败形态才立)
- 书素材:Ch12(memory)+ Ch7(sub-agent)

### Day 5 — 面 #7 E2E:L2 任务集落地(金字塔补最后一层)

- dogfood:重跑六练习中 oracle 可机器化的 3 个(练习 4 fail-closed /
  练习 5 verify 链 / R3 轮次顶),确认预期仍成立
- eval 动作:**L2 种子集落地**——3-5 个任务级 case(迷你仓库 + 硬 oracle,
  `--verify` 判),这正是"任务级的可反复吃的 dogfood"
- 书素材:Ch18(benchmark 之下的自有任务层)+ 金字塔三层的完整叙事

### Day 6(半天)— 周复盘

- [ ] RUNLOG 周记(七面各一节点:体感→eval 动作→素材去向)
- [ ] 章节素材总账(哪章富了多少)
- [ ] 投递批次 #2 + 下周展望(成文周?)

## 诚实预算

- **必建只有两个**:B2(Day 3)+ L2 种子(Day 5);C2/C4/B3 是"当天
  拍板"不是承诺——七面里五面是 review 型,一周装得下
- 书稿**只出素材包不出成文**——成文塞进本周会两头都糊
- 任何一天求职线有面试/笔试,当天轨 B 顺延,计划不重排(容差在 Day 6)
