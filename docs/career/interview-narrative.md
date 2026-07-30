# OpenHarness 面试叙事

> 目标：把项目讲成一组清晰的工程判断，而不是功能报菜名。
> 所有数字和 claim 以 `project-evidence.md` 为准。

## 30 秒版本

我从零实现了一个 Python coding-agent harness。模型负责提出 tool calls，
harness 负责权限、执行隔离、长上下文状态、恢复和完成判定。项目有 2,783 项
测试、95.29% coverage，并用公开 CLI 跑完了 SWE-bench Lite，qwen3.7-max
最终 170/300 resolved。这个过程让我最关注的不是模型能不能写代码，而是
怎样让系统在模型可能自信犯错的前提下仍然可控、可恢复、可验证。

## 2 分钟版本

OpenHarness 的起点是一个最小 tool loop：模型流式输出文字或 tool call，
系统检查权限、执行工具、把结果喂回模型，直到 end turn。

真正把它放进代码库后，我遇到的是一条控制问题链：

1. 模型能调用工具，但不能默认信任，所以需要权限闸和不可逆操作红线。
2. 任意 Bash 的 side effect 无法由 path metadata 完整描述，所以授权和 sandbox
   必须分层。
3. Session 会超过 context window，所以需要单条结果截断、reactive recovery、
   compaction、snapshot 和 resume。
4. 模型说“完成”不代表真的正确，所以完成权必须交给外部 oracle。
5. 自治 loop 必须有清晰的 context semantics、fail-closed 行为和 iteration cap。

最后我做出了三种循环：交互式 `/goal` 延续同一 conversation；headless repair
loop 每轮 fresh context；autopilot 从持久队列顺序领取任务。它们共用 engine，
但故意不合并，因为 continuation、retry 和 scheduling 是三种不同语义。

我还用 shipped CLI 跑了 300 题 SWE-bench Lite。战役不仅给出 56.7% resolved，
还冲出了五个 harness 缺口，包括 config drift、stream retry 与 provider 参数
透传。最重要的数据是：正确和错误 patch 的 turn 数分布很接近，所以“模型工作了
很久、说自己完成了”不是 completion evidence。这直接支撑了 external gate 的设计。

## 10 分钟展开顺序

### 1. 先画责任边界

```text
User / script / queue
          |
          v
REPL or headless controller
          |
          v
agent engine <------> working model
     |
     +--> permission + hooks --> host / Docker / gVisor
     |
     +--> compaction + snapshot + memory
     |
     +--> external gate --> pass / repair feedback
```

一句话：model orchestrates actions，harness governs consequences。

### 2. 讲三种 loop 为什么不能混

| Loop | Context | Gate | 关键语义 |
|---|---|---|---|
| `/goal` | 同一 conversation 延续 | 独立语义判官 | 保留 working context |
| Headless repair | 每次 fresh context | command 或 semantic gate | 用结构化失败反馈重试 |
| Autopilot | 持久化 cards | 当前要求 command gate | 负责 intake 与 scheduling |

面试重点不是“我有三个 loop”，而是：

> Context continuation、attempt retry 和 job scheduling 的状态归属不同。
> 强行统一只会让 resume、权限和停止语义变得含混。

### 3. 讲一个安全故事

选择 F9：interactive Bash 绕过 path-based permission。

### 4. 讲一个状态故事

选择 F17：goal terminal sentinel 晚于 snapshot，resume 复活已完成 goal。

### 5. 讲一个 evidence 故事

选择 F6：只保留 Bash output head，丢掉 pytest summary，模型随后编造并自我强化。

### 6. 用 SWE-bench 收尾

展示不是只会写 unit tests，而是能运行长战役、控制实验条件、分类失败，并把
benchmark feedback 反哺 production path。

## `/goal` 精确讲法

### 调用链

1. `parse_goal_command()` 把 `/goal <condition>` 解析为 set/show/clear。
2. Set 时创建 `GoalState`，把 `[goal-status] set` sentinel 放进 history，并排入
   kickoff message，所以 goal 一设定就开工。
3. Working model 按普通 agent loop 执行一轮，工具照常经过 permission checker。
4. 回到 default-state REPL 后，`render_history_transcript(history)` 把累计
   conversation 变成 judge 可读 transcript。
5. `run_semantic_verification()` 发起一次新的 model request：
   - 只有 condition 与 transcript；
   - transcript 被包在 untrusted delimiters 中；
   - system prompt 明确禁止服从 transcript 内指令；
   - `tools_disabled=True`；
   - 输出 contract 是单行 `{"reason": "...", "score": 0|1}`。
6. 任何 exception、empty response、malformed JSON、non-dict 或非法 score 都返回
   `passed=False`。
7. Pass 时写入 `met` sentinel、同步补写 snapshot、清除 active goal。
8. Fail 时把 reason 包装成 `[goal checker] not met:`，作为下一 turn 的输入，
   继续同一 conversation。

### “独立 LLM”独立在哪里

准确答案：

- 它是独立 API request；
- 使用独立 context，不继承 working model 的 system prompt 或 tool catalog；
- tools 被禁用；
- judge 只看到 condition 与显式构造的 transcript；
- judge model 可通过 `OPENHARNESS_GOAL_JUDGE_MODEL` 单独配置。

默认情况下，它可以仍使用 working model 同款 model 与同一个 API client。
所以“独立”指 role/context/call path 分离，不代表默认使用不同 provider。

### 为什么 judge 看 transcript，不直接看仓库

当前设计让 judge tool-disabled，避免 checker 自己改变环境，也让判定路径更容易
审计。代价是 working model 必须把验证证据显式带进 conversation。

这也是为什么 goal prompt 会要求“surface verifiable evidence”。如果需要更强
保证，优先使用 headless `--verify` 直接执行 command oracle，而不是让 semantic
judge 推断仓库状态。

### Prompt injection 怎么处理

Transcript 可能包含模型从网页、文件或工具输出中读到的恶意文字，因此：

1. system prompt 明确把 transcript 定义为 untrusted data；
2. 使用 begin/end delimiters；
3. judge 没有 tools；
4. parse contract 严格；
5. 失败一律 not-met。

这降低风险，但不能把 probabilistic judge 说成形式化安全。能用 executable
oracle 时仍应使用 `--verify`。

## 五个代表性故事

### 故事 A：F9，授权与隔离不能混为一谈

**Situation：** Path-based permission 对 Read/Write/Edit 有效，但 interactive
Bash 默认放行。Dogfood 中模型通过 Bash 写 `/tmp`、安装 package，绕过了文件
工具的规则。

**判断：** 不是再加一条 glob。任意 subprocess 的 side effects 不可能由 harness
静态穷举。

**Action：** 把 interactive、mutating、pathless tool 默认改为 ASK；保留
headless fail-closed、灾难操作 deny 与显式 allow rule；把真正 containment 放到
Docker/gVisor。

**Result：** 四象限行为进入 regression tests；形成 authorization vs containment
的明确架构边界。

### 故事 B：F17，模块都绿不代表 seam 正确

**Situation：** Goal judge 判定 met 后，history 中的 terminal sentinel 写入时间
晚于 engine snapshot。用户在下一 turn 前退出，resume 会恢复旧 snapshot，重新
激活已完成 goal。

**判断：** Goal parser 和 snapshot service 各自测试都绿，缺陷在状态转换顺序。

**Action：** 把 extinguish goal 做成“append terminal sentinel + 同步补写 snapshot”
的一次状态操作；增加跨 memory/disk seam tests。

**Result：** met、clear、resume 三条路径不再复活 goal；也形成面试中可讲的
state ownership 案例。

### 故事 C：F6，harness 会制造 hallucination

**Situation：** Bash preview 只保留 output head，长 pytest 输出的 summary 在尾部
被截掉。模型随后编造测试数量，并在下一轮引用自己的错误总结。

**判断：** 不是简单归咎“模型会 hallucinate”；harness 删除了唯一可靠证据。

**Action：** 改为 head+tail truncation，在有限 token budget 内同时保留命令开头
与最终状态。

**Result：** 后续 dogfood 中模型能直接引用真实 summary；evidence visibility
成为 compaction 设计的一等约束。

### 故事 D：Provider drift，实验条件必须钉死

**Situation：** SWE-bench 长跑中 provider 把 qwen3.7-max 默认切到 thinking mode。
同一 probe 从 1.4 秒、22 tokens 变成 48 秒、2,559 tokens，大量实例 timeout。

**判断：** 起初看起来像模型不收敛；A/B probe 证明是 provider default drift。

**Action：** 增加 generic `OPENHARNESS_EXTRA_BODY` passthrough，并让 bench child
process 显式继承钉死的 config；不增加 Qwen-specific branch。

**Result：** 批次恢复；一个此前连续 timeout 的 instance 在 thinking off 后
193 秒/28 turns 完成，纠正了错误归因。

### 故事 E：官方评测坏了，不等于没有 ground truth

**Situation：** 官方 hosted SWE-bench evaluator 对所有 submission 报 failed；
连 gold patch 也失败。

**判断：** Gold-probe 把 patch 质量与 evaluator 故障分开。

**Action：** 在阿里云 ECS 自建官方 harness，处理 Docker data-root、磁盘、swap、
并发和逐题 report 聚合。

**Result：** 得到最终 170/300 resolved；同时保存完整 predictions、records 与
failure taxonomy。

## 高频问题

### 为什么不直接用 Claude Code/Codex？

因为项目目标不是替代日常工具，而是理解并验证强模型周围的系统边界。直接使用
成熟产品看不到 permission precedence、snapshot ordering、judge failure semantics
或 benchmark attribution。重建后，我能针对这些边界做实验和 regression test。

### 这和普通 agent framework 有什么不同？

核心不是 graph 编排，而是 coding-agent control plane：真实文件和 subprocess、
交互审批、long-session state、fail-closed headless execution、external completion
gate、可恢复 repair loop 与 benchmark attribution。

### 为什么 working model 不能自己判断完成？

SWE-bench 中 resolved 与 unresolved-completed 的 turn 分布相似；错误 patch 同样
可能经历完整分析并给出自信总结。Self-report 是行为信号，不是 correctness oracle。

### LLM judge 也会错，为什么还用？

它只用于无法写成 exit code 的 soft condition，并且与 working context 分离、
tool-disabled、严格解析、fail closed、有 cap。能写 command oracle 时优先用
`--verify`。设计不是宣称 judge 可靠，而是给 probabilistic criterion 一个受控位置。

### 用 coding agents 写了这么多，哪些是你的工作？

我负责问题定义、boundary、anti-scope、acceptance criteria、失败归因和是否接受
实现；agent 负责在 contract 内探索、编码和跑验证。最能证明 ownership 的不是
commit author，而是 append-only decisions、被推翻的假设、dogfood findings 和
benchmark 条件控制。

### 你会如何把它推向真实生产？

优先级：

1. 拆分大型 `cli.py`，把 controller/state machine 从 Typer surface 中抽离；
2. 增加 native Anthropic Messages adapter 与 provider contract tests；
3. 在 Linux CI 中常态运行 Docker sandbox integration；
4. 把 secrets、network egress、resource quota 纳入更严格 policy；
5. 为 judge 建持续 calibration set，并提供 deterministic evidence attachment；
6. 将 autopilot 的 queue/store/worker protocol 化，再考虑多 worker。

### 最大的技术债是什么？

`cli.py` 仍是过大的 composition root；部分 experimental eval code 不在稳定核心
coverage 口径内；native provider 与 sandbox integration matrix 还不够宽。这些是
明确边界，不应被 README 的“production bar”措辞掩盖。

## Demo 路径

面试现场只演示一条闭环，不要展示所有命令：

1. `uv run oh`
2. `/plan` 要求检查一个小变更并给验证计划
3. 展示 `Bash/Edit/Write` 被 plan permission clamp
4. 选择 approve，强调“只退出 plan，不自动执行”
5. `/goal <目标 + verify + turn cap>`
6. 展示 working turn、独立 checker feedback、继续或 met
7. 最后打开对应 test 与 semantic gate 源码

备用离线 demo：

```bash
uv run pytest tests/cli/test_chat_plan_mode.py tests/cli/test_chat_goal.py -q --no-cov
```

不要把 live model 网络、provider 余额或 sandbox daemon 作为唯一 demo 路径。
