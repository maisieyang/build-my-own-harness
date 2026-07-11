# Decision 42 — REPL UX(正门 / 识别层 / 状态行)

> Date: 2026-07-11 · 上游: [tasks/repl-ux-plan.md](../tasks/repl-ux-plan.md)
> 起点:dogfood 复盘——"为什么我不吃自己的 dogfood"。诊断框架(四层语法:
> 意图=NL / 策略=环境化配置 / 元操作=`/` 识别 / 自动化=headless flags)
> 记录在 plan 文件里。

## 一、Why now

自 Phase 5b/18 起 slash 命令系统管线完整(built-ins → CommandStore →
SkillStore,D38.1),但发现机制是回忆门控的(`/help`),且正门是帮助页
而非会话。回忆税是作者本人主动测试的门槛——门槛不除,dogfood 流水
(Phase 14.5 式的真实使用发现)就断流。

## 二、In / Out

**IN**:裸 `oh` 进 REPL;`/` 弹补全菜单;bottom toolbar 状态行;
持久输入历史;gnureadline 退役。

**OUT**:

| 推到哪 | 项 | 一句话原因 |
|---|---|---|
| 将来按需 | `/model` 会话内热切换 | 需要 QueryContext 热重建,超出本切片 |
| 将来按需 | `/status` 汇总面板 | 状态行已覆盖高频信息 |
| 不做 | `oh ask` / headless 任何改动 | 机器接口,回忆税不适用 |

## 三、Decisions

### D42.1 — 裸 `oh` = argless `oh chat`

**Chosen**:`no_args_is_help=False` + callback `invoke_without_command`,
裸调用显式传 chat 全部默认参数。
**Why**:一个词进入会话是心智分水岭;帮助页仍在 `oh --help`。
**Alternatives**:保持帮助页(回忆税不除)/ 新增 `oh repl` 别名(再加一个要记的词)。
**Reversibility**:easy——改回一行 Typer 参数。

### D42.2 — 输入层 = prompt_toolkit,TTY 门控

**Chosen**:新模块 `repl.py`;`is_interactive()`(stdin+stdout 双 TTY)
为真才建 PromptSession,否则走原 `input(">>> ")`。
**Why**:识别层需要打 `/` 即弹菜单(`complete_while_typing`),readline
只有 Tab 补全,无菜单形态。
**Alternatives**:readline Tab 补全(识别体验打折)/ rich Prompt(无补全)。
**Reversibility**:easy——非 TTY 路径 byte-for-byte 未动,删除交互分支即回滚。

### D42.3 — 菜单候选 = D38.1 dispatch 顺序合并

**Chosen**:built-ins → CommandStore → SkillStore,同名高层胜出。
**Why**:菜单展示的描述必须是 dispatch 实际会执行的那个条目。
**Reversibility**:easy。

### D42.4 — 状态行 = 纯格式化,零新测量路径

**Chosen**:`format_status_bar` 消费 `estimate_message_tokens` +
`get_context_window`;auto-compact 关闭时阈值段整体省略。
**Why**:上下文用量数据压缩子系统本就实时持有,不可见纯属展示缺口;
新增测量路径会引入第二个真相源。
**Reversibility**:easy。

### D42.5 — 输入历史:per-project cwd-hash 文件

**Chosen**:`~/.openharness/chat-history/<basename>-<sha1[:12]>.txt`,
与 snapshots / session-memory 同形。
**Why**:项目间上下键历史不串;目录约定复用已有心智。
**Reversibility**:easy——删文件无副作用。

### D42.6 — gnureadline 退役

**Chosen**:删除 darwin-only dep + cli.py 的 readline side-effect import。
**Why**:TTY 路径由 prompt_toolkit 自带行编辑接管(CJK 宽度正确),
Phase 14.5 修的 libedit bug 不再有触发面;非 TTY 的 `input()` 从不经
readline。
**Reversibility**:easy——dep + import 两处恢复即回。

## 四、Acceptance

- [x] regression:全仓 `uv run pytest -q` 绿(2593 passed,coverage 95.15%)
- [x] 非 TTY 契约:全部既有 chat 测试(CliRunner 管道)未改一行断言通过
- [x] `mypy --strict` + ruff 全绿
- [x] 文档同步:CHANGELOG + 本文件 + plan Status → Done

## 五、Wiring audit

| Layer | Verdict | Reasoning |
|---|---|---|
| `cli.py` | extension | 正门 callback + 循环输入分流;headless 路径 unchanged |
| `repl.py`(新) | extension | 全部可无 TTY 单测;PromptSession 仅 cli 触碰 |
| `commands/` + `skills/` | unchanged | 只读 `discover()`,structural Protocol 消费 |
| `services/compact` | unchanged | 只读既有函数 |
| `engine/` / `permissions/` / `hooks/` | unchanged | 未触碰 |

**Conclusion**:2 extension + 其余 unchanged,cleanup-sized slice。

## 六、References

- [tasks/repl-ux-plan.md](../tasks/repl-ux-plan.md)(scope 拍板记录)
- D38.1(slash dispatch 顺序,菜单候选与之对齐)
- Phase 14.5(gnureadline 引入语境,本次退役)
