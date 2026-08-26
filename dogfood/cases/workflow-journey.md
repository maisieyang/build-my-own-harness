# 完整工作流手动 Journey：Default → Plan → Goal → Compact → Resume → Memory/Snapshot

这条 journey 让使用者亲自走完 OpenHarness 的核心工作流，并记录真实体感。它不是 CI、
Eval 或自动协议测试；Runner 不发送 prompt，也不根据工具名、固定 Memory schema 或
transcript marker 判定 pass/fail。

## 设计边界

Journey 只自动处理那些不应该消耗使用者注意力的实验基础设施：

- 从 tracked fixture 重建一次性工作目录；
- 清理该 fixture cwd 精确对应的 Snapshot 与 Memory；
- 安全加载当前仓库 `.env`；
- 记录 fresh/resume transcript、fixture hash、Snapshot 和 Memory checkpoint；
- 生成 `notes.md`，供运行者写下观察与判断。

以下内容明确属于人：

- 如何自然表达任务；
- 是否觉得 Plan、Goal、Compact 和 Resume 好用；
- 模型的工具选择与 Memory 分类是否合理；
- 什么算通过、什么需要改进。

## 六个连续观察点

| Case | 链路 | 人要回答的问题 |
|---|---|---|
| DPG-016 | Default | 普通调查是否克制；自然的“请记住”是否形成合理 Memory |
| DPG-017 | Plan | 规划是否只读、完整、容易批准；批准后是否保持人类控制 |
| DPG-018 | Goal | 是否真正沿用已批准计划；执行、验证与 Judge 收口是否可信 |
| DPG-019 | Compact + Snapshot | 压缩后是否保留最新事实和未完成工作，并进入可恢复 Snapshot |
| DPG-020 | Resume | 新进程是否自然恢复准确工作状态，而不是重新探索 |
| DPG-021 | Memory | 正常询问“还记得吗”时，是否正确访问并应用 durable Memory |

建议输入由 `dogfood.workflow_journey` 统一生成，但它们不是必须逐字匹配的测试向量。可以按
真实说话方式微调，只要观察目标不变。

## 专用任务

fixture 是一个故意写错的折扣函数，初始 pytest 基线为 `1 failed, 1 passed`。计算任务保持
简单，是为了把注意力留给 Harness 工作流，而不是模型的算法能力。

运行时工作目录：

```text
.dogfood/work/workflow-journey/
├── AGENTS.md
├── pricing.py
├── pytest.ini
└── test_pricing.py
```

Plan 阶段会自然补充产品要求：折扣百分比只接受 0～100，非法值需要明确报错，并考虑边界
测试与验证方式。Goal 会把批准后的关键决定编译成自包含契约：明确目标、验收标准、精确
验证命令与有界停止条件。它仍然保留“按刚才批准的计划执行”这个自然交接，但不要求 Worker
或 Judge 从 Goal 之前的历史猜测什么才算完成。

fixture 的精确验证命令是：

```bash
python -m pytest -c pytest.ini test_pricing.py -q --no-cov
```

本地 `pytest.ini` 阻止 pytest 向上查找仓库根配置，因此命令只需读取当前 fixture 和已授权的
Python runtime，不需要扩大沙箱边界。

## 启动并记录

从仓库根目录运行：

```bash
uv run python -m dogfood.workflow_journey manual \
  --run-id 20260819-workflow-manual-02
```

外层 runner 会：

1. 重置专用 fixture 和它自己的 Snapshot/Memory；
2. 验证初始失败基线；
3. 生成并打印自然语言 runbook；
4. 启动 fresh REPL，由使用者完成 DPG-016～019；
5. 第一次 `/exit` 后等待使用者按 Enter；
6. 启动 `--resume` REPL，由使用者完成 DPG-020～021；
7. 记录两个 transcript 和最终 checkpoint，不作通过判定。

如果只想检查实验准备结果而不进入 REPL，可以运行：

```bash
uv run python -m dogfood.workflow_journey prepare \
  --run-id 20260819-workflow-manual-02
```

`prepare` 只重建 fixture、验证基线并生成初始 artifact，不启动会话。完整交互仍使用上面的
`manual` 命令。每个阶段也可以在另一个终端执行生成 runbook 中的 `capture` 命令，留下
更细的结构化证据。

## 自然输入原则

主 Journey 的用户输入不包含：

- `MemoryUpsert`、`MemoryShow` 等工具名；
- 强制 Memory type、name、slug 或 body；
- 为 scorer 准备的固定短语；
- “必须调用某工具”之类隐藏 oracle；
- 逐字重复整个已批准 Plan。Goal 只固化执行与验收所必需的决定。

例如，Memory 只这样提出：

```text
另外，请记住我的协作习惯：先给我计划，等我明确同意后再执行；每轮结束时告诉我实际运行了什么验证。
```

Resume 后只自然询问：

```text
你还记得我之前告诉你的协作习惯吗？
```

是否写 Memory、怎样分类和命名、回忆时如何访问，都是本次 Dogfood 要观察的产品行为，
不能提前写进 prompt。

## 记录格式

```text
.dogfood/artifacts/<run-id>/workflow-journey/
├── manifest.json
├── manual-runbook.md
├── notes.md
├── baseline.txt
├── transcript-fresh.txt
├── transcript-resume.txt
├── transcript.txt
├── result.json              # recorded/interrupted，不包含自动 pass/fail
└── checkpoints/
    ├── 00-prepared.json
    ├── after-fresh.json
    └── after-resume.json
```

Checkpoint 保存可供事后检查的外部状态，但不替运行者下结论。运行完成后，把你的判断、困惑、
意外行为和希望改进的地方写入 `notes.md`；这才是这条 Dogfood 的主要产物。
