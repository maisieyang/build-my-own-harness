# REPL 核心工作流：Default、Plan 与 Goal

状态：DPG-001 至 DPG-003 已由用户在 2026-08-11 手动运行并报告符合预期；自动化
runner 与完整 artifacts 尚未建立，因此这不是双线 ratification。

这组实验使用一个简单的折扣计算错误验证 harness 状态转换，而不是模型解决复杂算法
问题的能力。

## 共享环境

```text
.dogfood/work/repl-core-workflows/
├── pricing.py
└── test_pricing.py
```

`pricing.py` 预先种入百分比计算错误；初始验证结果为 `1 failed, 1 passed`。

精确验证命令：

```bash
uv run pytest .dogfood/work/repl-core-workflows/test_pricing.py -q --no-cov
```

启动命令：

```bash
uv run oh --auto --sandbox
```

共享任务文本：

```text
检查并修复 .dogfood/work/repl-core-workflows/pricing.py 中的折扣计算。要求 discount_percent 按百分比计算，只接受 0 到 100，非法值抛出 ValueError，补充边界测试，并运行 uv run pytest .dogfood/work/repl-core-workflows/test_pricing.py -q --no-cov。只修改这个 dogfood 目录。
```

## DPG-001：Default 完成普通任务

直接输入共享任务。实现和测试应被正确修改，精确验证命令通过，working loop 自然结束
并返回 `>>>`；不能出现 Plan menu 或 Goal controller 消息。

## DPG-002：Plan 只探索，不执行

输入 `/plan` 后发送共享任务。Agent 只能使用只读能力；fixture hash 必须保持不变。
出现 Plan menu 后输入 `1`，应返回 Default，但不能自动执行刚批准的计划。

## DPG-003：Goal 持续工作直到通过验证

输入 `/goal ` 加共享任务全文，不再发送“开始”消息。Goal 应立即开工，最终显示
`goal met after ...`，外部验证实际通过。随后输入 `/goal`，不能显示仍有 active Goal。

基础 case 不强制要求 `goal not met — continuing`；首轮证据完整时，零次自动续跑是
正确结果。

## 基础验收结论

手动链路已经观察到以下结果：Plan 未修改 fixture；批准后只返回 Default；Goal 立即
执行；Judge 正常收口；最终外部验证通过。由于 transcript、hash 与结构化 result
尚未归档，自动化 runner 完成前仍保留“仅手动通过”的标记。

## Dogfood 发现：Goal 完成轮次文案

DPG-003 首次手动运行完成时显示：

```text
goal met after 0 auto-turn(s)
```

Controller 实际执行了 1 个经 Judge 检查的 Goal turn，只是没有触发 continuation。
原文案仅展示 continuation 计数，容易被理解为 Goal 没有工作。

修复后的统计分别展示经检查的 turn 和 continuation：

```text
goal met after 1 checked turn (0 continuations)
```

当 Judge 判定未完成并续跑 2 次后完成时，应显示：

```text
goal met after 3 checked turns (2 continuations)
```

确定性测试已经覆盖首次完成、连续续跑和 permission park/resume；修复后的真实 REPL
文案仍需在下一次手动 DPG-003 中复核。
