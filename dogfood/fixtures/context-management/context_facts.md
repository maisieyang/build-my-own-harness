# Context Probe Facts

- Session 代号：`CONTEXT-LIFECYCLE-0812`
- 工作目录：以启动 REPL 时的当前工作目录（`pwd`）为准；编号 fixture 的目录名会随
  每次 dogfood run 改变，不从本文推断路径。
- 精确验证命令：`uv run pytest test_context_probe.py -q --no-cov`
- 不允许的动作：修改仓库生产代码
- 长文件头部事实：`context-head-0812`
- 长文件中段事实：`context-middle-0812`
- 长文件尾部事实：`context-tail-0812`

这些值故意放在稳定文件里，供 Read、Grep、tool-result 截断、`/compact` 与
`oh --resume` 的组合实验使用。
