# Decision 47 — plan-mode v1（REPL 双态：默认 / plan）

> 2026-08-05 status: `/plan` remains current. References below to the parallel
> headless goal/autopilot line are historical; D49 retired that product line.

> Date: 2026-07-24 · 上游: [docs/ideas/mode-spectrum-plan-mode-design.md](../docs/ideas/mode-spectrum-plan-mode-design.md)
> 配套读物:
> - 四点光谱与 CC v2.1.200+ 调研: 上游文档 §一/§二
> - D42/D43（repl-ux / 正门收口）、D44（交互 prompt 先例）

## 一、Why now

四点光谱定稿后，OH 光谱缺第 2 点（plan → 批准执行）——两端（REPL 理解模式、
goal/autopilot 执行模式）均已建成。依赖全部就位（permissions 模块 5、repl-ux、
斜杠命令、D44 交互 prompt），当下可建；CC v2.1.200+ 调研给出对标形状（菜单硬闸）。

## 二、In / Out

**IN（this phase 必做）**:

- `plan_mode_preset()` deny 预设（Edit/Write/Bash）
- REPL mode 状态机（default | plan）+ `/plan` 命令 + 状态栏标识
- plan 态 turn 结束审批菜单（三选项）+ 批准回 DEFAULT + sentinel 注入历史（不自动执行）
- planning prompt 姿态注入

**OUT（推迟 / 不做）**:

| 推到哪 | 项 | 一句话原因 |
|---|---|---|
| v2 | 只读 Bash 分类放行（CC `useAutoModeDuringPlan`） | CC 花大力气的地方，v1 整拒 Bash |
| v2 | 识别"计划已呈交"再弹菜单 | 需结构化信号；v1 每 turn 弹 +「继续规划」兜住 |
| v2 | 计划外置编辑（CC Ctrl+G） | 好细节非骨架 |
| 不做 | headless/`-p` 的 plan 姿态 | plan 是交互侧审批闸，无头侧归 L2 policy |
| 不做 | 模式状态持久化到 snapshot | 见 D47.7 |
| 不做 | 模型侧退出类工具 | 见 D47.2，永久排除 |

## 三、Decisions

### D47.1 — plan 姿态收编为规则预设，非新 PermissionMode 枚举值

**Chosen**: `plan_mode_preset() -> ("Edit(*)", "Write(*)", "Bash(*)")` deny 预设。

**Why**: `accept_edits_preset()`"收编为规则"立场的第二次应用；deny 在规则引擎优先级最前，机制现成。

**Alternatives**: 新枚举值（污染模式语义）/ prompt 约束（模型自律不可信），选规则预设。

**Reversibility**: `easy`——纯增量函数。

### D47.2 — 审批 = harness 渲染菜单，模型无退出工具

**Chosen**: plan 态每个 assistant turn 结束渲染三选项菜单：[1] 批准回 DEFAULT / [2] 继续规划 / [3] 放弃回默认。

**Why**: follow CC（ExitPlanMode 已移除，审批是 harness 直接接管的 UI 硬闸）；模型连提议退出的工具资格都没有。原 v1 设计中更复杂的批准落地模式在实践中简化为三选项——额外的权限提升分支与 D48 goal 循环职责重叠，移除后用户通过 `/goal` 显式启动执行更安全可控。

**Alternatives**: `/execute` 用户命令（对话中被否：与 CC 对标形状不符）/ ExitPlanMode 类工具（CC 已弃），选菜单。

**Reversibility**: `easy`。

**Anti-scope**: 明确**不**注册任何模型可调的退出/审批工具——审批权永远在 harness/人。

### D47.3 — 批准 = 回 DEFAULT + sentinel 注入，不自动执行

**Chosen**: [1] 回 DEFAULT，注入 `[plan-status] approved:` sentinel 到历史；不自动发起执行 turn，等用户下一条消息决定下一步（refine / `/goal` / 其他）。

**Why**: 批准 ≠ 执行。用户需要审批后的中断点来将计划转化为显式 `/goal <target + verification>` 再启动 D48 循环。自动执行跳过这一审查环节，与 goal mode 的设计意图冲突。

**Alternatives**: 批准即自动发起执行 turn（原 v1 设计，实践中发现与 D48 职责重叠且跳过用户审查），选不自动执行。

**Reversibility**: `easy`。

### D47.4 — （已合并入 D47.3）

原 v1 设计的一键执行+回落机制已随三选项简化一并移除。approve 仅回 DEFAULT 地面，无临时权限提升。

### D47.5 — v1 每 turn 结束弹菜单（声明简化）

**Chosen**: 不识别"计划是否已呈交"，plan 态每个 assistant turn 结束都弹菜单。

**Why**: 简单可预测；模型还在追问/探索时人选 [2] 继续规划即兜住。

**Alternatives**: 结构化信号识别计划完成（v2）/ 模型自报完成（自评不可信），选每 turn 弹。

**Reversibility**: `easy`——v2 收紧弹出时机不破坏语义。

### D47.6 — planning prompt 是姿态非契约

**Chosen**: mode=plan 时注入轻 planning 指令（研究、收敛一屏计划、文本呈交）；测试只测"注入了"，不测模型服从。

**Why**: 契约只在 deny 预设；模型服从度属概率行为，归 eval 域（draft，不设门）。

**Reversibility**: `easy`。

### D47.7 — 模式状态不持久化

**Chosen**: mode 为 REPL 内存态；session 断 → 回默认地面。

**Why**: 计划文本本在消息历史里无损失；地面是安全默认。

**Alternatives**: 写进 snapshot（多一处状态同步，v1 无收益），选不持久。

**Reversibility**: `medium`——将来接 snapshot 需补"恢复时 mode 语义"设计。

## 四、Acceptance（phase 级，跨 task）

- [x] regression: 全仓 `uv run pytest -q` 绿（2717 passed）+ `uv run mypy --strict src/` + `uv run ruff check && uv run ruff format --check`（2026-07-24）
- [x] dogfood: `oh chat` 亲手跑一遍 /plan → 只读探索（Edit 被拒可见）→ 菜单批准 → 回落 DEFAULT 且不偷跑执行（作者亲手跑过，2026-07-24；2026-07-29 行为收窄）
- [ ] 文档同步：CHANGELOG 一行（已加）；learnings/retro 留待 debrief
- [ ] §六 wiring audit verdict 实测对照回填

## 五、Tasks

### T1 — `plan_mode_preset()` 权限预设

**Description**: `permissions/rules.py` 新增 deny 预设，`accept_edits_preset()` 镜像，同款 docstring 风格。

**Acceptance**:
- [ ] 预设生效时 Edit/Write/Bash 一律 deny（含 deny 优先于既有 allow 规则的测试）
- [ ] Read/Grep 等只读工具不受影响
- [ ] mypy strict + 测试先 RED 后 GREEN

### T2 — REPL mode 状态机 + `/plan` + 状态栏

**Description**: chat 会话持有 `mode ∈ {default, plan}`；`/plan` 进入并叠加 T1 预设；`_CHAT_COMMANDS` 与 /help 同步；`format_status_bar` 亮 plan。

**Acceptance**:
- [ ] `/plan` 进入 plan 态，状态栏含 plan 标识
- [ ] plan 态下工具调用实际走 deny（接线测试，非只测预设函数）
- [ ] 重复 `/plan` no-op 提示，不崩

### T3 — 审批菜单 + 状态转移

**Description**: plan 态 assistant turn 结束渲染三选项菜单（复用 D44 交互 prompt 机制）；选项分发按 D47.2-D47.5。

**Acceptance**:
- [ ] 三选项渲染且各自转移正确：[1] 撤 deny、回 DEFAULT、注入 approved sentinel 但不启动执行；[2] 留 plan；[3] 回默认
- [ ] 批准后的下一 turn 必须来自用户新输入，不由 harness 合成
- [ ] 非 TTY 环境 plan 态行为 fail-closed（不挂死等菜单）

### T4 — planning prompt 姿态注入

**Description**: mode=plan 时 prompt 组装处注入轻 planning 指令。

**Acceptance**:
- [ ] mode=plan 时注入存在、mode=default 时不存在（组装层测试）
- [ ] 注入内容不进 snapshot/记忆等持久层

## 六、§六 Wiring audit

| Layer | Verdict | Reasoning（一句话） |
|---|---|---|
| `permissions/` | requires extension | 新增 `plan_mode_preset()`，引擎不动 |
| `hooks/` | unchanged | 不新增事件 |
| `services/snapshot\|session_memory\|compact` | unchanged | D47.7 不持久化 |
| `engine/slash_skill` | unchanged | `/plan` 是 REPL 内建命令（如 /compact），不走 skill 展开 |
| `skills/store + model` | unchanged | — |
| `commands/` | unchanged | 非 markdown 命令 |
| `bundles/` | unchanged | — |
| `cli.py` + `repl.py` | requires extension | mode 状态机 + 菜单 + 状态栏，唯一真正的新面 |
| `prompts/` | requires extension | plan 姿态注入 |
| `observability` | unchanged | 至多 mode 转移 log 一行 |
| `eval/` | unchanged | 模型服从度归 eval draft，本 phase 不建面 |

**Conclusion**: 3 extension + 8 unchanged + 0 bypass——符合小 phase 形态，无需重 ratify。

## 七、References

- [docs/ideas/mode-spectrum-plan-mode-design.md](../docs/ideas/mode-spectrum-plan-mode-design.md)（光谱 + CC 调研 + 定位插点）
- https://code.claude.com/docs/en/permission-modes.md（CC 审批菜单/落地模式）
- [44-interactive-bash-ask.md](./44-interactive-bash-ask.md)（交互 prompt 先例）
- [42-repl-ux.md](./42-repl-ux.md) / [43-front-door-closure.md](./43-front-door-closure.md)
