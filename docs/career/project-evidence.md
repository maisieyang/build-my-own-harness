# OpenHarness 求职证据底稿

> 用途：这是 README、简历和面试叙事的共同事实源，不是对外营销文案。
> 数字基线审计于 2026-07-30；任何对外数字更新前，先重新运行底部命令。

## 一句话定位

我从零实现并完整评测了一个 Python agent harness，重点不在“再做一个聊天
CLI”，而在强模型周围的控制面：类型化工具执行、授权与隔离、长上下文状态、
外部完成判定、可恢复自治循环，以及 capability-level eval。

不建议把项目定位成“Claude Code clone”。更准确的区分是：

> 模型负责提出动作，harness 负责动作能否执行、在哪里执行、状态如何延续，
> 以及什么证据才算任务完成。

## 事实卡

| 维度 | 可复核事实 | 证据 |
|---|---|---|
| 建造周期 | 2026-04-27 至 2026-07-29；47 个活跃提交日 | `git log` |
| 提交历史 | 415 commits；412 个使用同一作者身份，另 3 个是同一邮箱的名称变体 | `git shortlog -sne --all` |
| 代码与测试 | 151 个 source Python files；214 个 test Python files | `rg --files` |
| 测试 | 2,783 collected；2026-07-30 实跑 2,775 passed、8 environment-gated skipped | `uv run pytest -q` |
| Coverage | 95.29%；门禁 95% | `pyproject.toml`、pytest coverage |
| 静态质量 | Ruff lint/format 通过；`mypy --strict` 151 个 source files 通过 | CI 与本地命令 |
| CI | Python 3.10 / 3.11 matrix | `.github/workflows/ci.yml` |
| 设计记录 | 50 份 decisions、48 份 task plans、47 份 learnings | 对应目录 |
| Benchmark | SWE-bench Lite 170/300 resolved = 56.7% | `benchmarks/swebench/RUNLOG.md` |
| Benchmark 条件 | qwen3.7-max、thinking off、OpenHarness 0.4.0、自建官方 evaluator | 同上 |

8 个 skip 的边界必须说清：1 个 live API integration，6 个 Docker sandbox，
1 个 gVisor。默认核心套件没有失败；没有对应环境时，不声称真实 Docker/gVisor
integration 已在本机验证。

## 能力与证据

### 1. Agent runtime 与协议

**可说：** 实现 OpenAI-compatible streaming client、Pydantic v2 wire types、
tool-call loop、retry 与 typed event stream；同一 engine 驱动 REPL、headless
loop 和 benchmark adapter。

**证据：**

- `src/openharness/api/`
- `src/openharness/protocols/`
- `src/openharness/engine/query.py`
- `src/openharness/_stream_render.py`
- `tests/api/`、`tests/engine/`、`tests/cli/test_render.py`

**不要说：** “支持所有 provider”或“原生支持 Anthropic”。准确表述是
“支持 OpenAI-compatible Chat Completions endpoint；没有 native Anthropic
Messages adapter”。

### 2. 工具安全：授权不等于隔离

**可说：** 实现 allow/ask/deny 规则、敏感路径与不可逆 Git 红线、headless
fail-closed、hooks，以及可选 Docker/gVisor execution substrate。

**关键故事 F9：** Dogfood 发现交互式 Bash 可以绕过基于 path specifier 的权限
模型，因为任意 subprocess 的 side effects 无法从 path metadata 完整推断。修复不是
增加更多 path matching，而是让 interactive、mutating、pathless 工具进入 ASK；
真正的 arbitrary-command containment 交给 sandbox。

**证据：**

- `src/openharness/permissions/`
- `src/openharness/execution/`
- `src/openharness/tools/bash.py`
- `decisions/44-interactive-bash-ask.md`
- `learnings/dogfood-day2-error-feedback.md`

**面试结论：** authorization 决定“是否允许”，containment 限制“允许后能伤到
哪里”；两者不能由同一组路径规则替代。

### 3. 长上下文与可恢复状态

**可说：** 实现 tool-result truncation、`PromptTooLong` reactive recovery、
显式 `/compact`、project memory、snapshots 与 resume。

**关键故事 F17：** `/goal` 完成/清除 sentinel 最初在 engine snapshot 之后写入，
进程退出再 resume 会复活已经完成的 goal。修复把 terminal sentinel 的持久化
提升为状态转换的一部分，并增加跨 snapshot seam test。

**证据：**

- `src/openharness/compaction/`
- `src/openharness/services/compact.py`
- `src/openharness/services/snapshot.py`
- `src/openharness/memory/`
- `src/openharness/repl.py`
- `tests/cli/test_chat_goal.py`
- `decisions/48-repl-goal-boundary.md`
- `learnings/dogfood-goal-todo-mvp.md`

**面试结论：** 恢复语义不能只测试模块内部；状态转换与持久化边界之间的顺序才是
真正的不变量。

### 4. 外部完成判定与自治循环

**可说：** 实现三种有不同 context semantics 的循环：

1. `/goal`：每次 working-model reply 后，把累计 transcript 交给独立、
   tool-disabled judge；失败反馈回到同一 session。
2. Headless repair loop：每个 attempt 使用 fresh context，通过 `--verify` 或
   `--goal-condition` 外部判定，支持 `--decompose`、worktree isolation、
   run journal 与 resume。
3. Autopilot：持久化、去重、优先级排序的本地顺序 intake queue。

**安全边界：** Judge transcript 被标为 untrusted data，使用显式 delimiter；
exception、空输出、malformed JSON 与非法 score 全部 fail closed；所有循环都有
turn/iteration cap。

**证据：**

- `src/openharness/verification/`
- `src/openharness/services/decompose.py`
- `src/openharness/services/run_journal.py`
- `src/openharness/services/run_session.py`
- `src/openharness/services/worktree.py`
- `src/openharness/services/autopilot.py`
- `tests/cli/test_chat_goal.py`
- `tests/verification/`

**不要说：** “LLM judge 保证正确”。准确表述是“为不可执行的语义标准提供独立、
fail-closed 的 soft gate；有 executable oracle 时优先用 `--verify`”。

### 5. Eval 与证据纪律

**可说：** Eval 按 capability decision surface 设计，而不是只看最终文本；同时
使用 programmatic scorer、LLM judge、cassette/replay、result hash 与 judge
meta-evaluation。

**关键故事 F6：** Bash 输出截断早期只保留 head，恰好丢掉 pytest summary。模型
随后捏造测试数量，并在后续 turn 把自己的错误总结当成事实。修复改为 head+tail
截断。这里的结论不是“模型会 hallucinate”这么泛，而是：

> Harness 决定模型能看见什么证据；错误的 evidence visibility 会制造并强化
> hallucination。

**证据：**

- `src/openharness/eval/`
- `evals/`
- `tests/eval/`
- `learnings/dogfood-day1-tool-skill.md`
- `learnings/dogfood-day2-error-feedback.md`

### 6. SWE-bench 战役与运行工程

**可说：**

- 用公开 `oh` subprocess path 跑完 SWE-bench Lite 300 题；
- 本地阶段 268/300 completed，284 个 non-empty patches；
- 官方托管服务连 gold patch 都评测失败后，在阿里云 ECS 自建官方 harness；
- 最终 170/300 resolved = 56.7%；
- 战役反向发现 5 个 harness bug/gap，并把修复放回 production path。

五个缺口：

1. package version drift；
2. child-process config source drift；
3. 缺少错误消息承诺的 `--max-turns`；
4. retry 未覆盖 mid-stream disconnect；
5. 缺少 generic provider request passthrough。

**最重要发现：** resolved 的 turn 中位数为 11，unresolved-completed 为 13。
模型做了很多轮并不等于正确完成，因此 completion gate 不能建立在 self-report 上。

**证据：**

- `benchmarks/swebench/RUNLOG.md`
- `benchmarks/swebench/TAXONOMY.md`
- `benchmarks/swebench/out/records.jsonl`
- `benchmarks/swebench/out/predictions.jsonl`
- `benchmarks/swebench/out/official-verdicts.json`

**不要单独说：** “97 个失败 100% 都是模型责任”。必须补充限定：这是对
97 个 unresolved 且已完成归因样本的分类；另有 17 个 matplotlib evaluation
environment build failures，不在这个分母中。

## 核心竞争力

这个项目对 harness engineer 岗位最有价值的不是功能数量，而是以下能力组合：

1. **能定位 model 与 harness 的责任边界。** 不把所有失败都归因于 prompt 或模型，
   也不把模型错误包装成 runtime 成功。
2. **能设计外部 oracle。** 区分 deterministic verify、probabilistic judge 与
   human approval，给每种 gate 合适的权限和停止语义。
3. **能处理跨层 seam。** 权限与 subprocess、state transition 与 snapshot、
   output truncation 与模型认知、provider defaults 与实验归因。
4. **能把 dogfood 变成系统改进。** 失败被写成可复现测试、decision amendment、
   eval case 或 benchmark taxonomy，不停留在 prompt tweak。
5. **能运行真实评测战役。** 处理配置漂移、长跑恢复、计费/网络/provider drift、
   官方评测故障和自建 evaluator。

## 对外 claim 分级

### 可直接公开

- “From-scratch Python agent harness。”
- “2,783 tests，95.29% coverage，mypy strict，Python 3.10/3.11 CI。”
- “SWE-bench Lite 170/300 resolved（56.7%），qwen3.7-max，thinking off，
  self-hosted official evaluator。”
- “实现 typed tool loop、permission/sandbox、compaction/resume、skills/MCP、
  independent completion gates、repair loop 与 benchmark adapter。”

### 需要上下文

- “Built to a production bar”：可用于描述工程标准，不应写成“已在生产环境大规模
  服务用户”。
- “Provider-agnostic”：应改为“OpenAI-compatible endpoint compatible”。
- “Claude Code plugin compatible”：必须明确是 metadata + `SKILL.md` tree 的
  partial compatibility。
- “Autonomous”：必须同时说 external gate 与 bounded loop，不能暗示无限自治。

### 暂时避免

- “Production-grade”作为不带限定的既成事实。
- “All tests pass”而不说明 environment-gated skips。
- “Supports Anthropic/Claude natively”。
- “Full Claude Code plugin compatibility”。
- “SWE-bench failures are all model failures”而不说明分类分母。
- 把 source LOC、test LOC 或 commit 数当作主要价值证明。

## 当前可信度缺口

1. `src/openharness/cli.py` 仍是大型模块；虽然覆盖率 92%，但职责拆分是可维护性
   风险，面试时应主动承认。
2. Native provider adapter 只有 OpenAI-compatible path；Anthropic Messages
   等原生 protocol 尚未实现。
3. Docker/gVisor integration 在当前 Mac 环境被跳过，不能用本次本地运行证明。
4. Autopilot 是本地顺序 queue，不是 distributed scheduler、PR automation 或
   multi-worker orchestration。
5. Claude Code plugin support 不包含 HTTP/OAuth `.mcp.json` 与 declarative
   agents。
6. Semantic judge 即使经过 meta-eval 仍是 probabilistic soft gate；command
   oracle 优先级更高。

## 复核命令

```bash
git log --oneline | wc -l
git shortlog -sne --all
uv run pytest -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
jq '{resolved: (.resolved | length), unresolved: (.unresolved | length)}' \
  benchmarks/swebench/out/official-verdicts.json
```

SWE-bench 的最终 `170/300` 分数还依赖 RUNLOG 中记录的 17 个 environment-build
failure 与逐题聚合过程，不能只从 `official-verdicts.json` 的 key 数量反推出完整
300 题分母。
