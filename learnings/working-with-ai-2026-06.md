# 跟 AI 协作做软件 —— 2026-06-08 sediment

> 这是 2026-06-08 一次集中讨论后的认知 snapshot。
> Field evolves fast，未来想更新写新 dated file，不改这一篇。
> 主体：今天我（solo + AI implementer）跟 AI 协作做 production-grade
> software 的方法论 —— 从 Google spec-kit 进化到 Shape B lean 之后的状态。

---

## 1. 三种 working mode（empirical 对比）

post-AI 时代有三种 stable 形态。彼此**不能 copy** —— 选哪种取决于团队
规模 + 协调成本 + 决策可逆性。

| | Google spec-kit | Claude Code 团队 | Codex 团队 | 我 (solo) |
|---|---|---|---|---|
| 团队规模 | 多 team / 跨部门 | ~12-15 人 | ~40 人 | 1 人 |
| 主要协调成本 | **多人多团队对不齐** | 已经对齐 (senior + dogfood) | 已经对齐但人数破 Dunbar | 没有协调 |
| 前置 spec 重量 | **重**（detailed specs / requirements） | **轻**（CLAUDE.md ≤ 100 行 + prototype）| **轻**（AGENTS.md ~100 行 + docs/）| **中**（boundary doc ≤ 230 行）|
| Review 形态 | 多层人审 + sign-off | walkthrough + adversarial subagent | **AI reviewer 第一道（90% S/N）+ 人兜底** | 人 walkthrough + lint hooks |
| 重写节奏 | 跟随业务节奏 | **每 3-4 周重写一次代码库** | 持续 | 不重写 |
| Dogfood | 弱 | **极强**（自己每天用 Claude Code）| 极强（自己写自己 90%+）| 弱（在学习领域）|
| 持久化形态 | text spec | text (CLAUDE.md) | **latent state (encrypted blob)** | text |
| Model 与 harness 关系 | 解耦 | 解耦 | **共训** (GPT-5.5 agentic-first) | 解耦 |

**Google spec-kit 适用场景**：多团队 / 合规域 / 回滚贵 —— spec 是
**协调机制**，替代不能彼此 dogfood 的对齐。

**Claude Code 形态可行的前提**：小 senior + 极强 dogfood + cheap
rewrite。dogfood-driven feedback loop 比 spec ratify ROI 高。

**Codex 形态的 phase transition**：12-15 → 40 人时，dogfood 不够覆盖
所有方向，开始上 custom AI reviewer 做 tiered review。

**我的形态**：solo + AI implementer + boundary doc 当**学习者直觉装载
scaffolding**。我不需要协调，但需要把 senior 工程师 20 年才有的直觉
**人为 scaffold 进去** —— 这就是为什么 boundary doc 比 Claude Code
团队的 plan mode 重。这不是过度工程，是**新手补丁**。

---

## 2. 协作边界（Human 写什么 / AI 写什么）

[Augment Code 2026 对 spec-driven development 的总结](https://www.augmentcode.com/guides/claude-code-spec-driven-development)：

| Human 写 | AI 写 |
|---|---|
| Project architecture / conventions | Code implementation 在 spec 边界内 |
| Phase gates / validation checkpoints | Architectural expansion within bounds |
| Explicit constraints / non-negotiables | Session continuity（resume 上次） |
| **Test scenarios + acceptance criteria** | sub-task decomposition (runtime) |

**我的 OH 实例字节级印证**:

| Augment 的"Human 写" | 在 OH 哪里 |
|---|---|
| Project architecture | `REFERENCE.md` (HKUDS reverse-engineered) |
| Phase gates | `decisions/NN-*.md` §一 IN/OUT |
| Validation checkpoints | `decisions/NN-*.md` §六 wiring audit verdicts |
| Explicit constraints | `decisions/NN-*.md` §二 D-numbered decisions + Anti-scope |
| Test scenarios / acceptance | `decisions/NN-*.md` §四 + §五 per-task acceptance |

decisions/ 这套 = 工业界 spec-driven development 的本地化实例。没有
偏离 Anthropic / Augment 的共识，只是更适配 solo + 学习者场景。

---

## 3. 软件稳定性怎么保证 —— 四层防御

每一层针对**不同的失败模式**。互相不可替代。

| Layer | 防什么失败 | 工具 | 适用 |
|---|---|---|---|
| **1. 代码正确性** | "这段函数 buggy" | TDD（unit tests）+ lint + types + pre-commit hooks | 任何代码 |
| **2. AI 行为正确性** | "LLM 不遵守 contract / 在边界 case degrade" | **Eval**（rubric + LLM judge + gold set + calibration） | 所有 LLM-driven path |
| **3. 跨 contract drift** | "spec 起草时假设错了，实施时碎掉" | **§六 wiring audit** (我原创 falsifiable prediction loop) | Boundary doc 起草 + retro |
| **4. 单 session 内 reasoning chain bias** | "agent 写完自己 reviews 看不到自己 bias" | Adversarial subagent review with fresh context | 复杂 commit 前 |

**关键观察**：layer 2 和 layer 3 在工业界讨论里**经常被混淆**。Eval
是测**LLM 行为**，§六 audit 是验**人对 contract 的预测**。**两者
falsify 的对象完全不同**。

---

## 4. Eval 深一层（之前讨论过薄，今天补）

### 4.1 Eval ≠ TDD

| | TDD | Eval |
|---|---|---|
| 测对象 | 代码行为（`assert x == y`）| **agent 行为**（trace 满足 rubric）|
| Determinism | 完全确定 | 概率性（多次 trial，pass rate）|
| Grader | 编译器 + assertion | **3 类**（见 4.2）|
| 何时跑 | 每 commit | 每次 model / prompt / harness 改 |
| 失败信号 | 代码 bug | agent 误用 tool / 偏离 contract / 边界 degrade |

公开讨论里很多博客**混用两者**。Anthropic [Demystifying evals for AI
agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
给出 precise 区分。

### 4.2 三类 grader（按可靠性 vs 灵活性 trade-off）

| Grader | 优点 | 缺点 | 用法 |
|---|---|---|---|
| **Code-based** | 快 / 便宜 / 客观 / 可复现 / 可 debug | brittle to 合理 variation | string match / outcome verify / static analysis |
| **Model-based (LLM-as-judge)** | 灵活 / 可扩展 / 捕捉 nuance / 处理 open-ended | 非确定 / 贵 / **必须 calibrate to human gold** | 用 LLM 按 rubric 给 0-5 分 |
| **Human gold standard** | 最准 | 贵 / 慢 / 需要 domain expert | **calibrate model-based grader 的锚** |

**Anthropic 强制要求**：LLM-based rubric **必须**频繁 calibrate against
expert human judgment。calibration 跑不通 = model-based grader 不可信。

### 4.3 Capability eval vs Regression eval（graduate path）

Anthropic 把 eval 分两阶段:

| 阶段 | 目标 pass rate | 用途 |
|---|---|---|
| **Capability eval** | **低**（开发期）| "agent 能不能做这件事" |
| **Regression eval** | **接近 100%** | "agent 是否还能做以前做到的事" |

**Graduate 机制**：当 capability eval 持续 ≥95% pass rate，**升级**进
regression suite。这是 living test suite —— 一开始 fail 是 OK 的
(measuring capability)，stable 后变成 stay-passing constraint。

### 4.4 Self-eval loops（5 个 production patterns）

[Result Loops 5 patterns for production agents](https://dev.to/raxxostudios/claude-result-loops-rubrics-5-self-eval-patterns-for-production-agents-2l07)
给出 5 个**实战 pattern** —— agent 跑完自己按 JSON rubric 打分，
threshold 不过就 retry：

| Pattern | 用在 | Threshold | Retry rate |
|---|---|---|---|
| Blog quality | TLDR / H2 数 / 字数 / banned chars + LLM judge for tone | 0.85 | ~14% |
| **Code PR Gate** | tests / lint / types / no `console.log` | **1.0**（critical 全过）| 30% 第一轮 / 50% 第二轮 pass |
| Email tone | 字数 / 禁词 + LLM judge vs voice sample | 0.85 | 中等 |
| Image prompt | 比例 / banned text + brand palette via LLM | 0.85 | ~25% |
| Bug triage | severity / reproducer 长度 / owner | 0.9 | ~18% |

**调节经验**:
- threshold **0.8-0.85 是 sweet spot**；critical gate 才用 1.0
- max iterations **cap 在 2-3** 防 agent gaming（删 test / 凑字数）
- retry rate > 30% = rubric 太严，< 5% = rubric 没用

### 4.5 Claude Code 团队怎么用 eval（empirical）

Anthropic [demystifying evals 原话](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents):

> "Claude Code started with **fast iteration based on feedback** from
> Anthropic employees and external users. Later, we added evals —
> first for **narrow areas** like concision and file edits, and then
> for more complex behaviors like over-engineering."

**关键 framing**：他们**不是一开始就 eval-driven**。是先 dogfood feedback
loop 跑起来，**当 dogfood 没法 cover 某个 narrow behavior 时**才上
eval。eval 是 **scaling solution**，不是 starting tool。

Anthropic 内部 Claude Code eval **现在的形态**:
- skill-creator agent 帮**写** eval（描述 prompt + good output looks
  like，agent 生成 test case）
- **Parallel independent agents** 跑 eval —— 每个 eval 独立 context
- **Benchmark mode** 跨 model update / skill edit 跟踪 pass rate
- **Comparator agents** 做 A/B testing with blind judging

### 4.6 OH 现在 eval 状态 + 未来扩展空间

**OH 当前 eval 覆盖**:

| Layer | OH 现状 |
|---|---|
| code-based grader (unit tests) | ✅ 2144 tests / pytest |
| model-based grader (LLM judge) | ⚠️ `services/focus_state.py` + `memory_decision` eval 有，但 **没 calibrate to human gold** |
| Human gold set | ❌ 没有 |

**两个 gap**:

1. **没 human gold set** —— 现有 LLM-as-judge eval (focus_state /
   memory_decision) 跑结果**无校准锚**。如果未来 deploy 给真用户，
   model upgrade 时不能验证 "判定结果是否还跟人对齐"
2. **没 capability eval ↔ regression eval 区分** —— 目前 eval 跑就
   pass/fail，没 graduate 机制

**未来 OH 可能引入 eval 的场景**:

- 用户向 OH 贡献新 SKILL.md 时跑 capability eval（这 skill 在
  representative inputs 上是否 surface skill body？）
- Model 升级时（qwen → DeepSeek-V3 → Claude 等）跑 regression eval 防
  drift
- §六 wiring audit verdict mapping 自身可以**进化成 eval** —— 每次
  retro 把 "predicted verdict vs actual" 录进结构化 dataset，跨 phase
  累积 falsifiable accuracy

**当前不做**：用户少 / driver 弱，eval 价值密度低于 §六 audit + TDD
的组合。Phase 21+ 真有 driver 再考虑。

---

## 5. 我从 Google spec-kit 进化到今天的路径

| 时间窗 | 工作流 | 哪些 spec-kit 残留 |
|---|---|---|
| Phase 1-7（早期）| **Google spec-kit 1:1**：detailed plan / sub-task 1a/1b/1c / requirements doc | 全部 |
| Phase 8-15 | boundary doc + plan 两份文件 | plan 还在分 sub-task |
| Phase 16-19 | boundary doc + lean plan（去 sub-unit）+ §六 wiring audit | sub-task 不写了，但仍 2 份文件 |
| **Phase 20+（今天 Shape B）** | **单 boundary doc**（含 task ordering）+ ≤ 230 行 + §六 audit | 残留 0 |

**每次缩减都是认知前进，不是简化为简化**:

- Phase 1-7 → Phase 8：发现 sub-task decomposition 是 AI runtime 的事，
  人不该写
- Phase 8-15 → Phase 16：发现 boundary doc 70% prose 是 spec-kit
  ritual（"Triggering observation" / "HKUDS cite" / multi-paragraph
  alternatives），不写也不影响决策
- Phase 16-19 → Phase 20：发现 boundary doc 跟 plan **30% 内容重复**，
  task ordering 直接进 boundary 一个 section 就够

**核心 invariant 三步进化都不动**：D-numbered ratification + Anti-
scope + Acceptance + §六 audit。这就是工业 spec-driven development 的
本体。

---

## 6. 跟 Claude Code / Codex 团队对比，我独有的部分

**§六 wiring audit + verdict mapping** —— 这是 Anthropic / OpenAI /
Augment / Simon Willison / Addy Osmani 全部公开讨论里**找不到等价物**
的工具：

- Boundary doc 起草时**预测**每个 runtime layer 的影响（4 选 1：
  unchanged / extension / bypass / verification）
- Retro 时**逐项 falsify** 实测
- Phase 17 (10/10) + Phase 18 (13/13) + Phase 19 (15/16 + 1 self-
  corrected pre-T1.1) = 三次累计 prediction accuracy

[Augment Code 2026 明文承认](https://www.augmentcode.com/guides/claude-code-spec-driven-development):

> "**None [of major AI coding tools] is clearly documented to
> automatically verify that the implementation matches the original
> specification.**"

§六 audit 是这个 industry-acknowledged gap 的具体填补。

为什么我做了别人没做：solo + 学习者 + boundary doc 重，强迫每次都
falsify 上次预测才能进步。Anthropic / OpenAI 团队**靠 dogfood 直觉
做了同样的 calibration**，但没显式化成 doc-level falsifiable artifact。

---

## 7. AI 永远不该接管的（边界硬约束）

以下事 AI 可以**协助 draft**，但**最终决定权必须在人**:

| 不接管的事 | 为什么 |
|---|---|
| **Architecture trade-offs** | trade-off 是价值判断，不是技术正确性。"我们要 swap-up models" vs "我们要 vendor lock-in latent state" —— AI 不知道我的长线意图 |
| **Spec at capability level** | sub-task decomposition AI 干很好；**capability 切分**需要 domain context + 商业判断 + 长线对齐，AI 没这个信息 |
| **§六 audit predictions** | predict layer impact 需要对整个 codebase 心智模型，**人持有 mental model 比 AI 的 grep 更整体**。AI 可以 verify 预测，但最初 predict 是人的事 |
| **"前提是不是错了" 的 judgment** | D38.3 → D38.8 reversal、D39.5 → D39.9 reversal 这种都是 "premise wrong" 的人为判断。AI 不擅长怀疑自己的前提 |
| **审 acceptance 的 walkthrough** | "tests passed 但 feature 实际坏" 只有人对照 acceptance criteria 一条条对才能抓 |
| **用户真实意图** | 比如"你 SPEC.md 还有用吗"这种 retro 性的 framing 判断，AI 不会自发问 |

**协作的最高形态**：AI 把 implementation + 大量 grunt work 接管，**人
专注在以上 6 类**。今天我跟 AI 协作 19 个 phase 的真实工作时间分布
≈ 30% 我做（以上 6 类）+ 70% AI 做（代码 + 测试 + draft doc）。

---

## 8. Source notes

### 主要 reference

- [Anthropic — Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [Anthropic — Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)
- [Anthropic — Building effective AI agents](https://www.anthropic.com/research/building-effective-agents)
- [Augment Code — Claude Code for Spec-Driven Development](https://www.augmentcode.com/guides/claude-code-spec-driven-development)
- [Result Loops + Rubrics: 5 Self-Eval Patterns](https://dev.to/raxxostudios/claude-result-loops-rubrics-5-self-eval-patterns-for-production-agents-2l07)
- [Pragmatic Engineer — How Codex is built](https://newsletter.pragmaticengineer.com/p/how-codex-is-built)
- [Pragmatic Engineer — How Claude Code is built](https://newsletter.pragmaticengineer.com/p/how-claude-code-is-built)
- [OpenAI — Harness engineering for Codex](https://openai.com/index/harness-engineering/)
- [InfoQ — Anthropic Agent-Based Code Review for Claude Code](https://www.infoq.com/news/2026/04/claude-code-review/)
- [Latent Space podcast — Claude Code with Boris + Cat](https://www.latent.space/p/claude-code)
- [Skill-creator evals + benchmark + A/B 2026 update](https://www.adwaitx.com/claude-agent-skills-skill-creator-evals/)
- [Anthropic 2026 Agentic Coding Trends Report](https://resources.anthropic.com/hubfs/2026%20Agentic%20Coding%20Trends%20Report.pdf)

### 数据点 anchors

- Claude Code 团队 ~12-15 人；2M+ WAU；$2.5B ARR
- Codex 团队 ~40 人（1 PM + 2 designers + 37 engineers）；4M WAU；
  自写 90%+ 代码；100% PR 内部 Codex review
- Anthropic 80%+ 内部新代码由 Claude 写；engineer merge rate 8× 2024
- Pragmatic Engineer 2026-02 Survey (15K devs): Claude Code 46%
  "most loved"
