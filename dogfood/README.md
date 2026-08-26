# OpenHarness Dogfood

这个目录记录 OpenHarness 开发过程中的真实产品实验。Dogfood 使用当前 checkout、
当前配置的模型、真实 REPL 和真实工具，由开发者手动触发；它不是单元测试、CI 门禁
或模型 eval。

## 手动优先，按需自动化

Dogfood 的第一责任是让人亲自使用产品、记录体感和作出判断。成熟且需要频繁回归的
独立 case 可以再增加 PTY 自动重放，但自动化不是 Dogfood 的默认完成条件。

当前有两种形态：

1. `repl_runner` 的既有核心/controller case 同时提供手动 runbook 与真实 PTY 重放。
2. 完整 workflow journey 保持 manual-only：Runner 只准备环境和记录证据，不发送输入，
   不根据工具名、固定 Memory schema 或 transcript marker 自动判定结果。

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

要连续体验并记录 `Default → Plan → Goal → Compact → Snapshot → Resume → Memory`，使用
manual-only workflow journey：

```bash
# 亲自输入和判断；runner 只记录 fresh + resume 两个真实 REPL transcript
uv run python -m dogfood.workflow_journey manual \
  --run-id 20260819-workflow-manual-01
```

`prepare` 可以只生成并检查 fixture、runbook 和初始 checkpoint；完整交互仍由
`manual` 入口串联 fresh 与 resume。每个阶段都带可选 `capture` 命令。完整说明见
[`workflow-journey.md`](./cases/workflow-journey.md)。

Runner 会实时转发 REPL 输出，并把 transcript、fixture hash、Snapshot/Memory checkpoint
和供运行者填写的 `notes.md` 写入 `.dogfood/artifacts/`。

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
- [`workflow-journey.md`](./cases/workflow-journey.md)：贯穿 Default、Plan、Goal、Compact、
  Snapshot、Resume 与 Memory 的单条 manual-only journey。

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
