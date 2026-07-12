# Sprints 2026-07 — benchmark 收官后的修复与改进规划

> Created 2026-07-12 · 上游:benchmark 战役(`benchmarks/swebench/RUNLOG.md`
> 12 节点 + TAXONOMY)、eval 双 P0(D41)、dogfood 首轮
> (`learnings/dogfood-2026-07-12.md` F1-F4)。
> 排序原则:先闭合 eval 改进环(叙事价值最高)→ 再修 dogfood 体验
> (飞轮进料速率)→ 再开新 eval(P1,case 源已热)。
> 用户已 ratify:Sprint 顺序 1→2→3;F2 拍**甲**(绝对路径 specifier)。

---

## Sprint 1 — 治 skill_trigger 两个吸引子,闭合 ratchet 环(S,半天)

**目标**:eval 线从"测量装置"变成"改进引擎"的第一次实证——
eval 抓缺陷 → 改被测对象 → 重跑画像 → 红转绿 → bar 棘轮。

**背景**(evals/skill_trigger/dataset_card.md Known reds):
- 委派吸引子:多步任务(TS1-release-notes)4/4 次选 SpawnAgent 而非先 LoadSkill
- 直答吸引子:窄任务(TS5-exact-slug-hyphens)4/4 次零工具直答

**改动面**:`prompts/system.py::_format_skills_section`(目录段措辞,加
"before delegating or answering, check skills"方向的引导)± `LoadSkillTool.description`。
**不改** skill_trigger eval 本身(oracle 一字不动——改题凑绿是红线)。

**波及核查**(已预判):tool_choice eval 不受影响(其 subject 不带
skill_store,Available Skills 段不出现);prompts 的既有 TDD 测试若断言
目录段原文需同步更新(先改测试见 RED)。

**Acceptance**:
- [ ] qwen-max N=4 重画像:两个稳定红至少一个转绿;转绿即 ratchet bar
      (6/9 → 对应新地板),部分转绿如实记录
- [ ] 原 6 个稳定绿 case 无一破绿(回归守卫)
- [ ] 重录 cassettes;dataset_card 更新画像与 bar 依据
- [ ] 全仓质量门:pytest / mypy --strict / ruff

**诚实预期**:prompt 措辞是概率层改动,可能治不好——届时如实记录
"措辞 X 不足以治 Y",bar 不动,也是有效产出(eval 的负结果同样是结果)。

---

## Sprint 2 — dogfood 小修包 F1-F4(S×4,半天)

**目标**:清掉首轮 dogfood 的四条进料,改善日常使用 → 提升飞轮进料速率。

| 项 | 改动 | Acceptance |
|---|---|---|
| F1 `/compact` 守卫 | 显式命令绕过 12 条门槛(或降为 2)+ 拒绝信息如实(报"仅 N 条消息,低于压缩窗") | REPL 内两轮对话后 `/compact` 真的压缩或说真话 |
| F2 绝对路径 specifier(**甲**) | `rules.py`:specifier 以 `/` 开头 → 绝对路径 glob 匹配;不以 `/` 开头维持 cwd 相对 | `Write(/tmp/**)` 在 headless 放行 /tmp 写入;`Write(**)` 语义不变(回归) |
| F3 副产品提示 | 无 `--isolate` 的修复循环收尾时,检测工作树新增 untracked 文件并在 stderr 提示 | 复现实验 5 场景,收尾输出包含新文件清单 |
| F4 拒绝消息带正解 | permission denied 消息内嵌正确的 env 配置示例(`OPENHARNESS_PERMISSIONS__ALLOW=...`) | 消息含可直接复制的正确语法;模型转述时有真话可抄 |

全部 TDD:先写测试见 RED。F2 是安全面改动,测试须覆盖
"绝对 specifier 不会意外放大 cwd 相对规则"的边界。

---

## Sprint 3 — A5 被拒后行为 + A6 错误恢复 eval(P1,M,1-2 天)

**目标**:D41 P1 首开,oracle 升级到**轨迹不变量**(比 `=` 判难一档)。

**Case 源(已到手,按 D41.6 飞轮沉降)**:
- A5:astropy-14182 死亡链(Bash 被拒 → 挣扎 → 撞顶,benchmark 实录)
- A5:F3 fizzbuzz 改道(被拒后换路写 cwd——**健康**行为的正样本)
- A6:F4 编造 YAML 配置(错误恢复时的编造形态)
- A6:tool_choice TC4 / skill_trigger TS4 的种植错误模式复用

**不变量 oracle**(D41.4):被拒后下一步 ≠ 原样重试;同一被拒调用不重复
≥2 次;未过早放弃(有后续动作)。
**联动**:Sprint 2 F4 改进了拒绝消息——A6 可对照"消息改进前后"两个条件
跑(条件戳纪律的正用)。

**Acceptance**:照 D41 §四规格(四声明头 / 引用 D41 面号 / 复用 substrate /
≥8 case / N≥4 画像后定 bar / 质量门)。

---

## 无人值守决策规则(ratify 2026-07-12——判断预先立法,judge 型判据降级为 verify 型)

1. **Sprint 1 措辞迭代上限**:最多试 2 版措辞;每版跑 qwen-max N=4 画像;
   任一稳定红转 4/4 绿 → ratchet bar 到新的稳定绿地板;两版都治不好 →
   如实写入 dataset_card("措辞 X/Y 不足以治 Z"),bar 不动,**停**——
   不许第三版。
2. **Sprint 1 回归红线**:原 6 个稳定绿 case 任何一个破绿 → 立即回滚该版
   措辞,该版记为失败。
3. **Sprint 3 bar 规则**:沿用 skill_trigger 先例——bar = N=4 画像的稳定
   绿地板 + 稳定绿必须全绿;稳定红全部记 Known reds(改进 backlog);
   禁止给抖动 case 设门。

## 排队(不进本轮 Sprint,已定 scope 待触发)

| 项 | scope | 触发条件 |
|---|---|---|
| D1 正门收尾 | 根命令收编 `-p` + 位置 prompt(`oh "x"` 进 REPL);36 旗按"每次调用会变吗"分堆,配置旗退役为 env-only;Ctrl+D 双按保护 | 下次碰 REPL/CLI 层时整包做 |
| B2 compact L4 摘要 eval | 种植事实回收 oracle(fail-open 最高风险面) | Sprint 3 收口后的下一个 P1 |
| D2 retry 流中断覆盖 | api/retry.py 识别 mid-stream disconnect 为可重试 | 下次碰 api 层时顺手 |
| B3 / L2 任务集 / SWE-bench-Live | D41 P2 / D40 M2 | 维持等触发 |

## 不做(边际递减,无真实驱动)

- 继续加 benchmark 题量 / 追 17 个 matplotlib env error
- 跨模型比较(D35.8 前置未满足)
- swarm / 并行多 agent

---

## 里程碑上下文(为什么是这三个 Sprint)

benchmark 已交付"使用证据"(56.7% + 零 harness 硬失败);eval 线已立范式
(双 P0)。当前最高杠杆 = 让 eval 环真正转一圈(Sprint 1)——它是求职
叙事里"我不只测量,我用测量驱动改进"的那一段;dogfood 修复(Sprint 2)
保持飞轮转速;A5/A6(Sprint 3)趁 case 源新鲜沉降。三个 Sprint 合计
2-3 个工作日,与求职材料线并行,冲突时求职优先。
