# Decision NN — <一行 phase 名>

> Date: YYYY-MM-DD · 上游: [link]
> 配套读物（按需，可省）:
> - Phase N-1 retro: [link]
> - Boundary 上游契约: [link]

## 一、Why now

<2-3 句话足够。什么 incoming pressure 让这个 phase 必要。
不写长 narrative，不重复 retro 已经说过的事。>

## 二、In / Out

**IN（this phase 必做）**:

- capability 1
- capability 2
- capability 3

**OUT（推迟 / 不做）**:

| 推到哪 | 项 | 一句话原因 |
|---|---|---|
| Phase N+1 | item | 一句话 |
| 不做 | item | 一句话 |

## 三、Decisions

### DNN.1 — <一行标题>

**Chosen**: 一句话决策

**Why**: 1-2 句。**不超过 2 句**。

**Alternatives**: 一句话——「考虑过 A（缺点 X）/ B（缺点 Y），选 C 因为 Z」

**Reversibility**: `easy` | `medium` | `hard`——后面跟 1 句条件

**Anti-scope（如需要）**: 一句话——「明确**不**做 X 因为 Y」

### DNN.2 — <一行标题>

…重复结构…

### DNN.3 — …

## 四、Acceptance（phase 级，跨 task）

- [ ] regression: 全仓 `uv run pytest -q --no-cov` 绿
- [ ] dogfood: <端到端验证一句话>
- [ ] 文档同步：CHANGELOG + learnings/phase-N.md 写完
- [ ] §六 wiring audit verdict 实测对照入 retro

## 五、Tasks

按执行顺序列 T1..TN。每 task = capability 描述 + per-task acceptance。
Sub-task 拆解由 agent runtime 自行决定，不在 spec 里写 1a/1b/1c。

### T1 — <一行 capability 标题>

**Description**: 1-2 句话目标。

**Acceptance**:
- [ ] criterion 1（具体可验证）
- [ ] criterion 2
- [ ] criterion 3

### T2 — <一行 capability 标题>

**Description**: …

**Acceptance**:
- [ ] …

### T3 — …

## 六、§六 Wiring audit

跨 runtime layer 的影响预测——retro 时回头逐项 falsify:

| Layer | Verdict | Reasoning（一句话） |
|---|---|---|
| `permissions/` | unchanged \| extension \| bypass \| verification | 一句话 |
| `hooks/` | … | … |
| `services/snapshot|session_memory|compact` | … | … |
| `engine/slash_skill` | … | … |
| `skills/store + model` | … | … |
| `commands/` | … | … |
| `bundles/` | … | … |
| `cli.py` | … | … |
| `observability` | … | … |
| `eval/` | … | … |
| (其它本 phase 涉及的 layer) | … | … |

**Conclusion**: <1 句话总结——比如「3 extension + 11 unchanged + 0
bypass，符合 cleanup-sized phase 形态」。

按 CLAUDE.md 规则：≥ 3 `requires extension` **或** 多个 `bypass` →
重新 ratify scope。

## 七、References

- [link 1]
- [link 2]

---

<!--
=========================================================================
                       Drafter guidance (copy D40 时删此块)
=========================================================================

1. D-numbered decisions 是 anchor。后面可以被 reference 或 reverse。
2. 每条 decision ≤ 10 行（Chosen / Why / Alternatives / Reversibility 各 1 句）。
3. §六 verdict 严格限定 4 选 1: unchanged / requires extension /
   requires bypass / requires verification。retro 时逐项 falsify。
4. Why now ≤ 3 句。

## 行数 budget

| Section | 预算 |
|---|---|
| Header | 5 |
| Why now | 5 |
| In/Out | 15 |
| Decisions | 70-100（7-9 条 × 10 行）|
| Acceptance (phase 级) | 8 |
| Tasks | 40-60（T1..TN × 6-8 行）|
| §六 audit | 25 |
| References | 10 |
| **合计** | **~180-230** |

Task 内每条 acceptance bullet 必须**具体可验证**。
引用老 D-decision 直接写 "per D38.5"，不 inline 复制。
narrative / "我学到什么" 进 `learnings/`，不塞 boundary doc。

=========================================================================
-->
