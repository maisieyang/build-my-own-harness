# REPL Controller 深入链路

状态：DPG-004 与 DPG-005 已完成双线验证。用户于 2026-08-11 手动运行并报告符合
预期；PTY runner 于 2026-08-12 使用真实模型和工具完成 live 运行。证据保存在
`.dogfood/artifacts/20260812-depth-01/`。

基础链路通过后，这组实验保持编码任务简单，只增加状态机深度。同一个 session 依次
验证 Plan 的 `keep planning`、`discard`，以及 Goal 在首次证据不足时的自动续跑。

## 共享环境

```text
.dogfood/work/repl-controller-depth/
├── pricing.py
└── test_pricing.py
```

精确验证命令：

```bash
uv run pytest .dogfood/work/repl-controller-depth/test_pricing.py -q --no-cov
```

预期初始结果：`1 failed, 1 passed`。

启动命令：

```bash
uv run oh --auto --sandbox
```

## DPG-004：Plan 继续规划后丢弃

进入 `/plan`，然后输入：

```text
检查 .dogfood/work/repl-controller-depth/pricing.py 中的折扣计算问题，给出修复与验证计划，只规划，不修改代码。
```

第一次出现 Plan menu 时输入 `2`，然后输入：

```text
继续规划：补充输入边界、精确验证命令，以及验证失败时如何定位；仍然不要执行。
```

第二次出现 Plan menu 时输入 `3`。

### 通过标准

- 两轮都保持 Plan mode，只使用只读能力。
- 第二轮在第一轮基础上补充边界、验证命令和失败定位方法。
- 两轮之间及丢弃后 fixture hash 始终不变。
- 输入 `2` 后继续 Plan；输入 `3` 后显示
  `(plan discarded — back to default mode)`。
- 丢弃后回到 `>>>`，不会自动执行计划。
- 外部验证仍为 `1 failed, 1 passed`。

## DPG-005：Goal 在证据不足时自动续跑

保持同一个 session，在 Default 下输入：

```text
/goal 严格分阶段完成：第一个 assistant turn 只读取 .dogfood/work/repl-controller-depth/pricing.py 和 test_pricing.py，解释当前失败原因，不修改文件、不运行 Bash，然后结束这一轮；后续 assistant turn 才修复百分比计算，只接受 0 到 100，非法值抛出 ValueError，补充 0%、100% 和非法百分比测试，并成功运行 uv run pytest .dogfood/work/repl-controller-depth/test_pricing.py -q --no-cov。只修改这个 dogfood 目录。
```

Goal 报告完成后输入 `/goal` 查看状态，再输入 `/exit`。

### 通过标准

- 设置 Goal 后立即开始第一阶段，不需要额外发送“开始”。
- 第一个 assistant turn 只调查并解释，fixture hash 不变。
- Judge 不把只有分析、没有修改与测试证据的状态判为完成。
- 至少出现一次 `goal not met — continuing`。
- 自动续跑后才出现修改与 Bash 验证。
- 最终显示 `goal met after ...`，且 auto-turn 数至少为 1。
- 外部执行精确验证命令时全部通过。
- 随后输入 `/goal`，显示当前没有 active Goal。
- Fixture 之外没有文件变化。

## 失败分类

- 第一阶段写入或运行 Bash：工作模型没有遵守阶段约束，或 Goal kickoff 表达不清。
- 证据不足却显示 `goal met`：Judge false positive。
- Judge 返回未完成但没有续跑：Goal controller 状态机错误。
- 续跑没有携带已有证据或 Judge reason：continuation context 错误。
- 测试失败却显示完成：证据声明或 Judge verification policy 错误。
- 测试通过但 Goal 永不结束：Judge false negative 或 controller 终止错误。

## 2026-08-12 自动化运行结果

DPG-004：

- 两轮 Plan 只调用 Read/Grep；没有 Bash、Edit、Write 或 Agent。
- `2` 保持 Plan，`3` 丢弃并返回 Default。
- Fixture 前后 hash 完全相同，外部验证保持 `1 failed, 1 passed`。

DPG-005：

- 第一轮只解释问题，没有修改或 Bash。
- Judge 返回 `goal not met — continuing (1/100)`，controller 自动进入第二轮。
- 第二轮修改两个 fixture 文件，精确验证得到 `6 passed`。
- 最终显示 `goal met after 2 checked turns (1 continuation)`。
- `/goal` 显示没有 active Goal；独立外部 pytest 复跑仍为 `6 passed`。

### 非阻塞发现：隐藏目录 Grep 造成错误推断

DPG-004 第二轮使用 `Grep path='.'` 时，默认 `hidden=false`，因此结果没有包含
`.dogfood/`。模型随后错误推断用户给出的目标可能是旧路径或 symlink，尽管第一轮
已经成功读取该文件。这个问题没有破坏 Plan 的只读边界或后续 Goal，但暴露了两点：

1. 对隐藏工作目录执行仓库级搜索时，模型需要显式设置 `hidden=true` 或继续使用精确
   path；
2. 模型不应让一次范围更窄的 Grep 结果推翻同一 conversation 中已有的直接 Read
   证据。

这项发现应作为独立 case 或 tool-use 行为改进处理，不改变 DPG-004/005 的 controller
通过结论。
