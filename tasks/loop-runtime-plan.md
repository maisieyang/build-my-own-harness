# loop-runtime — 把 harness 从「交互式 CLI」升级成「能跑无头 loop」（plan）

> **地位声明**：这**不是** SPEC v1 的一个 phase（v1 已于 2026-05-20 冻结，见
> `learnings/phase-7.md`）。这是 v1 冻结之后的**新 epic**，capability 级、留档不删。
> 认知来源：`docs/ideas/from-prompt-to-loop-2026.md`（三把椅子）。
> §参照系：`loop-runtime-autopilot-reference.md`（逆向上游 `autopilot` 子系统，2026-06 补）——
> 见本文 **§7 参照系回填**。
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
| **L3′** | 语义验证闸（soft gate） | **验证**（与 L3 平级） | 新（≈§3.8 evaluator，对标 CC `/goal`） | L1 | 跟 L3 平级的第二种验证闸：`--goal-condition` 吃自然语言完成条件，由**独立 LLM 裁判**读 transcript 判 yes/no + 理由——给判不了退出码的语义/主观标准用（文档完整性、格式规范）。跟 L3 互斥，二选一喂给 L4。**已实现**，见 `loop-runtime-L3-goal-plan.md`。 |
| **L4** | 外层 loop（Ralph 式） | **规划/编排** | §3.1 + §3.8 | L2 + L3/L3′ | 拿 goal → 跑内层 engine → 过 L3 或 L3′ 验证闸 → 未达标则**新开 context、把验证反馈重喂** → 直到达标 / 撞**迭代或预算上限**。撞上限即停并报告。 |

后置（MVP 之后，按需）：

| # | 模块 | 椅子 | 对应 § | 依赖 | capability |
|---|---|---|---|---|---|
| L5 | 规划器（模型自拆） | **规划** | §3.8 | L4 | 让模型把大 goal **自拆**成子目标（复用 `SpawnAgent`），替代人脑预拆。对应 Claude Code 的 `/batch`。 |
| L6 | 触发器 | 触发 | §3.9 | L4 | cron / git 事件 / API / 手动。**MVP 只要手动 kickoff**，自动触发后置。 |

> ⚠ **2026-06 回填**：逆向上游 `autopilot` 后，本表漏列了**四块**（L6 不只是后置触发器，还另有 worktree 隔离 / 人机交接边界 / 状态机+journal）——见 **§7**。

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
>
> **2026-06 update**：module loop 第 0 步（参照系 → reverse-spec）**已完成**，产出
> `loop-runtime-autopilot-reference.md`。下一步仍从 L1 起，但 L1 的 interview-me 要把
> reference §5 的"上游教训"带进 trade-off（见 §7.3）。

---

## 7. 参照系回填（2026-06 update · 逆向上游 `autopilot` 之后）

> 来源：[`loop-runtime-autopilot-reference.md`](./loop-runtime-autopilot-reference.md)——逆向
> HKUDS/OpenHarness `autopilot` 子系统（`main` @ `9b2efd7`）。**核心结论：上游已有这条 epic
> 的完整参照实现**（issue/idea → worktree → agent → returncode 闸 + CI → repair 循环 → PR
> 状态机，cron 驱动）。§1「v1 缺外层 loop」对**你的** build-my-own-harness 仍成立，但补一句：
> **外层 loop 在上游有现成的、可逐行抄思路（非抄码）的范本。**

**1) §2 的 L1-L4 全部命中上游参照**（指针见 reference §5）：L1←`run_print_mode`、
L2←`permissions/checker`、L3←`_run_verification_steps`、L4←`run_card`+`_prepare_repair_prompt`。
建造每块时对着读。

**2) 逆向暴露了本 plan 漏列的四块——补进 capability 地图**：

| # | 新模块 | 椅子 / 边界 | 上游参照（要素→文件） | 为什么不是后置/不变量 |
|---|---|---|---|---|
| **L6′** | 触发 = intake 评分队列 + cron 守护 | 触发 | 要素①+⑥ · `scan_*`/`_score_card`/`cron_scheduler` | 原把 L6 当"后置、手动 kickoff"；上游证明 `$GOAL` 真源头 = 评分队列，是自治核心入口。MVP 仍可手动，但模块要预留这个 seam |
| **L7** | worktree 隔离执行场 | 把关（沙箱兜底的具体形态） | 要素② · `swarm/worktree.py` | 原 §4 只当不变量；上游证明它是 L2 敢无人值守放手的**物理前提**，该单列模块 |
| **L8** | 人机交接边界 | 把关（自治边界） | 要素⑦ · automerge / human-gate / `stop_on` | PR-not-merge + 不可逆动作清单是独立设计，不只一句不变量 |
| **L9** | 状态机 + journal（可恢复/可观测） | — | 要素⑧ · 13 状态 / `update_status` / journal | 无人值守离不开"状态外置"；原 plan 完全没提 |

**3) 三处把 §1/§4 的设想校准成上游实证**：
- §4 不变量 #1（gate 非 prompt）→ 上游用 **returncode + 真 CI 双硬闸**坐实；但其 L3 默认 policy
  被 `_looks_available` 按仓库标志物筛，**外部仓库会空过当 success**——**你的 L3 要 fail-closed**
  （零步 = 未验证，而非通过）。
- §4 不变量 #2（fail-closed）→ 上游唯一不可覆盖底线是 `SENSITIVE_PATH_PATTERNS`，
  `denied_commands` 默认**空**。**L2 别假设有内置危险命令护栏，要自己定。**
- L1 教训：上游 `run_print_mode` **不透传 permission_mode、不自定退出码**——**你的 L1 务必把
  "成/败退出码"做实**（这恰是 §2 对 L1 的硬要求，上游反而没守住）。

**4) module loop 第 0 步（参照系）就此完成**，下一步仍从 L1 起（§6）。

---

## 8. L3′ 已实现（2026-07 update）

真正的 `/goal`（语义 condition + LLM 裁判）已建成——见 §2 表格新增的 L3′ 行，实现记录、
锁定立场、任务分解、冒烟验证全在独立文档：[`loop-runtime-L3-goal-plan.md`](./loop-runtime-L3-goal-plan.md)（跟 L1/L2 各自有独立 `-plan.md` 是同一惯例）。

---

## 9. L4-L9 剩余项：为什么要做 + 依赖/耦合分析（2026-07 update）

L1-L4（含 L3′）已全部落地。§7 回填的 L6′/L7/L8/L9，加上 L4 自己留的两条已知限制，是
epic 剩下的部分。每一项"为什么要做"用同一条链子讲：**朴素方案（现状）→ 暴露的问题 →
下一修复**。这条链子本身是跟用户讨论沉淀出来的，比单纯罗列"缺什么"更能回答"不做行不行"。

### 9.1 每一项的问题链（为什么要做）

**L5 规划器自拆**
- 朴素方案：L4 outer loop 只会反复跑**同一个** goal，goal 从头到尾不变、不拆分，规划这
  一步（大目标怎么拆成几步）完全靠人自己想清楚再喂给 `oh -p`。
- 暴露问题：① goal 太大时单轮做不完（受 `max_turns` 限制，撞上限即"没成功"，但 L4 的
  重喂逻辑不会把目标拆小，agent 每轮还是面对同一个庞然大物，收敛很慢甚至原地打转）；
  ② 验证闸只能一次性判断终态，没法表达"先验证子任务 A，再做子任务 B"这种里程碑式推进；
  ③ 人还在充当"规划者"——三把椅子里"验证"和"执行循环"腾空了，"规划"没有。
- 下一修复：让模型自己把大 goal 拆解成子目标序列（复用 `SpawnAgent` 递归委派原语，对应
  Claude Code 的 `/batch`），每个子目标可能各自走一次 L4 的小循环。

**L6 触发器（intake 评分队列 + cron 守护）**
- 朴素方案：人自己在终端敲 `oh -p "goal" ...`，goal 是人脑现想的一句话，"现在要跑这个"
  完全靠人手动决定、手动触发。
- 暴露问题：① 没人在，就不会有新循环被触发——`oh` 不会自己醒来检查"现在有没有该做的
  事"，真无人值守连"触发"这件事本身也得自动化；② goal 从哪来是个黑箱，没有稳定、结构化
  的来源（issue/PR/idea 散落各处，人得自己翻）；③ 候选多了要排优先级，不然重要的会被拖到
  最后。
- 下一修复：intake 评分队列（异构来源统一入队，启发式规则打分——源头基础分 + 标签 +
  新鲜度衰减，**不靠模型判断**，零 token、确定性、可解释）+ cron 守护（定期扫队列，把
  排名最高的候选自动喂给 L4）。

**L7 worktree 物理隔离**
- 朴素方案：L4 每轮 attempt 直接调 `_run_ask`，`cwd` 就是用户敲命令时所在的真实工程目录，
  agent 在这个目录里原地改文件。
- 暴露问题：① 半成品会叠加——attempt 1 没过验证，attempt 2 是在"已经改乱的工作树"上继续
  改，不是从干净状态重新开始；② 没有"作废重来"机制——改动原地叠加，没法简单回到干净状态；
  ③ 爆炸半径没兜底——无人值守场景下撞上限，工作区已经被改脏，得手动清理；④ 没法并行——
  多个 attempt/多个 goal 同时跑会互相踩踏同一批文件。
- 下一修复：每次（或每个 session）新开一个 git worktree（共享 `.git` 对象库、独立工作
  目录/分支），每轮/每个 session 都从干净基线开始；失败了直接删 worktree，真实目录完全
  没被碰过；天然支持并行；只有验证通过的改动才 merge 回真实分支。
- **补一句区分**：这跟 `--sandbox`（execution 层的 Docker/gVisor 隔离，L2 已有）是两个不同
  维度——`--sandbox` 隔离的是"命令跑在什么容器里"，容器里挂载的还是同一份 cwd 文件系统；
  worktree 隔离的是"改动落在哪个 git 工作树"。L4 现在两层都没绑定，即使开 `--sandbox`，
  agent 改的还是同一份实际仓库文件。

**L8 人机交接边界**
- 朴素方案：改动完停在工作区里，不会自动 commit；commit 这一步靠执行者（目前是 Claude
  Code 本身）自觉遵守"人审 diff → 问要不要 commit → 说了才 commit"，**不是代码层面的硬
  约束**。
- 暴露问题：① 全靠执行者自觉——换一个不遵守这条约定的执行者（比如 L6 触发的全自动 loop，
  没有人过一遍 diff 才 commit 这一步），系统可能会一路 commit 下去，没人真正看过改了什么；
  ② 没有一份"不管谁下指令都碰不了"的不可逆动作清单（force push / 自动 release 等），目前
  只是文档层面的软约束，不是像 L2 Tier1 红线那样撬不动的硬约束。
- 下一修复：upstream 的做法是 PR-not-merge（loop 跑完验证过 → commit+push+开 PR → 按
  label 决定自动合还是等人 review）——但**这个仓库没有 PR 工作流**（直接在 main 上迭代，
  没有 fork/开分支给 PR 审），"开 PR 当交接点"这个具体机制没有对应物可挂。要落地得换一个
  不依赖 PR 的载体：把"commit/push 这类动作永远走一个显式确认门"做成代码层面的硬约束
  （类似 L2 Tier1 那种红线），而不是像现在这样靠执行者自觉遵守。

**L9 状态机 + journal**
- 朴素方案：整个 repair loop 是一次性、同步的一次函数调用，全程状态只活在内存里的局部
  变量，跑完了只在最后 echo 一个 json，中途没有任何东西落盘。
- 暴露问题：① 进程一断，全部丢失——机器重启/进程被 kill/手滑 Ctrl-C，"跑了几轮、上一轮
  发生了什么"全部消失，没法"从第 N 轮继续"，只能从头重来；② 黑盒，没法中途观测——没地方
  查"现在跑到哪了"，这跟"无人值守"场景是矛盾的；③ 无法防重复触发——L6 的 cron 真按点
  触发时，同一个 goal 被触发两次，没有机制识别"已经在跑了，别重复启动"。
- 下一修复：显式状态机（`queued → running → verifying → repairing → completed/failed`），
  每次状态变化落盘成 append-only journal，每轮验证报告也存成可读文件。核心原则：**只要是
  无人盯着跑的东西，状态就必须外置到进程之外**。

**L4 已知限制①：`--sandbox` 场景每轮重启容器**
- 这是设计 L4 时用 AskUserQuestion 问过、选了"每次迭代重新调用 `_run_ask`"（而非"整个
  session 只装一次 sandbox/MCP pool"的深度重构）之后的必然代价：每轮都重新拉起一次容器
  （image inspect/pull + create + start），N 轮下来容器启停开销可能占大头。接受原因：
  深度重构要把 `_run_ask` 约 230 行装配逻辑拆成可复用 helper，回归面大；效率不是 epic
  锁定的不变量（fresh context/fail-closed/迭代上限硬栏才是）。

**L4 已知限制②：`--resume`/`--max-iter` 互斥**
- code review 揪出：同时传两者会导致每轮偷偷把上一轮刚写的 snapshot 加载回来，破坏
  "fresh context"这条核心不变量。当时选择直接在校验层禁止组合（报错退出 2），而不是设计
  更精细的"resume 只作用于第一轮"融合语义——这个组合场景本身没有验证过真有需求，属于
  YAGNI，宁可禁止组合报清晰错误，也不要悄悄跑出违反设计初衷的行为。

### 9.2 依赖/耦合分析：哪些能并行，哪些不能

**Track A——相互独立，可以并行**（新文件/新模块，不碰 `_run_ask`/`_run_repair_loop` 内部）：

| 模块 | 落点 | 为什么独立 |
|---|---|---|
| L5 规划器 | 新文件（自拆逻辑）+ 薄 CLI 接线 | 只是"调用现成的 L4 N 次，每次喂不同子目标"，不需要改 `_run_repair_loop` 内部 |
| L6 触发器 | 全新子系统（intake 队列 + cron），新 CLI 子命令 | 最终只是"程序化地调 `oh ask -p ...`"，不碰 `ask` 命令内部 |
| L8 人机交接边界 | `permissions/` 层加一条不可覆盖的红线 | 落在权限模块，不是 loop 控制流 |

**Track B——同一块肌肉，必须一次统筹地做，不能拆给独立并行任务**：

| 模块 | 为什么耦合 |
|---|---|
| L4 限制①（sandbox 重启） | 要解决就得把 `_run_ask` 的装配逻辑拆成"整个 session 只建一次"——最底层的重构 |
| L7 worktree 隔离 | 本质是同一个重构的另一面：不止"复用 sandbox"，还要"复用/隔离 worktree"，落点是同一个装配函数 |
| L9 状态机 + journal | 要在 `_run_repair_loop` 每一轮的状态转换点插入落盘调用——直接改同一个循环体 |
| L4 限制②（resume/max-iter） | 范围更小，但也是改 `_run_repair_loop` 的 prompt 构造逻辑，同一片代码 |

四个如果各起一个独立 `/goal` 并行跑，会同时改同一段代码——不只是"合并冲突"，是四套互不
知情的设计会互相打架（L7 想包一层 worktree、L9 想在同一循环里插状态钩子，两边对"循环体
该重构成什么样"各有主张）。这一组该合成一次统筹设计（先做"整个 session 只装配一次执行
环境"的底层重构，L7/L9/限制②都长在这个新底座上），不是四个并行任务。

### 9.3 执行顺序决定（2026-07-01）

- **Track A 先做，且先从 L6 + L8 起**（L5 看这两个跑完的结果再决定要不要现在做）。
- **Track B 等 Track A 落地后再回头做**，作为一次统筹设计，不拆分并行。

### 9.6 Track B 统一设计已批准（2026-07-03 update）

Track A（L5/L6/L8）已全部落地。Track B 四项（L7 worktree 隔离 + L4 限制①sandbox 复用 +
L9 状态机/journal + L4 限制②resume/max-iter 融合）经三个并行 Explore agent 摸底 +
一个 Plan agent 出稿 + native Plan Mode 批准，设计已定稿，实现未开始。完整设计见独立文档
[`loop-runtime-trackb-plan.md`](./loop-runtime-trackb-plan.md)（跟 L1/L2/L3'/L5 各自有
独立 `-plan.md` 同一惯例）。

**核心决策摘要**：新增 `services/run_session.py::open_run_session(...)` 作为四项耦合的
公共宿主（worktree + sandbox 容器的 `AsyncExitStack` 从 `_run_ask`/`_run_repair_loop`
提到 `ask()` 的分发点）；`_run_ask` 只新增 `cwd_override`/`execution_env_override` 两个
通用参数（不是四个专用参数），靠既有的 `**run_ask_kwargs` 透传机制免费传播到每个
attempt/子目标；新建 `services/worktree.py`（git worktree，fail-closed on 脏工作区）+
`services/run_journal.py`（append-only JSONL + 原子 `state.json`，机制抄
`autopilot.py`/`snapshot.py`，拓扑不抄）；L4 限制②不做"融合"，另开正交的
`--resume-run <id>` flag（resume 一次 run 的循环入口状态，不是 resume 会话历史）；
`--isolate` 跟 loop 场景**解耦**（参照 Claude Code 自己 `Agent`/`Workflow` 工具的
`isolation: "worktree"` 设计校准），任意 `-p` 调用都能单独开；worktree 清理策略精化为
"没改动自动清理、有改动永不自动删"（因为 L8 已无条件拒绝 `git commit`/`push`，worktree
里只会是未提交改动，没有"要不要自动合并"这个问题）。TDD 切片 T0-T7（+ 可选 T8）按
Wave 0-3 排序，Wave 1（T1 worktree ∥ T2 journal）和 Wave 2（T3 override ∥ T4
run_session）各自能真并行 `/goal`，Wave 3（T5→T6→T7）因为都改
`_run_repair_loop`/`ask()` 同一段代码，必须单人顺序做。

### 9.7 Track B 已实现（2026-07-03 update）——loop-runtime 主线 epic 收口

按 §9.6 的设计，Wave 0→3 全部落地，三次 commit（`919e4a7`/`017d20d`/`98d92e2`/
`3c43ddc`），每个 Wave 后都过了一轮高强度 workflow code review 并修复发现的问题
（Wave 1 修 8 个、Wave 2 修 6 个、Wave 3 修 5 个 + 1 个记录为已知限制不修）。最严重的
一次发现在 Wave 3：`--resume-run` 设计时依赖的 `state.json` 持久化路径
（`RunJournal.write_state`）在生产代码里其实从未被调用过——6 个测试全靠测试自己手写
`state.json` fixture 才通过绿灯，真实中断的 run 永远无法 resume；修法是把 `--resume-run`
改成完全只读 append-only 的 `journal.jsonl`（生产环境确实在写），并补了一个端到端回归
测试——先跑一次真实耗尽 attempt 的 repair loop（不用任何测试 fixture），再对**那次真实
run** 调 `--resume-run`，证明整条链路真的打通，而不是只在手工搭建的场景下"看起来能跑"。

全量测试 2466 passed，`mypy --strict` 129 文件全过，`ruff` 全过。完整落地记录见
[`loop-runtime-trackb-plan.md`](./loop-runtime-trackb-plan.md) §8。

**Track A + Track B 全部完成——loop-runtime 主线 epic（L1-L9 + L3'）至此收口。**

— 2026-06 plan（capability 级 · 留档不删；§7 为 2026-06 参照系回填，§8 为 2026-07 L3′ 落地
回填，§9 为 2026-07 剩余项问题链 + 并行策略回填，§9.6 为 2026-07 Track B 统一设计批准
回填，§9.7 为 2026-07 Track B 落地回填 · epic 收口）
