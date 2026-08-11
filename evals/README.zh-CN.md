# Eval 手册

OpenHarness 的 capability eval 是贡献者手动运行的工作流。它们衡量普通单元测试
无法证明的模型行为，不会在 CI 或默认测试套件中自动运行。

每次运行都必须显式选择 `live`、`record` 或 `replay`。裸 eval 命令会在调用
模型之前 fail closed。

英文版见 [README.md](./README.md)。

## 选择验证层级

使用能够回答当前问题的最低成本层级。每次改动都需要验证，但完整 live eval
不是编辑代码时的内循环。

| 层级 | 运行时机 | 验证内容 | 成本 |
|---|---|---|---|
| L0 — 快速检查 | 每次修改 | 肉眼 review、单个复现、相关单测 | 秒级，不调用模型 |
| L1 — 确定性验证 | 一组改动完成 | 定向 pytest、类型、lint、cassette replay | 分钟级，不调用模型 |
| L2 — 定向 live | dogfood 基本通过 | 用 `--mode live --case` 运行受影响的 1–3 个 case | 少量模型费用 |
| L3 — ratification | 候选行为已经冻结 | 按 dataset card 完整跑 live 稳定性流程，然后 `record` | 成本最高 |

日常 CI 与贡献者门禁是：

```bash
uv run pytest -m "not integration and not eval" -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
```

## 选择 mode

| Mode | 调用当前配置的模型 | 写入 cassette | 使用场景 |
|---|---:|---:|---|
| `replay` | 否 | 否 | 验证 loader、scorer 与已经录制的行为 |
| `live` | 是 | 否 | 定向诊断、dogfood 后确认与稳定性观察 |
| `record` | 是 | 是 | ratification 通过后替换已提交的基线 |

`record` 是基线维护操作，不是普通测试命令。提交前必须 review cassette diff。
Replay 只能证明已录制响应仍满足当前 scorer；当 prompt、工具描述、context
assembly、judge、model 或 provider 改变时，它不能替代 live re-ratification。

## 选择 model

手动 eval 按以下优先级解析 model：

1. 当前命令的 `--model MODEL`；
2. 进程环境中的 `OPENHARNESS_MODEL`；
3. 当前项目 `.env` 中的 `OPENHARNESS_MODEL`；
4. 仍未配置则明确失败。

运行时不再 fallback 到历史 reference model。Reference policy 只属于对应的
`dataset_card.md`。由于 model identity 是 cassette key 的一部分，replay 某个
旧基线时，可能需要显式选择 dataset card 中记录的 model：

```bash
uv run oh dev eval tool_choice --mode replay --model qwen-max
```

`live` 与 `record` 还需要项目 `.env` 中完整的 provider 配置。`replay` 不会发起
provider 请求。

## 标准工作流

### 1. 修改期间只跑确定性检查

实现仍在快速变化时，只跑单个复现和相关单测。不要每修改一点就付费跑完整
live eval。

```bash
uv run pytest tests/tools/test_grep.py -q
```

### 2. 手动 dogfood 受影响行为

通过真实 REPL 工作流操作一遍。明显的路由、超时、渲染或状态机问题，应先修复，
再进入 eval。

### 3. 运行定向 live smoke

dogfood 基本可信后，只运行受影响的 case：

```bash
uv run oh dev eval error_feedback \
  --mode live \
  --case A6-grep-launch-denied
```

`--case` 会在 inference 之前过滤 dataset，因此未选中的 case 不会调用模型。
未知 case id 会失败并显示可用 case catalog。

### 4. Ratify 已冻结的候选行为

先阅读该 capability 的 dataset card，再按照其中声明的完整 live 流程、reference
policy、运行次数与 pass bar 验证。不同 capability 不能互相照搬同一个 `N`。

```bash
uv run oh dev eval error_feedback --mode live
```

### 5. 录制并验证基线

只有 live 结果满足 dataset contract 后，才可以替换 reference cassette：

```bash
uv run oh dev eval error_feedback --mode record
uv run oh dev eval error_feedback --mode replay
git diff -- evals/error_feedback
```

修改 scorer、dataset loader、cassette 实现或共享 eval substrate 后，再手动运行
全部已提交 replay gate：

```bash
uv run pytest -m eval -q --no-cov
```

聚合 gate 使用各 dataset contract 已 ratify 的 cassette identity，刻意不依赖
当前 `.env`。

## 命令参考

```bash
# 查看全部 capability eval
uv run oh dev eval --help

# 查看一项 eval 的 options
uv run oh dev eval error_feedback --help

# 确定性 replay
uv run oh dev eval error_feedback --mode replay

# 运行一个 live case
uv run oh dev eval error_feedback --mode live --case CASE_ID

# 运行完整 live dataset
uv run oh dev eval error_feedback --mode live

# 录制已经 ratify 的响应基线
uv run oh dev eval error_feedback --mode record

# 临时选择其他 model
uv run oh dev eval error_feedback --mode live --model MODEL
```

## Capability catalog

| Eval | 衡量的能力 | 评测契约 |
|---|---|---|
| `focus_state` | 从对话状态中提取当前 goal 与 next step | [dataset card](./focus_state/dataset_card.md) |
| `tool_choice` | 工具选择、参数选择与克制调用 | [dataset card](./tool_choice/dataset_card.md) |
| `error_feedback` | 工具失败后的正确恢复行为 | [dataset card](./error_feedback/dataset_card.md) |
| `skill_trigger` | 是否加载 skill，以及选择哪个 skill | [dataset card](./skill_trigger/dataset_card.md) |
| `memory_decision` | 是否写入项目 memory，以及如何写入 | [dataset card](./memory_decision/dataset_card.md) |
| `memory_read` | 是否读取项目 memory，以及读取哪一项 | [dataset card](./memory_read/dataset_card.md) |
| `memory_compact` | compact 时保留相关事实并排除噪声 | [dataset card](./memory_compact/dataset_card.md) |
| `permission_review` | 审查精确权限请求且不扩大授权范围 | [dataset card](./permission_review/dataset_card.md) |
| `verify_judge` | 独立判断 goal 是否真正完成 | [dataset card](./verify_judge/dataset_card.md) |

这张表只负责导航。链接的 dataset card 才是 capability claim、reference
policy、scorer、pass bar、稳定性要求、known gaps 与复跑方式的权威来源。

## Artifact 布局

```text
evals/<capability>/
├── dataset.yaml       # 版本化 case 与预期行为
├── dataset_card.md    # 评测契约与 pass bar
├── cassettes/         # record 写入的模型响应
└── results/           # 对应 eval 选择保留的运行证据
```

生产 runner 与 scorer 位于 `src/openharness/eval/`。原有
`scripts/spike_*_eval.py` 现在只是 `oh dev eval` 背后的薄启动适配器；贡献者应
优先使用本手册中的 CLI。

## Eval 发生变化时

- Runner、loader、scorer 或 cassette 代码：补确定性测试，运行受影响 replay
  与聚合 replay gate。
- Dataset 或 scorer contract：同步更新 dataset card 并 replay 已提交 artifact；
  只有接受的基线确实改变时才 record。
- Prompt、工具描述、context assembly、judge 行为、model 或 provider：先定向
  live，再按 dataset card 完整 live re-ratification。
- 新 capability eval：增加 dataset、dataset card、runner/scorer、CLI 注册、
  manual-safety tests 与 replay gate。

## 故障排查

### 缺少 `--mode`

这是有意设计的 fail-closed 行为。必须明确选择 `replay`、`live` 或 `record`。

### 缺少 model 配置

在 `.env` 中设置 `OPENHARNESS_MODEL`，或为当前命令传入 `--model`。Eval 不会
静默选择历史 cassette。

### Cassette missing

确认选中的 model 是否存在对应 cassette，并检查 dataset card 的 reference
policy。缺少 cassette 不代表可以自动 record 新基线。

### Replay 通过但 live 失败

Replay 只证明存储的响应和 scorer 仍然一致。Live 才代表当前行为；在 record
之前，应检查 prompt、provider、model、context 与 tool schema 的变化。

### Eval 前已经肉眼发现问题

回到 L0/L1，先修复确定性或 dogfood 缺陷。Eval 不能替代明显的产品验证。
