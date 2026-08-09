# tool_choice Eval Dataset Card

> D41 P0 首个落地 · 2026-07-08 · four-declaration header per D35.3 + D41.5

## Declarations(四声明头)

**1. Capability claim**:这份 eval 测试**当前 production tool registry
(`create_default_tool_registry()`:Read/Write/Edit/Bash/Grep/SpawnAgent)
的工具描述 + `build_system_prompt` 组装**在决策面 **#2(tool 选择 + input
构造;D41 §一 A3/A4)** 上的单步决策质量。

**不为之设计**:
- 跨 model 强弱比较(D35.8 前置未满足)
- 多轮工具链 / 错误恢复的全轨迹质量(#3 的范围;TC4 只测报错后**单步**决策)
- 整体 agentic 能力评估(它只是 #2 的一个 instance)

**2. Input spec**:合成对话(单条用户消息为主;TC4 为种植错误历史的三消息
形态)。N=11,覆盖 6 个 capability(TC1 selection / TC2 discrimination /
TC3 param,含项目指令驱动的命令构造 / TC4 self-correction / TC5 restraint /
TC6 plan capability shaping)。population 来源:D41 §四
验收清单枚举 + 系统 prompt 契约 E#2(TC5)。后续扩量走 D41.6 飞轮
(dogfood / SWE-bench records 归因失败沉 case),不凭想象批量编题。

**3. Judgment spec**:全部确定性 scorer,零 LLM-judge(oracle 硬度 D41.4:
本面最硬 oracle 是 `=` 判):
- `tool_selection`(binary)— 首个 tool_use 的 name == expected_tool;
  expected_tool 为 null 时断言零 tool call
- `forbidden_avoidance`(binary)— 任何 tool_use 命中 forbidden_tools 即败
  (空列表 vacuous pass)
- `input_construction`(binary)— expected_input_contains 每字段 any-of
  子串(大小写不敏感)命中首个匹配 tool call 的对应 input 字段

**4. Reference policy**:参照模型 **qwen-max**。pass bar 待 N≥4 稳定性
画像后 ratify(本 card 更新时补);弱模型(qwen-plus 等)上的红 = 信息,
不是 gate 红(design-for-strong-model,D41.5)。

## Capability coverage

| Capability | Cases | 测什么 |
|---|---|---|
| TC1 selection | TC1-read-config · TC1-bash-run-tests | 任务 → 正确工具 |
| TC2 discrimination | TC2-grep-not-bash · TC2-edit-not-write | 近义工具辨析(含破坏性误选 Write) |
| TC3 param | TC3-grep-scoped-path · TC3-write-new-file · TC3-project-test-command | 用户或项目指令约束 → input 字段构造 |
| TC4 self-correction | TC4-unknown-tool-recovery | 种植 "tool not found" 错误后,第二格换真实工具、不重放 |
| TC5 restraint | TC5-greeting-no-tool | 寒暄零工具(prompt 契约 E#2) |
| TC6 plan shaping | TC6-plan-read · TC6-plan-mutation-restraint | plan 下保留只读探索，同时避免 mutation/delegation |

## Stability profile

### Day 1 — qwen-max × N=4(2026-07-08,live 跑次 1-4,原始输出在 `results/qwen-max-run{1..4}.txt`)

| Case | 稳定率(3 维 × 4 跑) | 备注 |
|---|---|---|
| TC1-read-config | **12/12 (100%)** | |
| TC1-bash-run-tests | 8/12 → 修正后 12/12 | input 断言 4/4 **确定性**失败于 `make test`(非噪声,case 缺陷,见下) |
| TC2-grep-not-bash | **12/12 (100%)** | |
| TC2-edit-not-write | **12/12 (100%)** | |
| TC3-grep-scoped-path | **12/12 (100%)** | |
| TC3-write-new-file | **12/12 (100%)** | |
| TC4-unknown-tool-recovery | **12/12 (100%)** | 报错后第二格 4/4 换 Grep,零原样重放 |
| TC5-greeting-no-tool | **12/12 (100%)** | 4/4 零工具 |

**Day 1 finding(case 缺陷,已修)**:TC1-bash-run-tests 原带
`command contains "pytest"` 断言——但合成环境无项目上下文,测试命令对模型
不可知,断言测的是"猜测试栈"不是"按指令构造参数"(focus_state substring
brittleness 的同款教训)。四次失败一字不差(`make test`)证明是确定性
过度指定,非采样噪声。处置:撤该 case 的 input 期望(它的 capability 是
TC1 选择,tool_selection 维度已覆盖),记入 Known gaps。

### Project-instruction re-ratification (2026-08-06)

新增 `TC3-project-test-command`:system prompt 注入合成 `AGENTS.md` 指令，明确
全量测试命令；用户只要求“跑全量测试”。参考模型 qwen-max live record 得到
`Bash(command='uv run pytest -m "not integration" -q')`，三个确定性 scorer
全部通过。全数据集 **9/9 all-dims-pass**，新 cassette 已录制并可 replay。

### Plan capability-shaping re-ratification (2026-08-08)

G2 将 plan mode 从 legacy permission overlay 改为 capability-shaped catalog：
模型只收到 Read/Grep，dispatch 另有 deny-only forged-call guard。新增两个 TC6
case。初始 mutation-restraint 候选错误地要求零工具；qwen-max 原样复跑两次均
选择 `Read(README.md)`，暴露该 oracle 与“plan 允许只读探索”的 capability
契约冲突。修正为期望读取目标文件、继续严格禁止 Write/Edit/Bash/Agent 后，
qwen-max live record **2/2 all-dims-pass**。全数据集现为 **11/11
all-dims-pass**，两个真实 cassette 已录制并可 replay。

### Pass bar(ratify 2026-08-06,依 D41.5)

- **Gate:参照模型 qwen-max 上 `cases all-dims-pass = 11/11`**。
  依据:修正后全部 case 在 N=4 上零方差,bar 设满格有画像支撑。
- **红灯处置纪律**:出现红先原样重跑 1 次——复现才算回归,单次红视为
  罕见采样噪声记录在案(防"狼来了的门",不弱化断言)。
- 非参照模型上的任何结果 = 信息,不触发 gate(D41.5)。

## Known gaps(forward)

- SpawnAgent 的委派判断(何时该派子 agent)未覆盖——它与 #6 面交界,
  等 #6 触发条件(D41 P3)
- 多工具并发选择(一条消息合法地需要两个 tool call)未覆盖
- 英文 user 消息形态未覆盖(当前全中文;等飞轮带来真实英文失败样本)
