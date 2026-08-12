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

## 自动化 Runner

Runner 只允许手动触发，不接入 CI。它使用真实 `oh` 进程、当前 `.env` 中配置的
模型和真实工具，并通过 PTY 保持 `stdin/stdout isatty=True`，走与手动操作相同的
prompt-toolkit REPL 路径。先执行不调用模型的 fixture 检查：

```bash
uv run python -m dogfood.repl_runner --prepare-only
```

再按需运行一组 live dogfood：

```bash
# DPG-001～003：Default、Plan approve 与基础 Goal
uv run python -m dogfood.repl_runner --suite core

# DPG-004～005：Plan keep/discard 与 Goal auto-continue
uv run python -m dogfood.repl_runner --suite depth

# 运行全部 case；会产生多次真实模型调用
uv run python -m dogfood.repl_runner --suite all
```

单次等待默认最多 900 秒，可使用 `--timeout` 显式调整。Runner 会实时转发 REPL
输出，并把 transcript、fixture hash、验证输出和结构化结果写入 `.dogfood/artifacts/`。

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
- [`context-management-lifecycle.md`](./cases/context-management-lifecycle.md)：Context 的来源、
  Tool Result、Skills、Compact、Snapshot/Resume、Plugins 与 Agent 隔离。

Context suite 使用一个不会调用模型的只读 inspector 收集阶段证据：

```bash
uv run python -m dogfood.context_inspector prepare \
  --target .dogfood/work/context-management-20260812-01

uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label 00-before-session
```

完整手动输入、双终端操作方式和通过标准见对应 runbook。

## 后续扩展

后续 case 将继续覆盖 permission parking 与审批、长程任务，以及工具或 provider 失败
后的恢复。Context suite 先手动执行；契约稳定后，再把相同输入和 inspector 断言接入
PTY runner。
