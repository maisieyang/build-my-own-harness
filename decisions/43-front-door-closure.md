# Decision 43 — 正门收尾(根命令收编 -p / 位置 prompt / 旗面瘦身 / Ctrl+D)

> Created 2026-07-12 · 上游:D42(正门)、`tasks/sprints-2026-07-plan.md` 排队区 D1、
> 2026-07-12 CLI 本质推演(人门/机器门、四栏合同、36 旗地层剖面)

## 一、Why now

D42 把裸 `oh` 变成了会话正门,但收尾没做完:带着问题进门要绕道
`oh ask`(答完即被赶出的旋转门);机器门 `-p` 挂在 `ask` 动词下而非
根命令(Claude Code 形态:`claude -p`);`oh ask --help` 36 个旗子里
一半是常驻配置的地层化石,回忆税重;Ctrl+D 单按裸退,手滑丢会话。

## 二、In / Out

**IN**:根命令位置 prompt(进 REPL 并提交首条消息)· 根命令 `-p`
(headless)· 配置旗退出 help 展示(hidden,兼容保留)· Ctrl+D 双按。

**OUT**:

| 推到哪 | 项 | 原因 |
|---|---|---|
| 不做 | 删除 `ask`/`chat` 动词 | 渐进退役;`-p` 生态(脚本/adapter)在用 |
| 不做 | 物理删除配置旗 | hidden 达成 UX 目标,零破坏(swebench adapter 等脚本不断) |
| 等触发 | `/model` 会话内热切换 | D42 OUT 维持 |

## 三、Decisions

### D43.1 — 根命令收位置 prompt:`oh "x"` = 带首条消息进 REPL

**Chosen**:`_root` callback 加 `prompt` 位置参数;无子命令且有 prompt →
chat 带 `initial_prompt` 启动,首条消息自动提交,回答后停在 REPL。
**Why**:消灭"旋转门"——单发是会话的子集,不是对等物。
**已知限制(接受)**:`oh "memory"` 这类与子命令同名的引号 prompt 会被
Typer 路由到子命令——与 Claude Code 同款取舍,文档说明。
**Reversibility**:easy。

### D43.2 — 根命令收 `-p`:`oh -p "x"` = headless

**Chosen**:根命令加 `-p/--print` + `--output-format` + `-m`(机器门最小
面);其余合同旗仍走 `oh ask -p`(完整面)。`-p` 无 prompt → exit 2。
**Why**:机器门与人门同级,不该藏在动词下;根命令只收最小面,避免把
36 旗问题复制到根。
**Reversibility**:easy。

### D43.3 — 旗面瘦身 = hidden,不是删除

**Chosen**:按"每次调用会变吗"分堆:**合同旗保留可见**(model/max-tokens/
auto/dry-run/-p/output-format/verify*/max-turns/max-iter/goal-condition*/
decompose/isolate/resume*/no-skills/no-commands/sandbox 主开关/log-*);
**配置旗 hidden=True**(sandbox-image/network/memory/cpus/runtime、
enable-plugin-hooks/plugins/memory/web、compact-threshold、no-auto-compact、
tool-result-cap、no-auto-truncate、llm-focus-state)——help 不展示,
解析照常,env 孪生是文档正道。
**Why**:UX 目标是 help 可读,不是断兼容;物理删除要动大量既有测试与
swebench adapter,收益为零。
**Alternatives**:物理删除(破坏面大)/不动(回忆税继续)。
**Reversibility**:easy——hidden 翻回即可。

### D43.4 — Ctrl+D 双按退出

**Chosen**:REPL 里首个 EOF → 提示 "(press Ctrl+D again to exit)" 并
继续;**连续**第二个 EOF → 退出;任何成功输入重置计数。Ctrl+C 行为不变。
**Why**:对齐 Claude Code 的防误触;手滑丢会话的代价 > 多按一次。
**Reversibility**:easy。

## 四、Acceptance

- [ ] `oh "问题"` 进 REPL、首条消息已提交、回答后可继续对话
- [ ] `oh -p "问题" --output-format json` 与 `oh ask "问题" -p --output-format json` 行为一致
- [ ] 裸 `oh` / `oh memory` 等子命令路由回归不变
- [ ] `oh ask --help` 不再展示配置旗;传配置旗仍解析成功(兼容)
- [ ] Ctrl+D 双按语义(单按提示/连按退出/输入重置)
- [ ] 全仓质量门(pytest/mypy --strict/ruff)

## 五、Wiring audit(lean)

| Layer | Verdict | 一句话 |
|---|---|---|
| `cli.py` | extension | root callback + chat initial_prompt + hidden 标记 |
| `repl.py` | unchanged | 输入循环的 EOF 处理在 cli 层 |
| 其余全部 | unchanged | 纯 CLI 表面 |
