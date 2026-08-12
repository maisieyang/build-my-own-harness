---
name: context-probe
description: 验证长对话中上下文来源、工具结果、压缩与恢复是否保持关键事实。只用于 context-management dogfood。
version: 1
---

# Context Probe Skill

处理这个 fixture 时遵循以下规则：

1. 先读取 `context_facts.md`，不要猜测代号、路径或验证命令。
2. 长文件包含 HEAD、MIDDLE、TAIL 三个 anchor；如果 Read 返回的内容被截断，使用
   Grep 精确找回缺失 anchor，不要声称它不存在。
3. 清楚区分“当前 tool result 中没看到”和“文件里不存在”。
4. 回答时标明每个事实来自 Read、Grep、Skill、conversation summary 或 resumed
   snapshot 中的哪一种证据。
5. 不修改仓库生产代码。
