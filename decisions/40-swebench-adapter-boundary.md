# Decision 40 — SWE-bench Lite Adapter (benchmark track M1)

> Created 2026-07-08 · 中文
>
> 配套读物：
> - `learnings/openharness-first-principles.md` — loop-runtime 是被测单元
> - `decisions/05-cli.md` D5.7 — integration marker 先例
> - `pyproject.toml` [tool.coverage.run] omit 注释 — eval/ experimental 先例
> - SWE-bench Lite: <https://www.swebench.com/lite.html>（300 题，冻结）

---

## 一、Why now

harness 作为代码 artifact 已过说服水位，缺的是**使用证据**和**行业可读的度量**。
SWE-bench Lite 是唯一同时满足「harness 在回路里 / 难度落在中档模型的信号区间 /
业内免解释」的尺。本 phase 只搭 adapter（喂题 → 跑 `oh` → 收 patch），
评测本身交官方 `sb-cli` 云端，不自建。

## 二、In / Out

**IN（M1 必做）**:

- dataset：HF datasets-server 拉 SWE-bench Lite 300 题 → 本地 JSONL 缓存
- workspace：bare-clone 缓存 + per-instance fresh checkout @ base_commit
- runner：headless 驱动 `oh ask -p --output-format json`，收 envelope + `git diff`
- 产出：SWE-bench 标准 `predictions.jsonl` + per-instance run record（归因原料）
- CLI：`oh bench swebench fetch / run`

**OUT（推迟 / 不做）**:

| 推到哪 | 项 | 一句话原因 |
|---|---|---|
| M2 | SWE-bench-Live 对照组（20-30 题） | 先让主跑通路收口 |
| M2 | failure taxonomy 报告器 | 归因需要先有 eval 结果回流 |
| M2 | 并行跑多 instance | 串行先验证正确性，避免 API 限速纠缠 |
| 不做 | 自建评测（跑 FAIL_TO_PASS） | 官方 sb-cli 云端已解决，且自建易与官方判定漂移 |
| 不做 | --verify 挂 hidden tests | 作弊红线，见 D40.3 |

## 三、Decisions

### D40.1 — 代码进 src，数据/产物进 benchmarks/

**Chosen**: adapter 代码落 `src/openharness/swebench/`（mypy strict + ruff 全覆盖），
数据缓存/workspace/predictions 落仓库顶层 `benchmarks/swebench/`（gitignore 缓存与
workspace，报告与 predictions 入库）。

**Why**: 完全照 `eval` 先例——runner 在 src 受质量门管，数据在顶层目录。

**Alternatives**: 顶层 `benchmarks/` 放代码（逃出质量门，违背 production bar）/
独立仓库（割裂使用证据与 harness 本体）。

**Reversibility**: easy——纯目录搬移。

**Anti-scope**: coverage gate 照 eval 先例 omit `src/openharness/swebench/*`
（experimental），测试照常跑 CI；毕业时再收编进 95% 门。

### D40.2 — subprocess 驱动 `oh`，不做 in-process import

**Chosen**: runner 以子进程调 `oh ask -p ...`，被测单元 = 用户实际运行的完整 CLI
surface（arg 解析、headless 权限姿态、journal 接线全在回路里）。invoker 以可注入
callable 形式暴露，单测注入 fake。

**Why**: benchmark 的意义是测「交付物」，in-process 会绕开 CLI 层，测到的不是用户拿到的东西。

**Alternatives**: import `run_query`（快但绕过 L1-L7 全部 loop-runtime 层，恰好是要被测的部分）。

**Reversibility**: easy。

### D40.3 — hidden-test 防火墙（红线）

**Chosen**: 泄题字段——`patch`（gold 修复）/ `test_patch` / `FAIL_TO_PASS` /
`PASS_TO_PASS` / `hints_text`（issue 后续评论，官方跑法默认不用）——**永不**进
prompt、env、workspace 可见文件；prompt builder 有专门测试断言此不变量。

**Why**: 泄漏 = 作弊 = 全部结果作废；这是 adapter 唯一不可逆的信誉风险。

**Alternatives**: 无——SWE-bench 提交规范的硬约束。

**Reversibility**: hard——一旦泄漏跑出的结果不可修复，只能作废重跑。

### D40.4 — dataset 走 datasets-server JSON API + 本地 JSONL 缓存

**Chosen**: `fetch` 用 stdlib `urllib` 分页拉 HF datasets-server rows API 落
`benchmarks/swebench/dataset/swe-bench-lite-test.jsonl`；`run` 只读本地文件，
不碰网络。

**Why**: 零新依赖（不引 `datasets`/`huggingface_hub` 重型库）；run 阶段确定性、离线可复跑。

**Alternatives**: `datasets` 库（+几百 MB 依赖树进可选组，为 300 行 JSON 不值）。

**Reversibility**: easy——loader 只认 JSONL，上游换获取方式不影响下游。

### D40.5 — workspace：bare 缓存 + fresh clone；patch = `add -A && diff --cached`

**Chosen**: 每 repo 一次 `git clone --bare` 进缓存目录；每 instance 从缓存
clone + `checkout base_commit`；跑完 `git add -A` 后 `git diff --cached` 取 patch
（含新增文件），显式排除 `.openharness/`。运行时 `OPENHARNESS_ENABLE_MEMORY=false`
+ `OPENHARNESS_SNAPSHOT__ENABLED=false` + `--no-skills --no-commands`。

**Why**: 缓存省 300 次网络 clone；fresh clone 保证 instance 间零污染；关掉
memory/snapshot 既防 diff 混入 harness 产物、也防用户本机记忆污染实验。

**Alternatives**: worktree 复用一个 clone（instance 间状态泄漏风险）/ 不关 memory
（实验被本机状态污染，不可复现）。

**Reversibility**: easy。

### D40.6 — 权限姿态：headless fail-closed + 白名单开洞；Bash 只在 sandbox 下放行

**Chosen**: 跑 instance 时 env 注入
`OPENHARNESS_PERMISSIONS__ALLOW="Edit(**),Write(**)"`（读类工具 headless 本就放行）。
Bash 默认不开；`--sandbox` 传入时追加 `Bash(*)`（命令进 Docker、network none、
cwd bind-mount）。

**Why**: 无沙箱放 Bash = 模型任意命令跑在本机真实环境，红线；有沙箱时 Bash 是模型
探索/复现问题的正当能力。

**Alternatives**: 全程禁 Bash（把「模型想跑复现脚本而不能」记成 harness 失败，
污染归因）/ 无沙箱白名单前缀（前缀可被 `;`/`&&` 绕，rules.py 自己的警告）。

**Reversibility**: easy——一个 env 组装函数的分支。

### D40.7 — v1 gate 姿态：single-shot，无 --verify / --goal-condition

**Chosen**: M1 每 instance 跑 `--max-iter 1` 单发，不挂任何 gate。

**Why**: hidden tests 是红线（D40.3）不能当 gate；repo 自带测试套件在无沙箱 v1
跑不起来。归因原料 = stop_reason + patch 空/非空 + sb-cli 判定回流。

**Alternatives**: `--goal-condition` LLM judge 自评 gate（M2 实验轴——正好可以量
「加 judge gate 对 resolve rate 的增量」，这本身是报告的一节）。

**Reversibility**: easy——runner 加参数即可。

### D40.8 — predictions 双轨产出：标准三字段 + run record

**Chosen**: `predictions.jsonl` 严格三字段
（`instance_id` / `model_name_or_path` / `model_patch`，naming =
`openharness-{version}+{model}`）；旁路 `records.jsonl` 每 instance 一行存
envelope 摘要（stop_reason / usage / num_turns / duration / patch 行数 / 退出码）。

**Why**: 前者给 sb-cli，后者是 failure taxonomy 的全部原料——评测判 fail 后靠它
归因 harness vs 模型。

**Alternatives**: 只出标准文件（归因时只能重跑，浪费一轮 API 钱）。

**Reversibility**: easy。

## 四、Acceptance（phase 级，跨 task）

- [ ] regression: 全仓 `uv run pytest -q --no-cov` 绿
- [ ] dogfood: `oh bench swebench fetch` + `oh bench swebench run --limit 1` 在
      真模型下产出非空 `model_patch` 的 predictions.jsonl
- [ ] 防火墙测试存在且 RED 见过：prompt/env 断言不含 FAIL_TO_PASS/PASS_TO_PASS 内容
- [ ] 质量门：`mypy --strict src/` + ruff 双绿
- [ ] 文档同步：CHANGELOG + learnings 记录（M1 收口时）

## 五、Tasks

### T1 — instance 模型 + JSONL loader

**Description**: `SWEBenchInstance` pydantic 模型（宽容未知字段），JSONL 加载 +
`--limit`/按 id 过滤。

**Acceptance**:
- [ ] 加载 fixture JSONL 得到类型化 instance 列表
- [ ] 缺关键字段（instance_id/repo/base_commit/problem_statement）报差异化错误
- [ ] hidden-test 字段以私有形态保存，不进 `repr`

### T2 — prompt builder + 防火墙

**Description**: instance → headless goal prompt（问题陈述 + 修复指令 + 不改测试约束）。

**Acceptance**:
- [ ] prompt 含 problem_statement 与「产出最小修复、不动测试文件」指令
- [ ] 断言 FAIL_TO_PASS/PASS_TO_PASS 的内容字符串不出现在 prompt
- [ ] prompt 不含绝对路径等本机泄漏

### T3 — workspace git 操作

**Description**: repo 缓存（bare）、per-instance checkout、patch 提取。

**Acceptance**:
- [ ] 本地 tmp git 仓库 fixture 上：prepare → 修改文件（含新增）→ patch 含两者
- [ ] `.openharness/` 下产物不进 patch
- [ ] 二次 prepare 同 instance 得到干净 workspace（无上次残留）

### T4 — headless invoker + envelope 解析

**Description**: argv/env 组装（D40.5/D40.6 全部开关）、超时、json envelope 解析为结果对象。

**Acceptance**:
- [ ] fake invoker 单测：argv 含 `-p --output-format json --no-skills --no-commands`
- [ ] env 断言：ALLOW 规则、memory/snapshot 关闭；sandbox on 才有 `Bash(*)`
- [ ] envelope 缺字段/非 JSON/超时 → 差异化错误结果，不 crash 整批

### T5 — orchestrator + 双轨写出

**Description**: 串行跑 instance 列表，写 predictions.jsonl + records.jsonl，可断点续跑（跳过已有 instance_id）。

**Acceptance**:
- [ ] fake invoker 跑 3 个 instance 产出 3+3 行两文件
- [ ] 单 instance 失败不中断批次，record 记录失败形态
- [ ] 重跑跳过已完成 instance（幂等）

### T6 — CLI 接线 + fetch

**Description**: `oh bench swebench fetch/run` typer 子 app；fetch 走 datasets-server 分页。

**Acceptance**:
- [ ] `oh bench swebench --help` 可见两命令
- [ ] fetch 注入 fake http 单测分页拼接正确；真网冒烟拉满 300 行
- [ ] run 缺 dataset 文件时报错并提示先 fetch

### T7 — 端到端冒烟（真模型）

**Description**: 1 个 instance 全链路：fetch → run → 非空 patch。

**Acceptance**:
- [ ] predictions.jsonl 1 行，`model_patch` 非空且 `git apply --check` 通过
- [ ] records.jsonl 含 usage/duration/stop_reason
- [ ] 全程无 TTY 交互（headless 姿态成立）

## 六、Wiring audit

| Layer | Verdict | Reasoning（一句话） |
|---|---|---|
| `permissions/` | unchanged | 只消费既有 `OPENHARNESS_PERMISSIONS__ALLOW` env 面 |
| `hooks/` | unchanged | 不注册新 hook |
| `services/snapshot·session_memory·compact` | verification | 靠 env 关闭——冒烟时验证开关真的关得掉 |
| `engine/` | unchanged | 完全在 CLI 边界外驱动 |
| `skills/` `commands/` `bundles/` | unchanged | `--no-skills --no-commands` 绕开 |
| `cli.py` | extension | +1 行注册 `bench` 子 app |
| `observability` | unchanged | 消费 stderr 日志，不改 |
| `execution/` | verification | sandbox 分支依赖 `--sandbox` 语义在 alien repo cwd 下成立 |
| `eval/` | unchanged | 平行子系统，仅同为 coverage-omit 先例 |

**Conclusion**: 1 extension + 2 verification + 其余 unchanged——adapter 形态成立
（消费者，不是侵入者）。

## 七、References

- SWE-bench predictions 格式: <https://www.swebench.com/sb-cli/>
- HF datasets-server rows API: <https://huggingface.co/docs/dataset-viewer/rows>
- mini-SWE-agent（scaffold-gap 基线）: <https://github.com/SWE-agent/mini-swe-agent>
