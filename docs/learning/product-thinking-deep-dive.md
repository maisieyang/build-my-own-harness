# 产品思维深挖：从 /ideate 反推回去的方法论根系

> 这是一份**活文档**——会随阅读和实践不断更新。
>
> 起源：2026-04-27 用 `/ideate` 跑了两轮 ideation（why harness now / what is harness），
> 反思后发现 `/ideate` 是 50 年设计思考方法论的 AI 操作化版本。要深挖这个根系。

---

## 为什么读这些

跑完两轮 `/ideate` 后我意识到几件事：

1. `/ideate` 的流程（divergent → convergent）**不是 AI 时代发明**，是 1960s-1980s 设计思考方法论的 AI 操作化
2. 它能挖出 unknown unknowns 是靠三个机制：**强制 articulate** / **多 lens 发散** / **对抗式 stress-test**——这些都来自更早的方法论传统
3. 我作为产品工程师，**学会这套方法的根系比学会一个 AI 工具更值钱**——AI 工具会迭代消亡，方法论的 mental model 是终生资产

所以建这份文档，分层读，让方法论真正长进我的工作流。

---

## 核心要内化的 5 个 abstract pattern

读书前先记住目标——读完这些书我**应该能在自己工作里熟练应用**这 5 件事：

- [ ] **Forced articulation 输入卫生**：每次写 PRD / RFC / 提需求前自问 5 个 sharpening question
- [ ] **多 lens 强制扩大搜索空间**：技术选型时强制用 SCAMPER 7 lens 跑一遍
- [ ] **Pre-mortem 制度化**：每个产品决策落地前显式写"什么会让这个失败"
- [ ] **"Not Doing" 列表 > "Doing" 列表**：每个 spec / design doc 强制有 Not Doing section
- [ ] **Artifact 标准化**：用 PRD / RFC / ADR / Decision Record 把消耗式讨论变成积累式资产

---

## 推荐阅读顺序（决策树）

```
Step 1: 读 Paul Graham《How to Do Great Work》（30 min, 免费）
  ↓
  - "嗯，有共鸣" → 投入读 Tier 1 的另一本（Tim Brown）
  - "so what" → 这套思维不是你的天然语言；
                考虑直接读 Lean Startup 那条路线
  ↓
Step 2: Tier 1 第二本（Change by Design）慢读 1-2 周
  ↓ 等"click"
  ↓
  click 发生 → 进入 Tier 2（Continuous Discovery / Working Backwards）
  没 click → 跳去 Tier 3 essay（Hamming / Bezos / Victor）从不同角度再撞
```

**反 anti-pattern**：不要按页码顺序硬读；不要"为了读完"读。**目标是在工作里用上**。

---

## Tier 1 — 让 "click" 发生（先读这两个）

| 状态 | 资源 | 形式 | 时间 | 为什么这本 |
|------|------|------|------|----------|
| ☐ | **Paul Graham《How to Do Great Work》** [paulgraham.com](http://paulgraham.com/greatwork.html) | 免费 essay | 30 min | 从"如何做出有意义工作"反推到"如何选问题、避免被错方向骗"。和 `/ideate` 的 forced articulation 同根。**第一站永远是这篇** |
| ☐ | **Tim Brown《Change by Design》**（中译《IDEO 设计改变一切》） | 书 (~250 页) | 1-2 周慢读 | IDEO 创始人，把 "divergent → convergent" 讲成可视的设计师工作流，配大量真实项目案例。读完会自然把 `/ideate` 认作 design thinking |

### 个人笔记区（读完填）

- _Paul Graham essay 笔记_：
  - （读完写下让你"嗯"的那 1 句话）
  - （和你工作的连接点）

- _Change by Design 笔记_：
  - （IDEO 案例哪个最让你印象深刻？为什么）
  - （这本书让你重新看自己工作的哪一点？）

---

## Tier 2 — 把方法变成能用的（click 之后读）

| 状态 | 资源 | 时间 | 为什么这本 |
|------|------|------|----------|
| ☐ | **Teresa Torres《Continuous Discovery Habits》** | 200 页 / 1-2 周 | Product discovery 最实操的手册。"Opportunity Solution Tree" = `/ideate` 的 product 版本；"Assumption Mapping" 章节直接是 stress-test 的细化教程 |
| ☐ | **Colin Bryar & Bill Carr《Working Backwards》** | 300 页 | Amazon "PR/FAQ" 方法解密。**把"先写发布新闻稿再做产品"作为 forced articulation 的企业制度**——你会看到 `/ideate` 的 sharpening question 在大尺度怎么落地。第二章的 Narrative Memos 论证了"写作就是思考"——和我们前面 `/build` 强制每个 commit 都写 message 同源 |

---

## Tier 3 — 节选阅读（不需要读完）

| 状态 | 资源 | 读哪部分 | 为什么 |
|------|------|---------|------|
| ☐ | **Daniel Kahneman《Thinking, Fast and Slow》** | Part 1（System 1 vs 2）+ Part 2（heuristics & biases） | 解释**为什么**结构化流程能击败直觉——unknown unknowns 都藏在 System 1 里。这是 `/ideate` 有效性的**认知科学基础** |
| ☐ | **Edward de Bono《Six Thinking Hats》** | 全本 100 页，但只需理解 6 顶帽子的 framework | 直接讲"切换 lens 为什么有效"。六顶帽子（白事实/红直觉/黑批判/黄乐观/绿创新/蓝流程）做技术评审时会不自觉用上 |

---

## Essays + 演讲（值得收藏，多次回看）

| 状态 | 资源 | 长度 | 为什么 |
|------|------|------|------|
| ☐ | **Richard Hamming《You and Your Research》**（1986 Bell 实验室） | 演讲 ~40 min（有书面版） | "如何选重要问题"——在工程师传统里和 `/ideate` 最接近。Bell 实验室文化的精华 |
| ☐ | **Jeff Bezos 2002 股东信** | 5 分钟 | "Most decisions are reversible. Stop deliberating, start trying." 和 `/ideate` 的"产出可证伪而非定论"同源 |
| ☐ | **Bret Victor《Inventing on Principle》**（2012 演讲） | 60 min 视频，bilibili 有中字 | 讲"为什么要直接看到反馈"。`/ideate` 的 stress-test 本质是把后期反馈提前到设计阶段 |
| ☐ | Bezos 全套股东信合集（1997-2021） | 各 5-10 分钟 | Day 1 思维 / 长期主义 / 反 Day 2 / 高判断力 vs 低判断力决策——一套企业家的认知工具箱 |

---

## 中文世界一本必读

| 状态 | 资源 | 为什么 |
|------|------|------|
| ☐ | **《俞军产品方法论》**——俞军（百度搜索 PM 出身） | 中文产品圈最系统的产品思维方法论。中文世界里和 `/ideate` 思想最接近的本土化版本。**强烈推荐和 Tim Brown 对照读** |

---

## 想加但还没确认的（候选池）

读完上面的之后，可能要加进来的：

- **《The Right It》by Alberto Savoia** — pretotyping，验证 BEFORE 建造
- **《Sources of Power》by Gary Klein** — pre-mortem 的发明者，naturalistic decision-making
- **《Inspired》by Marty Cagan** — 现代产品管理 bible（中译《启示录》）
- **《Lateral Thinking》by Edward de Bono** — Six Thinking Hats 的母书，更系统
- **《Where Good Ideas Come From》by Steven Johnson** — 创意如何涌现，跨学科视角
- **《Black Box Thinking》by Matthew Syed** — 从失败中学习，对产品工程师特别有用
- **《Shape Up》by 37signals** — Basecamp 的产品流程，反 SCRUM，更适合产品工程师视角

---

## 读后实践 checklist

读这些书有个陷阱：**读完会觉得"哦原来如此"，但其实只是从 "unknown unknown" 变成 "known but unpracticed"**。

真正的内化只能通过在工作里强制使用。下次有这些场景时，**回到这份文档**：

- [ ] 我下次写 PRD / RFC 时，要先自问 5 个 sharpening question
- [ ] 我下次做技术选型时，要强制用 SCAMPER 7 lens 跑一遍
- [ ] 我下次写 design doc 时，要显式有 "Not Doing" section
- [ ] 我下次做产品决策前，要做一次 pre-mortem（写下"什么会让这个失败"）
- [ ] 每个 ideation session，要标注我没做完整 phase 1-2-3 的成本（什么没问到 / 什么没 stress-test / 什么没列入 Not Doing）

每完成一次实践就在下面记一条：

```
日期 | 场景 | 用了哪个方法 | 发现了什么 unknown unknown | 之后做了什么调整
```

| 日期 | 场景 | 方法 | 发现 | 调整 |
|------|------|------|------|------|
| _空_ | _空_ | _空_ | _空_ | _空_ |

---

## 何时回看这份文档

- 每次你**有"模糊需求"** → 翻出 sharpening question 模板
- 每次你**做技术选型** → 翻出 SCAMPER lens
- 每次你**写新 design doc** → 检查 Not Doing section
- 每个**月回看一次**，看看哪本书读了、哪个方法用了、有没有新候选要加

---

## 修改记录

- **2026-04-27**：初版，从 `/ideate` 反思讨论中产出
