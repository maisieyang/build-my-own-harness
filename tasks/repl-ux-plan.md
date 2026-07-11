# REPL UX — dogfood 门槛拆除(正门 / 识别层 / 状态行)

- **Date**: 2026-07-11
- **Status**: Done — 判决 + trade-off 见 decisions/42-repl-ux.md
- **Origin**: dogfood 复盘对话——"为什么我不吃自己的 dogfood"。诊断:交互的
  回忆税是主动测试的门槛。三个修复全部对齐"四层语法"框架(意图=NL /
  策略=环境化配置 / 元操作=`/` 识别 / 自动化=headless flags):
  现状把正门装在了第 4 层,第 3 层有管线无 affordance。

## Scope(用户拍板,2026-07-11)

1. **正门**:裸 `oh` 直接进 chat REPL(对齐 `claude` 的心智:一个词进入
   会话)。`oh --help` / 十二个子命令 / `--version` 全部不变。
2. **`/` 识别层**:输入库换 prompt_toolkit。打 `/` 即弹补全菜单,候选 =
   内置命令 + CommandStore + SkillStore(与 D38.1 dispatch 顺序一致),
   带描述列。持久化输入历史(跨会话上下键)。
3. **状态行**:等待输入时 bottom toolbar 显示 模型 + context 用量/窗口
   (百分比)+ auto-compact 阈值。数据源 = `estimate_message_tokens` +
   `get_context_window`,纯展示,无新计算路径。

**Explicitly out of scope**(本切片不做,将来按需):`/model` 会话内热切换、
`/status` 汇总面板、`oh ask` 侧任何改动、headless 路径任何改动。

## Non-negotiables

- **非 TTY 路径零变化**:stdin/stdout 任一非 TTY → 回落原 `input(">>> ")`
  路径。所有既有 chat 集成测试(CliRunner 管道喂入)必须原样通过;CI /
  管道 / heredoc 用法不受 prompt_toolkit 影响。
- gnureadline (darwin) 由 prompt_toolkit 取代后退役——它当年修的中英混排
  问题在 prompt_toolkit 下不存在(自带行编辑,不经 readline)。
- 质量门照旧:TDD 先 RED、mypy --strict、coverage ≥95%、ruff。

## 模块拆分

- `src/openharness/repl.py`(新):`SlashCommand` / `collect_slash_commands`
  (merge + dedup,内置优先)/ `SlashCompleter`(仅行首 `/` 且无空格时
  激活)/ `format_status_bar`(纯函数)/ `default_history_path`
  (`~/.openharness/chat-history/<basename>-<sha1[:12]>.txt`,复用
  snapshot/session-memory 的目录约定)/ `create_prompt_session`。
- `cli.py`:`_root` callback 接管裸调用 → chat 默认参数;`_run_chat` 循环
  的 input 点按 TTY 分流。
- 测试:`tests/repl/`(completer / collect / status bar / history path)+
  `tests/cli/` 入口测试(裸 oh → chat;--version、--help、子命令不回归)
  + interactive 分支的 monkeypatch 集成测试。

## 留痕

完成后:decisions/42-repl-ux.md(判决 + trade-off)、CHANGELOG 条目、
本文件 Status → Done。
