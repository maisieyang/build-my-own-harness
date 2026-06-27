# loop-runtime L1 — 无头入口（`oh -p` print mode）

> 模块认知文档（领域专家书一章）。设计立场见 `tasks/loop-runtime-L1-plan.md`，
> 参照系见 `tasks/loop-runtime-autopilot-reference.md`，epic 见 `tasks/loop-runtime-plan.md`。
> 本文 §回顾 由 `debrief` 沉淀（2026-06）。

---

## 实现概要（它实际怎么工作）

`oh -p "<goal>"` 是 loop-runtime 所有外层 loop 的**被调原子**：非交互跑一个 goal、吐结构化结果、给一个 run 级退出码就退。它**没有自己的 engine**——和 `oh chat` REPL 调的是同一个 `run_query`（agent 主循环，`engine/query.py:208-490`，本模块一行没动）。L1 全部落在两层薄壳：

- **入口/装配**（`cli.py`）：`ask` 命令加 `-p/--print` + `--output-format text|json|stream-json`。print 模式下 `_run_ask` 返回终止 `stop_reason`，`ask` 命令据此映射 run 级二档退出码（`end_turn`→0；`max_tokens`/`stop_sequence`/`LoopLimitExceeded`/API 异常→非0）。goal 达成与否**不判**（闸在外，归 L3）。
- **输出层**（`_stream_render.py`）三个新函数：
  - `collect_print_result(events)`：静默 drain（仍驱动工具）、跨 turn 累加 usage、数 num_turns、取末轮 text+stop_reason。供 `--output-format json`。
  - `render_stream_json(events, *, session_id)`：逐事件吐 newline JSON（`assistant_delta`/`tool_started`/`tool_completed`/`retry`），末尾一个 `result` 对象。供 `--output-format stream-json`。
  - `build_result_obj(result, *, session_id)`：**单源化** result 对象形状（`result/stop_reason/usage/num_turns/session_id`，`cost_usd:null`），json 和 stream-json 共用，防两路漂移。

权限：print 模式继承引擎的 fail-closed 行为——只读工具放行，会改东西的工具在非 AUTO 模式被 DENY（`--auto` 圈地放行，敏感路径仍 Tier-1 硬拦）。

交付 commit（main）：`89afc2c`(T1+T2) → `80b9725`(T3+T4) → `327c840`(T5) → `f8da355`(完成记录) → `2d752c0`(debrief 修 retry bug)。全程 2167 passed、cov 95.11%、`mypy --strict src/` clean。

---

## 关键决策与 trade-off（含隐式决策补录）

**设计阶段明确锁的（interview 4 立场）**：① 退出码 run 级二档、闸在外；② 权限 read-only 放行/mutating fail-closed/permission_mode 透传；③ JSON 粒度薄、cost_usd:null；④ 复用 `ask` 加 `-p`。

**实现期浮现、debrief 补录的隐式决策**：

- **`_stream_render` 三函数切分**：对应两个真不同的消费者（静默 drain vs 逐事件 emit）+ `build_result_obj` 单源化形状。会再这么选。小 nit：两个收集器有 ~10 行累加逻辑重复，YAGNI 未抽。
- **`_run_ask` 返回 `None→str|None`，退出码映射留在 CLI 层**：选"_run_ask 报事实（终止 stop_reason），政策（退出码）归 `ask` 命令"，而非在 `_run_ask` 里 `raise typer.Exit`。和现有"异常→退出码"链同源，`_run_ask` 保持 policy-free。会再这么选。
- **stream-json 事件命名表**：选**人话名**（`assistant_delta` 等）而非镜像引擎类名——对外协议稳定、引擎改类名不破 stream-json。`ApiMessageCompleteEvent` 不发行只累加（num_turns 在末尾 result 给）。
- **T1 guard "not yet available" 脚手架**：text 放行、json/stream-json 逐步解禁。增量交付的诚实形态（拒绝"接受但 noop"的假实现）。
- **session_id 现铸机制**：`new_run_id()` 在 print 边界新铸，而非读引擎 `bind_run` 的 run_id——因为"engine 不动"，读引擎 run_id 得改 `run_query`（越界）。给定约束会再这么选，但它加深了下面那个名实之差。

---

## 被证伪的设计判断（保持原立场，记 correction）

- **T5 立场②"修上游两坑"被推翻**。plan 把 T5 写成"修 `run_print_mode` 的 noop-auto-allow + 不透传 permission_mode"——但这俩 bug **本 repo 不存在**：v1 引擎本就 fail-closed（`query.py` Three-Axis G：`ASK+非AUTO→DENY`），`_run_ask` 本就透传 permission_mode + 用真 `TierBasedPermissionChecker`。T5 从"build"塌成"satisfied-by-design + 回归测试锁定"，没编假 RED。
  - **认知偏差（会复发）**：我让 **§参照系（autopilot 逆向）过度锚定了 L1 设计**——把"上游 print 模式怎么坏的"当成了"v1 也这么坏"的待办。
  - **纠正纪律①（参照系→plan 翻译）**：每条"REFERENCE 这么解 / 有坑 Y"落到 plan 必须变成"**核实**目标有没有 X/Y"，**绝不**直接写成"实现/修 Y"。参照系给的是领域里有哪些问题、某实现怎么解的，**不**给目标现状。
  - **纠正纪律②（T0 scope）**：T0 spike 要覆盖 plan 依赖的**每一条参照系前提**，不只未知的**数值**。本次 T0 只对准了"cost_usd 算不算、session_id 有没有"，漏了"T5 的权限前提"——所以 cost_usd 被 T0 抓住（正面案例），T5 拖到执行期才抓住（T0 scope 太窄的反面案例）。同一条"核实前提"纪律，一次守住一次没守住。

- **立场③ cost_usd 假设了"有 cost"**。interview 选"带 usage/cost"时默认 v1 会算钱；T0 勘探推翻（v1 无定价层，只有 token）→ 降级 `cost_usd:null` + follow-up，不硬编价目。这是纪律②的正面案例。

---

## 行业对比的新理解（建完再看，懂了"为什么"）

- **上游 OpenHarness `run_print_mode` vs 你的 v1——懂了架构差在哪**：上游在 **print 那层**塞了便利捷径（`_noop_permission→True` 自动放行，方便 CI），结果 print 模式**绕过**了安全基线、还漏传 permission_mode。你的 v1 把权限决策放在**引擎**里（Three-Axis G），于是**每一个外壳**（REPL、`-p`、将来的）都**免费继承** fail-closed。**教训**：安全决策放引擎 → 外壳无脑继承；放外壳 → 各外壳各自为政、迟早分叉。这是 L1 "engine 不动、薄壳复用"能直接拿到正确权限行为的根因。

- **Claude Code headless（`claude -p`）**：① 它的 `session_id` 是**真能 `--resume` 续的**；我们的 `session_id` 借了它的**名**（脚本迁移友好）却没有它的**能力**（per-run、不可续）——这正是下面那个名实之差的来源："抄了标签没抄能力"。② CC 的 json 带 `total_cost_usd`（per-invocation 给脚本读）；我们 `cost_usd:null`（无定价层）。③ CC 的 `-p` 默认仍会问、要 `--allowedTools` 预授权才无人值守；我们用 `--auto` 达到同一效果（圈地）。

- **Agent SDK `query()`**：进程内跑 agent 循环 = 我们 `_run_ask→run_query` 的形态。L1 的 `oh -p` 就是这个第一方样板的手搓版。

---

## Open questions + 对后续模块的预判

**Open（已留 follow-up，不阻塞 L1）**：
1. **`cost_usd`**：等定价层（Phase 4 cost-cap）落地再填；现按 token 估，L4 预算栏暂用 token。
2. **`session_id` 名实之差**：现在 per-run 新铸、不可 resume、不与引擎日志 run_id 关联。要 resume / 关联需改 engine。是"留这个名 + 将来接 resume"还是"改叫 run_id"——悬而未决（用户中途质疑过）。
3. **stream-json 事件名**：已定"人话名"；若将来引擎事件种类暴涨，再评估是否值得镜像。

**debrief 补的真教训（写给下个模块）**：
- **guard/错误分支的测试必须跟任务同包**——T1 加 `--output-format` guard 时没同步写测试，cov 掉 0.06%、靠质量门补回。不许把"边界拒绝"的测试拖到后面。
- debrief 本身的产出：挖 stream-json taxonomy 时发现 `ApiRetryEvent` 被错误地 type 成 `error`（误导 L4 把"重试中"当"失败"），TDD 修掉（`2d752c0`）。**模块级回顾能抓住执行期 review 漏掉的语义 bug。**

**对后续模块的预判（留待下一轮回顾证伪）**：
- **L2（权限·loop 策略）会是真 build**（不像 T5）：autopilot 参照系显示 v1 只有 `default/plan/full_auto` 三档，**没有 acceptEdits 细粒度档**——L2 要真加这一档。**但**——记住纪律①：动手前先 T0 核实 v1 当前权限现状，别又把参照系当待办。
- **L3（验证闸）要 fail-closed**：autopilot 参照系暴露其 `_looks_available` 会让默认 policy 在外部仓库**空过当成功**。L3 必须"零步=未验证"，不能空过。

— 2026-06 · L1 debrief
