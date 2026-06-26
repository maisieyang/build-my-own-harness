# loop-runtime L1 — 无头入口（print mode）实现 plan

> **上游**：`loop-runtime-plan.md`（epic capability 地图）+ 本模块的 interview-me 已确认意图。
> **参照系**：`loop-runtime-autopilot-reference.md`（上游 `run_print_mode` 三个坑）。
> **纪律**：TDD 是脊梁——每个任务**先写测试见 RED，再到 GREEN**；测试是 spec，挂了改代码不改测试。
> commit 前出示 diff 等点头。**留档不删。**

---

## 0. 这个模块交付什么（一句话）

给现有 `ask` 命令加 `-p/--print`，让 `oh -p "<goal>"` 成为一个**无头纯原子**：非交互跑一个
goal → 吐结构化 JSON → 给 **run 级**退出码 → 退。被脚本和外层 loop（L4）调用。

**四个锁定立场**（来自 interview，作为不可漂移的验收基线）：
1. 退出码 = **run 级二档**：`end_turn`→`0`；错误/撞 `--max-turns`→非0。原因进 JSON（**闸在外**，goal 达成归 L3）。
2. 权限 = **read-only 放行 / mutating fail-closed DENY / permission_mode 透传**（修上游两坑）。
3. JSON 粒度 = `result, stop_reason, usage, num_turns, session_id`（薄 + 刚好喂得动 L4 预算栏）。
4. 入口 = 复用 `ask` 加 `-p/--print`，不新建命令；`--output-format text|json|stream-json`。

**Out of scope**（写死，越界即停）：L2 富策略（allowlist/acceptEdits/sandbox）、L3 验证闸、
L4 loop、L5 自拆、L6 触发。**L1 只透传 permission_mode 旋钮，不实现策略。**

---

## 1. 落点（你的 repo · capability 级参照，不当死约束）

```
cli.py: ask 命令（现有一次性路径，flag: model/max_tokens/auto/dry_run/log_level）
   └─ _run_ask  ──→  run_query / submit_message（engine，不动）──→ render_stream（输出层）
                                                          ↑
                          ApiMessageCompleteEvent{usage, stop_reason} · ConversationCompleteEvent
   权限：TierBasedPermissionChecker.evaluate(..., is_read_only) → Decision{ALLOW/DENY/ASK}
        （注释："caller decides ASK vs DENY semantics" ← L1 的 fail-closed 缝）
```

L1 ≈ ① `ask` 加 `-p` + `--output-format` flag；② 输出层加 json / stream-json 分支；
③ 退出码在入口按 run 级二档做实；④ 无 TTY 时 permission 按 read-only/mutating 分流 + 透传 mode。
**engine 一行不动。**

---

## 2. 任务（垂直切片 · 按依赖排 · 每个 TDD 到绿）

### T0 — 缝勘探（只读，无码改 · 解两处空缺）
- **干什么**：确认两个 interview 立场依赖、但 grep 存疑的事实：
  1. v1 **是否计算 `cost_usd`**，还是只有 token 数？（grep 仅见注释 "Phase 4 may add cost-cap"）
  2. v1 **是否有 `session_id`**？没有的话 L1 自己生成还是省略？
  3. `ApiMessageCompleteEvent.usage` 的确切形状；多 turn 时 usage 怎么累加成 `num_turns` 和总量。
- **产出**：本文件追加一节《T0 缝勘探结论》，据此**校准 T3 的 JSON 字段验收**（cost_usd 算/不算、session_id 带/不带）。
- **验收**：三个问题各有"代码出处 + 结论"一行；不写码。

### T1 — `-p/--print` + `--output-format text`（最薄垂直切片）
- **RED**：写测试 `test_print_mode_text_passthrough`：以 `-p "say hi"`（mock/cassette 引擎）调 CLI，断言**最终助手文本进 stdout** 且**进程退出码 0**。先跑见红。
- **GREEN**：给 `ask` 加 `-p/--print` bool + `--output-format` enum（默认 `text`）。`-p` 时走非交互渲染：只把最终文本写 stdout（复用 render_stream 的 non-TTY 分支）。
- **验收**：`oh -p "say hi" → stdout 有文本, $?==0`；交互态 `ask`（不带 -p）行为不变（回归测试绿）。
- **质量门**：`mypy --strict` + `ruff check/format`。

### T2 — run 级二档退出码（立场 1）
- **RED**：`test_exit_code_runlevel`：① clean `end_turn` → `$?==0`；② 引擎抛错 / 撞 `--max-turns`（mock 成 LoopLimitExceeded）→ `$?!=0`。先见红。
- **GREEN**：在 `-p` 入口把终止事件/异常映射成退出码——`ConversationCompleteEvent`(end_turn) → 0；`LoopError`/API 异常/turn 上限 → 非0。复用现有 exit-code try/except 链，**只为 -p 路径加 run 级映射**。
- **验收**：两条退出码路径各有测试；**不判 goal 达成**（那是 L3）。
- **checkpoint ①**：到此 `oh -p "..."`（text）能跑、退出码可信——最小可被 bash `if oh -p ...` 调用。

### T3 — `--output-format json`（立场 3 · 单一最终对象）
- **RED**：`test_print_mode_json_shape`：`-p --output-format json` 后 `json.loads(stdout)`，断言含
  `type=="result"`、`result`(文本)、`stop_reason`、`usage`、`num_turns`；`session_id` 按 T0 结论带/不带。先见红。
- **GREEN**：输出层加 json 分支——聚合多 turn 的 `usage`、取末个 `stop_reason`、数 `num_turns`、串 `session_id`。
  **cost_usd 依 T0**：v1 已算→带；只有 token→**带 token 数、cost_usd 置 null 并留 follow-up**（不硬编价目）。
- **验收**：JSON 可解析、字段齐；text 模式不受影响（回归绿）。

### T4 — `--output-format stream-json`（立场 4 · 逐事件 newline JSON）
- **RED**：`test_stream_json_events`：`-p --output-format stream-json`，断言**每行是合法 JSON**、事件类型覆盖
  `assistant_delta / tool_started / tool_completed / error / result`、**末行是 `result`**。先见红。
- **GREEN**：把 engine 事件流逐个映射成 newline JSON（`print(json.dumps(e), flush=True)`），末尾补一个 `result` 终结对象。
- **验收**：N 行各自合法 JSON；末行 result 与 T3 的 json 对象同构。
- **checkpoint ②**：三种 output-format 齐活，L1 的"吐结构化结果"达成。

### T5 — 无头权限：fail-closed + 透传（立场 2 · §7.3 修上游两坑）
- **RED**：`test_headless_permission_failclosed`：① `-p` 跑一个会触发 mutating 工具、**无 allowlist** 的 goal →
  该工具被 **DENY**（不是默默放行），且 JSON/stderr 记下"被拒"；② read-only 工具 → ALLOW 照跑；
  ③ `--permission-mode X` 传入 → 断言它**真到达** checker（修"不透传"坑）。先见红。
- **GREEN**：`-p` 的 permission 回调把 mutating 的 `Decision.ASK` 收成 **DENY**（read-only 维持 ALLOW）；
  把 `permission_mode` 参数**透传**给 `_run_ask`/checker（对照上游 `run_print_mode` 漏传的 bug）。
- **验收**：三条断言全绿；**不引入** allowlist/acceptEdits/sandbox（那是 L2）——L1 只做"read-only 放行 / mutating 拦 / 旋钮透传"。
- **checkpoint ③**：Success 全量达成。

---

## 3. 验收（整模块 done 的判据）

```bash
oh -p "say hi" --output-format json | jq -e '.result and .stop_reason and .usage and .num_turns'
oh -p "say hi"; test $? -eq 0                      # end_turn → 0
# 触发错误/撞上限的 goal → 非0（见 T2 测试）
# mutating-without-allowlist → 该动作被 DENY（见 T5 测试）
uv run pytest -q && uv run mypy --strict src/ && uv run ruff check
```

**全程确定性可测，不走 eval**（L1 是入口/退出码/输出/权限分流，无概率性 prompt 改动）——
对照 `loop-runtime-plan.md §5`：L1/L2 是确定性的。

---

## 4. 依赖图 + 顺序

```
T0(勘探) → T1(-p+text) → T2(退出码) ─┬─ checkpoint①
                                      ├─ T3(json) → T4(stream-json) ─ checkpoint②
                                      └─ T5(权限, 只依赖 T1) ───────── checkpoint③
```
T5 只依赖 T1（与 T3/T4 输出形状正交），可在 T2 后任意时机插入；建议放最后让输出管线先可测。

---

## T0 缝勘探结论（2026-06 · 只读核实完毕）

| 问题 | 结论 | 代码出处 |
|---|---|---|
| **cost_usd 算不算** | **v1 不算钱**——全 src 无任何 pricing/cost 代码，usage 只有 token 数 | grep `cost_usd/pricing/usd` 全空 |
| **usage 形状** | `UsageSnapshot{input_tokens, output_tokens, total_tokens(property)}`；无 cache token（"later phase"） | `protocols/usage.py:10-25` |
| **session_id 有没有** | **无 session_id，但有 `run_id`**——12 字符 hex、**每次 `oh ask` 一个**、`new_run_id()` 铸、`bind_run` 绑为 contextvar；不随事件流出，但在 CLI 入口边界可取 | `observability/context.py:8,36,47` |
| **事件取数** | `ApiMessageCompleteEvent{usage, stop_reason}`；`stop_reason: Literal["end_turn","tool_use","max_tokens","stop_sequence"]`；render_stream 已会留"末个" complete event（多 turn） | `protocols/stream_events.py:42-54`、`_stream_render.py:106-136` |

**据此校准 T2 / T3 的验收（替换原占位）**：

- **T3 cost_usd**【已定】：v1 不算钱 → JSON 里 **`usage:{input_tokens, output_tokens, total_tokens}` 照带，`cost_usd: null`** + follow-up「cost_usd 等定价层（Phase 4 cost-cap）落地再填」。**不硬编价目**。L4 预算栏暂按 token 估。
- **T3 session_id**【已定】：JSON 字段名 **`session_id`**，**值 = v1 的 `run_id`**（CLI 入口取 `bind_run` 绑的 run_id）。字段名对齐 Claude Code headless 输出，方便脚本迁移；值复用 v1 现成的 run 标识、不另造。
- **T3 num_turns**：= `ApiMessageCompleteEvent` 计数；**usage 多 turn 累加** = 逐 complete event 求和。
- **T2 退出码**：`stop_reason=="end_turn"` → `0`；`max_tokens`/`stop_sequence`（终止但未完）+ `LoopLimitExceeded`（撞 `--max-turns`，注意这是 engine 概念，**不是** API 的 `max_tokens`）+ API 异常 → **非0**。与立场 1「二档」一致。

— 2026-06 L1 plan（TDD 脊梁 · 留档不删 · T0 已勘探）
