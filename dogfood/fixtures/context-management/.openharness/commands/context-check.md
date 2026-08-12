---
name: context-check
description: 使用 context probe skill 检查三个 anchor，并说明每个事实的证据来源。
---

先加载 `context-probe` skill。检查 `large_context.txt` 的 HEAD、MIDDLE、TAIL 三个
anchor；如果一次 Read 没有给出中段内容，使用精确 Grep 恢复。只报告结果和证据来源，
不要修改文件。
