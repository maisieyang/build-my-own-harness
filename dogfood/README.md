# OpenHarness Dogfood

这个目录记录 OpenHarness 开发过程中的真实产品实验。Dogfood 使用当前 checkout、
当前配置的模型、真实 REPL 和真实工具，由开发者手动触发；它不是单元测试、CI 门禁
或模型 eval。

## 一份契约，两条执行线

每个 case 都通过两条路径执行：

1. 人按照 runbook 手动操作，并记录真实使用体感。
2. 终端自动化脚本发送相同输入，并检查相同的外部证据。

两条路径必须共享 fixture、初始状态、启动命令、输入文本和验收标准。自动化可以收集
证据、重置 fixture 和断言结果，但不能用 mock 替代真实模型或产品 runtime。

## 证据优先级

1. 外部状态：文件 hash、文件内容、命令退出状态和测试输出。
2. Harness 状态：模式切换、parked request、snapshot 和 controller 状态。
3. Transcript 声明：工作模型或 Judge 声称发生了什么。

模型的自我声明不能单独证明修改或验证已经成功。

运行证据保存在被 Git 忽略的目录中：

```text
.dogfood/artifacts/<run-id>/<case-id>/
├── transcript.txt
├── before.sha256
├── after.sha256
├── verification.txt
└── result.json
```

## Case 规范

每个 case 必须包含稳定 ID、单一待验证行为、可重复 fixture、精确输入、外部验收标准、
失败证据和范围排除。基础工作流稳定后，才加入复杂仓库任务和长程 soak case。

## 当前实验集

- [`repl-core-workflows.md`](./cases/repl-core-workflows.md)：Default、Plan 与 Goal 基础链路。
- [`repl-controller-depth.md`](./cases/repl-controller-depth.md)：Plan 分支与 Goal 自动续跑。

## 后续扩展

后续 case 将覆盖 permission parking 与审批、resume 与 snapshot、compaction、skill、
command、长程任务，以及工具或 provider 失败后的恢复。
