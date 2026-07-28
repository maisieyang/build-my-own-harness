# Decision 48 — REPL session 级 /goal 条件循环（续跑式，完全对齐 CC 行为设计）

> Date: 2026-07-24 · 上游: [docs/ideas/cc-goal-design-reverse.md](../docs/ideas/cc-goal-design-reverse.md)（双源调研）+ [docs/ideas/mode-spectrum-plan-mode-design.md](../docs/ideas/mode-spectrum-plan-mode-design.md) §5.2
> 配套读物: D47（plan-mode，地基）、loop-runtime L3′（判官原语）

## 一、Why now

四点光谱第 3 点（session 内条件循环）是 OH 唯一空位：headless `--goal-condition` + L4 是重喂式，CC /goal 的续跑式（同 session、上下文连续、判官每 turn 判）无对应。D47 恰好打完全部地基（turn 结束挂点、ChatMode 先例、turn 级 prompt 注入）。作者裁决：完全按 CC 行为设计对齐（调研文档 §三）。

## 二、In / Out

**IN（this phase 必做）**:

- `/goal <条件>` 设定 / 裸 `/goal` 状态统计 / `/goal clear`（+5 别名）
- turn 结束判官（复用 `run_semantic_verification`）+ 未达成自动续跑（判官反馈框架）+ 达成清铃与统计
- settings 兜底上限 + 姿态注入 + 状态栏标识
- transcript 哨兵 + `--resume` 恢复活跃 goal

**OUT（推迟 / 不做）**:

| 推到哪 | 项 | 一句话原因 |
|---|---|---|
| v2 | goal-as-hook 分层重构（CC：Stop 事件 prompt-hook 语法糖） | 行为不可见的内部分层，需先给 OH hooks 加 Stop 事件，面大 |
| v2 | 三值判决（impossible 跳出） | 动被 verify_judge 校准的判官 schema |
| v2 | 判官小模型硬默认 | provider 现实（qwen-plus 级弱模型不可靠）；字段先留 |
| v2 | 哨兵消息不进模型上下文 | OH 无 attachment 消息类型，过滤需动 engine/messages |
| 不做 | 模型可调的 goal 类工具 | 裁判分离 anti-scope，同 D47.2 |

## 三、Decisions

### D48.1 — goal 是 session 变量，与 ChatMode 正交

**Chosen**: `GoalState = {condition, iterations, set_at, tokens_at_start, last_reason}`，REPL 内存态；判官只在 DEFAULT 地面态 turn 结束触发（plan 态归审批菜单）。

**Why**: CC 同款正交（plan 批准 → 执行 → goal 跑到绿可组合）；触发互斥防挂点抢跑。

**Alternatives**: 新增 ChatMode.GOAL（被否：goal 不改变权限姿态，非模式）。

**Reversibility**: `easy`。

### D48.2 — 续跑 = 判官反馈框架直接入 history，立即续 turn

**Chosen**: 未达成 → `[goal checker] not met: <reason>` 框架消息 append 进 history（user 角色但明确框定判官身份）+ 立即发起下一 turn；UI 显示 `(goal not met — continuing: <reason>)`，**不走 pending_input 回显路径**（不冒充用户输入）。

**Why**: 对齐 CC "Stop hook feedback" 语义（调研 §一）；API 只有 user/assistant 角色，CC 底层同样是带标记的 user 消息——对齐的是身份框定与 UI。

**Alternatives**: pending_input canned 消息（plan v1，被调研证伪：冒充用户口吻 + 回显误导）。

**Reversibility**: `easy`。

### D48.3 — 判官输入保留渲染文本（作者裁决，与 CC 有意分歧）

**Chosen**: 新渲染器 `render_history_transcript(messages)`（格式对齐 `collect_transcript`：`[tool call]`/`[tool result]` + 截断 + turn 边界标记），喂给现有 `run_semantic_verification`。

**Why**: 行为层与 CC 消息数组等价（判官只见对话内容）；保住防注入定界符 + verify_judge 校准资产——不为形似弃校准。

**Alternatives**: 照搬消息数组（M 工作量 + 判官换输入格式即换未校准判官）。

**Reversibility**: `medium`——换格式需重跑 verify_judge。

### D48.4 — 判官 model/timeout：主模型默认 + 可配降档

**Chosen**: settings 字段 `goal_judge_model: str | None`（None=主模型；focus_state_model 惯例）；timeout 60s（对齐 headless 默认）。

**Why**: CC 硬默认小模型，但 OH 的 provider 小模型不可靠（qwen 实测）；差异声明为 provider 约束非设计分歧。

**Reversibility**: `easy`。

### D48.5 — 上限 = settings 兜底（默认 25）+ bound-in-condition 最佳实践

**Chosen**: `goal_max_auto_turns` settings 字段（env `OPENHARNESS_GOAL_MAX_AUTO_TURNS`），默认 25；`/goal` 设定时 echo 提示"建议在条件里写界限（如 or stop after 20 turns）"；达上限停自动续 + 响铃提示，人工输入重置计数。

**Why**: 对齐 CC"界限交给条件语言 + env 兜底"，但 OH 保留文档化兜底（fail-closed 品味，取中间值）。

**Alternatives**: 硬常量 10（plan v1）/ 无上限（CC 文档层）——取中间。

**Reversibility**: `easy`。

### D48.6 — 达成动作与统计

**Chosen**: 达成 → echo 判官 reason + `(goal met after N auto-turns, ~M tokens, T elapsed)` + `\a` 响铃 + 条件自动清除；tokens 为 `estimate_message_tokens` 基线差值近似。

**Why**: CC 同款（达成自动清除 + duration/turns/tokens 统计）；近似值声明为估算。

**Reversibility**: `easy`。

### D48.7 — transcript 哨兵 + resume 重建（不另设持久层）

**Chosen**: 设定/达成/清除各 append 一条带标记哨兵消息进 history（snapshot 自然持久化）；`--resume` 扫描历史，最后一条"设定"晚于任何"达成/清除"→ 恢复条件（计数/计时/token 基线重置，CC 同款）。哨兵进模型上下文（声明简化，视作显式状态声明）。

**Why**: CC 的 restoreGoalFromTranscript 同构——对话流是唯一事实源，崩溃安全，零状态同步。

**Reversibility**: `medium`——哨兵格式一旦入 snapshot 需向后兼容。

### D48.9 — set 即开工（dogfood 修正，2026-07-24 当日）

**Chosen**: `/goal <条件>` 设定即注入 kickoff 指令（`[goal set] ...treat the condition itself as your directive... immediately start working`）并立即发起 turn，零后续用户输入。

**Why**: 首次 dogfood 即暴露（作者 `/goal todo-mvp/GOAL.md` 后 REPL 死等输入）：CC 的 set 是点火动作——逆向已提取到该指令原文，设计时遗漏。等待输入的 goal 是死条件。

**Alternatives**: 纯状态设定等用户开口（v1 初版，dogfood 证伪）。

**Reversibility**: `easy`。

### D48.8 — 姿态注入与 fail-closed

**Chosen**: goal 激活期间 turn 级 system prompt 追加 `GOAL_PROMPT_SECTION`（含条件；复用 D47 turn_system_prompt 机制，不持久）。判官 fail-closed 不区分"判负"与"判官坏了"（v1 一律续跑，上限 + 可见 feedback 兜底）。

**Why**: 条件文本是行为引力场（§5.3）；错误类型区分等触发。

**Reversibility**: `easy`。

## 四、Acceptance（phase 级，跨 task）

- [ ] regression: 全仓 `uv run pytest -q` 绿 + `uv run mypy --strict src/` + `uv run ruff check && uv run ruff format --check`
- [ ] dogfood: `oh chat` 亲手跑 `/goal <可达成条件>` → 续跑 → 响铃+统计 → 自动清除；再跑不可达条件 → 上限暂停；`--resume` 恢复活跃 goal
- [ ] 文档同步：CHANGELOG 一行；learnings/retro 留待 debrief
- [ ] §六 wiring audit verdict 实测对照回填

## 五、Tasks

### T1 — `render_history_transcript()` 渲染器

**Description**: `_stream_render.py` 新增 message 列表版 transcript 渲染器，格式对齐 `collect_transcript`（`[tool call: name(input)]` / `[tool result (ok|error): 截断 output]`）+ turn 边界标记（供判官数 turn，D48.5）。

**Acceptance**:
- [ ] 覆盖 TextBlock/ToolUseBlock/ToolResultBlock 渲染 + 截断 + 空历史 + turn 边界标记
- [ ] 先 RED 后 GREEN；mypy strict

### T2 — repl.py goal 原语（纯函数）

**Description**: `parse_goal_command`（set/show/clear+5 别名）、`GoalState` dataclass、`GOAL_PROMPT_SECTION` 模板、`build_goal_continuation`（D48.2 框架）、哨兵消息构造/识别函数、`format_status_bar` goal 标识、`/goal` 进 `BUILTIN_SLASH_COMMANDS`。

**Acceptance**:
- [ ] 解析三态 + 别名全覆盖；哨兵 roundtrip（构造→识别→恢复判定）
- [ ] 模板含条件文本；状态栏显隐；与 plan-mode 原语零耦合

### T3 — settings 字段

**Description**: `goal_judge_model: str | None = None` + `goal_max_auto_turns: int = 25`（含 env 说明，focus_state_model 惯例）。

**Acceptance**:
- [ ] env 覆盖生效测试；默认值断言

### T4 — cli.py 接线

**Description**: `/goal` 命令处理（set 带 bound 提示 / show 状态统计 / clear）；姿态注入；turn 结束判官（DEFAULT 态）；续跑（D48.2 直接 append + 立即续 turn）；达成（D48.6）；上限 + 人工输入重置；`--resume` 哨兵扫描恢复；`/help` 同步。判官以 cli 命名空间引入供 monkeypatch。

**Acceptance**:
- [ ] 设定后 turn 结束判官被调、transcript 含工具行为与 turn 标记
- [ ] fail → 自动续 turn（无人工输入、无 `>>> ` 回显）、反馈框架含 reason；pass → 清除 + 统计 echo、无续 turn
- [ ] 上限暂停提示可见；人工输入重置计数；plan 态 turn 不触发
- [ ] resume：活跃 goal 恢复（计数重置）；已达成/已清除不恢复
- [ ] `/goal clear` 别名、裸 `/goal`、误用不崩

### T5 — 全量门 + CHANGELOG

**Description**: 三道门 + CHANGELOG 一行 + 无模型可调 goal 类工具确认。

**Acceptance**:
- [ ] 全仓 pytest / mypy strict / ruff 两检绿；CHANGELOG 已加

## 六、Wiring audit

| Layer | Verdict | Reasoning（一句话） |
|---|---|---|
| `permissions/` | unchanged | goal 不动权限（D48.1 正交） |
| `hooks/` | unchanged | v1 不做 hook 分层（OUT #9） |
| `_stream_render.py` | requires extension | 新渲染器（T1） |
| `repl.py` | requires extension | goal 原语层（T2） |
| `cli.py` | requires extension | 接线 + resume 扫描（T4） |
| `config/settings.py` | requires extension | 两个字段（T3） |
| `verification/semantic_gate` | unchanged | 判官原语原样复用（D48.3） |
| `services/snapshot\|session_memory\|compact` | unchanged | 哨兵走普通消息，snapshot 无感知 |
| `engine/` | unchanged | 续跑是 REPL 层再调 run_query |
| `prompts/` | requires extension | GOAL_PROMPT_SECTION（若放 prompts 层；放 repl 则 unchanged） |
| `eval/` | unchanged | 判官输入格式不变，校准不失效（D48.3） |

**Conclusion**: 4-5 extension + 其余 unchanged + 0 bypass——比 D47 大一档但仍 phase 形态，无需重 ratify。

## 七、References

- [docs/ideas/cc-goal-design-reverse.md](../docs/ideas/cc-goal-design-reverse.md)
- [47-plan-mode-boundary.md](./47-plan-mode-boundary.md)（地基）
- `verification/semantic_gate.py`（L3′ 判官）· https://code.claude.com/docs/en/goal.md
