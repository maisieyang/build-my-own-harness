# loop-runtime — 把 harness 从「交互式 CLI」升级成「能跑无头 loop」（plan）

> **地位声明**：这**不是** SPEC v1 的一个 phase（v1 已于 2026-05-20 冻结，见
> `learnings/phase-7.md`）。这是 v1 冻结之后的**新 epic**，capability 级、留档不删。
> 认知来源：`docs/ideas/from-prompt-to-loop-2026.md`（三把椅子）。
>
> ⚠️ 一个目录身份的小张力：`tasks/README.md` 说本目录是"17 个已发 phase 的冻结历史，
> 不是 roadmap"。这份前瞻 plan 放这里是按你指定的路径；若你想把"已发历史"和"前瞻
> epic"分开，可迁到 `roadmap/` 或 `tasks/v2/`。先按你说的放这。

---

## 0. 一句话目标

- **现在**：产品形态是 CLI 交互。规划、验证、把关三把椅子，**每一轮都人在坐**。
- **目标**：能 `oh -p "<goal>"` **无头**跑一个**自验证、自重喂、有迭代上限**的 loop，
  把三把椅子腾给 harness——人写一次 goal + 验收，然后走开。

---

## 1. 现状盘点：loop 要的「内层原语」，v1 基本都有了

这是这份 plan 最重要的发现——**你不缺地基，缺的是地基之上的那一圈外层 loop。**

| 九要素 | v1 现状（已发 phase） | loop 要它干什么 | 差距 |
|---|---|---|---|
| §3.1 Agent 循环 | ✅ `engine` `run_query`（phase 2） | 内层 `while stop_reason` 单次跑通 | **缺外层**「验证→重喂」那一圈 |
| §3.3 工具 | ✅ `tools`+`ToolRegistry`（phase 2） | loop 内动作 | 够用，无需新建 |
| §3.5 安全边界 | ✅ 三档权限（phase 3）+ Docker/gVisor sandbox（phase 7） | 无人在场也能安全放行 | **缺**一个「无 TTY 时怎么办」的 loop 权限模式 |
| §3.6 持久记忆 | ✅ `memory`（phase 7 索引项） | 跨轮/跨 session 状态落盘 | 可复用；重喂时按需读 |
| §3.8 多 Agent | ✅ `SpawnAgent` 递归委派（phase 6） | 规划器 / 验证器分离 | 有原语，可复用，非新建 |
| §3.9 接触面 | ✅ `cli` + `oh chat` REPL（phase 4 / 6+） | 无头入口 | **缺** `-p` 非交互模式 + 结构化输出 + 退出码 |

> 结论：要建的新面只有**四块**——无头入口、loop 权限模式、验证闸、外层 loop。
> 其余全是**复用 v1 已冻结的原语**。

---

## 2. 要建的：三把椅子 + 入口 → 四个 capability 模块（按依赖排）

沿用 §5 的"依赖列"排法。**capability 级，不下沉实现。**

| # | 模块 | 腾哪把椅子 | 对应 § | 依赖 | capability（要交付的行为，不写怎么实现） |
|---|---|---|---|---|---|
| **L1** | 无头入口（print mode） | —（前置） | §3.9 扩展 | v1 `cli` | `oh -p "<goal>"`：非交互、不开 REPL、读一个 goal、跑完吐**结构化结果**（json / stream-json）、**退出码区分成/败**。被脚本和外层 loop 调用的原子。 |
| **L2** | 权限·loop 策略 | **把关** | §3.5 扩展 | L1 + phase 3/7 | 无 TTY 时**不弹窗**：按声明式 policy 放行（allowlist / acceptEdits / 只拦不可逆）；危险/不可逆动作仍留闸或直接禁；**沙箱兜底**复用 phase 7。默认 **fail-closed**。 |
| **L3** | 验证闸（exit condition） | **验证** | 新（≈§3.8 evaluator） | L1 | 跑一个**可执行 check**（命令 / grader agent），读结果，产出「达标 / 未达标 + 反馈文本」。这是 loop 的**停止判据**，必须确定性可读，不靠模型自我感觉。 |
| **L4** | 外层 loop（Ralph 式） | **规划/编排** | §3.1 + §3.8 | L2 + L3 | 拿 goal → 跑内层 engine → 过 L3 验证闸 → 未达标则**新开 context、把验证反馈重喂** → 直到达标 / 撞**迭代或预算上限**。撞上限即停并报告。 |

后置（MVP 之后，按需）：

| # | 模块 | 椅子 | 对应 § | 依赖 | capability |
|---|---|---|---|---|---|
| L5 | 规划器（模型自拆） | **规划** | §3.8 | L4 | 让模型把大 goal **自拆**成子目标（复用 `SpawnAgent`），替代人脑预拆。对应 Claude Code 的 `/batch`。 |
| L6 | 触发器 | 触发 | §3.9 | L4 | cron / git 事件 / API / 手动。**MVP 只要手动 kickoff**，自动触发后置。 |

---

## 3. MVP 切线（最小能自己跑的一圈）

**L1 + L2 + L3 + L4**，目标场景 = 经典 Ralph loop：

```
oh -p "修好所有失败测试，只改源码别动断言" \
   --verify "pytest -q" \
   --max-iter N            # 无人值守，跑在 sandbox 里
```

跑通这一条 = 三把椅子第一次被腾空。L5（自拆）/ L6（自动触发）等这条绿了再上。

---

## 4. 要守的不变量（边界 · 写在动手前）

1. **验证闸是 gate，不是 prompt**：达标判据必须**可执行 + 确定性可读**（退出码 / 结构化），
   绝不退化成 prompt 里一句"请确保测试过"。这是「写 skill」和「写 loop」的分界线。
2. **无头默认安全（fail-closed）**：无 TTY + 无 policy = **拒绝**危险动作，不是默默放行。
3. **fresh-context 重喂**：每轮只重喂 goal + 验证反馈 + 必要状态（落盘的文件），**不堆历史**。
   注意这是**主动重置**，区别于 §3.4 你已有的 microcompact（被动压缩）——两者机制不同，别混。
4. **迭代/预算上限是硬栏**：撞上限即停、报告、退出码非零。**绝不无声烧**
   （对应 Vovance 的 "confident token furnace" 警告）。

---

## 5. 横切：哪几块要走 eval

按 CLAUDE.md——改动触碰 prompt / 概率性行为 → `eval`（draft）。本 epic 里：

- **L3 验证闸**用 grader agent 那一支 → LLM-judge，**必走 eval**（判过没过这件事本身要可信）。
- **L4 重喂 prompt**的措辞影响收敛 → 概率性，建 eval 盯"会不会越重喂越偏"。
- **L5 规划器**的自拆质量 → 概率性，eval 盯拆解合不合理。
- L1/L2 是确定性的（入口、权限策略），确定性测试即可，不需 eval。

---

## 6. 下一步（module loop 的入口）

按 CLAUDE.md 的模块循环：这份 capability plan 是**发散完能回来的锚**。
动手第一步——对 **L1（无头入口）** 起 `interview-me` 想清角色/trade-off → `/plan` 细化成
`tasks/loop-runtime-L1-plan.md` → TDD 实现到绿 → `debrief`。L1 绿了再走 L2。

> 为什么从 L1 起：它是所有外层 loop 的**被调用原子**，且**确定性、可端到端验证**——
> 正好复刻 v1 当年"phase 4 先把 CLI 跑通做最小入口"的那条经验（§5 模块 4）。

— 2026-06 plan（capability 级 · 留档不删）
