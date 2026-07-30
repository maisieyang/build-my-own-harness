# OpenHarness 简历项目段落

> 使用前按目标岗位裁剪。不要同时堆入所有版本。

## 推荐标题

**OpenHarness — 从零实现的 Agent Runtime & Evaluation Harness**

Python · Pydantic v2 · Typer · asyncio · Docker/gVisor · MCP · pytest

项目链接：`https://github.com/maisieyang/build-my-own-harness`

## 中文标准版

- 主导从零构建 Python coding-agent control plane：实现 OpenAI-compatible
  streaming tool loop、类型化协议、allow/ask/deny 权限、不可逆操作红线、hooks，
  以及可选 Docker/gVisor 隔离；明确拆分 authorization 与 containment 边界。
- 设计三种有不同 context semantics 的自治循环：交互式 `/goal` 每轮交由独立、
  tool-disabled LLM judge 判定，headless repair loop 使用 command/semantic gate
  与 fresh-context retry，autopilot 通过持久化优先级队列调度有界任务；支持
  compaction、snapshot/resume、worktree isolation 与 append-only run journal。
- 用公开 `oh` CLI 完整运行 SWE-bench Lite 300 题；在官方 hosted evaluator 故障后
  自建官方 harness，qwen3.7-max（thinking off）取得 **170/300 resolved（56.7%）**；
  战役反向发现并修复 config drift、stream retry、turn cap 与 provider 参数透传等
  5 个 harness 缺口。
- 建立 capability-level eval 与 dogfood 反馈闭环；当前 **2,783 tests、
  95.29% coverage**，全量 `mypy --strict`、Ruff 与 Python 3.10/3.11 CI，
  并以 decision/plan/retro 三条 append-only trail 保存设计取舍和失败归因。

## 中文精简版

- 从零构建 Python coding-agent harness，覆盖 typed streaming tool loop、权限与
  Docker/gVisor 隔离、compaction/snapshot/resume、skills/MCP，以及由外部
  command/LLM oracle 驱动的有界 repair loops；2,783 tests、95.29% coverage、
  mypy strict。
- 通过 shipped CLI 跑完 SWE-bench Lite 300 题并自建官方 evaluator，
  qwen3.7-max（thinking off）取得 170/300 resolved（56.7%）；将战役暴露的
  5 个 runtime/config/retry 缺口修回 production path。

## English Version

**OpenHarness — From-scratch Agent Runtime and Evaluation Harness**

Python · Pydantic v2 · Typer · asyncio · Docker/gVisor · MCP · pytest

- Built a Python coding-agent control plane from scratch, including an
  OpenAI-compatible typed streaming tool loop, allow/ask/deny authorization,
  irreversible-operation red lines, lifecycle hooks, and optional Docker/gVisor
  containment.
- Designed three bounded execution loops with distinct context semantics:
  session-continuing `/goal` with a separate tool-disabled LLM judge,
  fresh-context headless repair with command or semantic gates, and a persistent
  priority-scored autopilot queue; added compaction, snapshots/resume, worktree
  isolation, and append-only run journals.
- Ran all 300 SWE-bench Lite instances through the shipped CLI and self-hosted
  the official evaluator after the hosted service failed, reaching
  **170/300 resolved (56.7%)** with qwen3.7-max, thinking disabled; converted
  five benchmark-discovered runtime/config/retry gaps into production fixes.
- Maintained **2,783 tests and 95.29% coverage**, with Ruff, `mypy --strict`,
  and Python 3.10/3.11 CI; preserved design boundaries, plans, dogfood findings,
  and evaluation artifacts as append-only engineering records.

## 个人简介中的一句

偏 agent infrastructure / harness 的 AI engineer：关注强模型周围的控制面，
包括 tool execution、permission/sandbox、long-context state、external
completion gates 与 eval，而不只做 prompt 或 workflow orchestration。

English:

> AI engineer focused on agent infrastructure: tool execution,
> authorization/containment, long-context state, external completion gates,
> and capability-level evaluation around strong models.

## 针对岗位裁剪

### Agent runtime / harness engineer

保留标准版前 3 条。关键词：

`agent runtime`、`tool loop`、`permission model`、`sandbox`、`state/resume`、
`verification gate`、`eval`。

### AI infrastructure / platform engineer

强化：

- typed protocol 与 observability；
- fail-closed headless policy；
- worktree/isolation/journal/resume；
- benchmark 长跑中的 config/provider drift；
- CI、strict typing 与 regression discipline。

弱化 REPL UX 细节。

### Applied AI / agent engineer

强化：

- `/plan -> approve -> /goal` 使用闭环；
- independent judge 与 repair feedback；
- skills/MCP/plugin extension；
- SWE-bench 结果与 dogfood finding。

仍需保留“external oracle 优先于 model self-report”这条差异化判断。

## 不建议写入简历

- 31k source LOC、48k test LOC：容易变成 vanity metric。
- 415 commits：可在面试中说明投入周期，不适合作为成果。
- “Production-grade”：除非附上具体质量门禁和真实运行证据。
- “Provider-agnostic”：当前准确范围是 OpenAI-compatible endpoints。
- “Claude Code compatible”：当前只是 partial plugin compatibility。
- “All SWE-bench failures were model failures”：必须解释分类分母，不适合一行简历。
